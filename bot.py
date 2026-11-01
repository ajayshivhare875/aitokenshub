"""AITokensHub Telegram bot - referrals, credits, API keys."""
import os, asyncio
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import common as C

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

if not BOT_TOKEN or not ADMIN_ID:
    raise SystemExit("BOT_TOKEN and ADMIN_ID required in .env")


def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Balance", callback_data="bal"),
         InlineKeyboardButton("🔑 My API Key", callback_data="key")],
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="ref"),
         InlineKeyboardButton("❓ Help", callback_data="help")],
    ])


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
            if ref == u.id:
                ref = None
        except Exception:
            ref = None
    user, is_new, ref_credited = C.register_user(u.id, u.first_name or "", ref)
    if is_new:
        msg = (f"🎉 Welcome to AITokensHub!\n\n"
               f"💰 You got {fmt(C.STARTER_CREDITS)} FREE tokens!\n\n"
               f"🤖 Chat with AI, or use our API in your own apps.\n"
               f"👥 Refer friends → earn {fmt(C.REFERRAL_REWARD)} tokens per referral!")
        if ref_credited:
            # notify referrer
            try:
                await context.bot.send_message(
                    chat_id=ref,
                    text=f"🎉 New referral!\n+{fmt(C.REFERRAL_REWARD)} tokens added!\nBalance: {fmt(C.get_user(ref)['credits'])}")
            except Exception:
                pass
    else:
        msg = (f"👋 Welcome back!\n💰 Balance: {fmt(user['credits'])} tokens")
    await update.message.reply_text(msg, reply_markup=main_kb())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    user = C.get_user(u.id)
    if not user:
        await q.edit_message_text("Send /start first!")
        return
    d = q.data
    if d == "bal":
        await q.edit_message_text(
            f"💰 Balance: {fmt(user['credits'])} tokens\n"
            f"📥 Total earned: {fmt(user['total_earned'])}\n"
            f"📤 Total used: {fmt(user['total_used'])}\n"
            f"👥 Referrals: {user['referrals']}",
            reply_markup=main_kb())
    elif d == "key":
        keys = C.list_user_keys(u.id)
        if keys:
            k = list(keys.keys())[0]
            txt = f"🔑 Your API key:\n`{k}`\n\n🌐 Endpoint:\n`{PUBLIC_URL or 'https://YOUR-URL'}/v1`\n\n_Keep it secret!_"
        else:
            k = C.create_api_key(u.id)
            txt = (f"🔑 New API key created!\n`{k}`\n\n"
                   f"🌐 Endpoint:\n`{PUBLIC_URL or 'https://YOUR-URL'}/v1`\n\n"
                   f"Use with any OpenAI-compatible library!\n_Keep it secret!_")
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_kb())
    elif d == "ref":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={u.id}"
        await q.edit_message_text(
            f"👥 Refer & Earn!\n\n🔗 Your link:\n{link}\n\n"
            f"💰 You earn {fmt(C.REFERRAL_REWARD)} tokens per referral!\n"
            f"🎁 They get {fmt(C.STARTER_CREDITS)} free to start!\n\n"
            f"Your referrals: {user['referrals']}",
            reply_markup=main_kb())
    elif d == "help":
        await q.edit_message_text(
            "❓ How it works:\n\n"
            "1️⃣ Chat here directly, or\n"
            "2️⃣ Get your API key → use in your apps\n"
            "3️⃣ Refer friends → earn tokens\n\n"
            "Every message costs tokens based on length.",
            reply_markup=main_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat directly with AI (costs credits)."""
    u = update.effective_user
    user = C.get_user(u.id)
    if not user:
        await update.message.reply_text("Send /start first!")
        return
    text = update.message.text.strip()
    if not text:
        return
    est = C.estimate_tokens(text) + 500
    if user["credits"] < est:
        await update.message.reply_text(
            f"⚠️ Not enough tokens! Balance: {fmt(user['credits'])}\n"
            f"Refer friends to earn more! 👥")
        return
    await context.bot.send_chat_action(chat_id=u.id, action="typing")
    # call backend locally
    import requests as rq
    try:
        keys = C.list_user_keys(u.id)
        key = list(keys.keys())[0] if keys else C.create_api_key(u.id)
        base = PUBLIC_URL or f"http://localhost:{os.getenv('PORT', 5002)}"
        r = rq.post(f"{base}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"messages": [{"role": "user", "content": text}],
                          "max_tokens": 500},
                    timeout=90)
        d = r.json()
        if "choices" in d:
            reply = d["choices"][0]["message"]["content"]
            left = d.get("credits_remaining", "?")
            await update.message.reply_text(reply + f"\n\n_💰 {fmt(left)} left_" if isinstance(left, int) else reply,
                                            parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ Error: {d.get('error', 'unknown')}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed: {type(e).__name__}")


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = C.get_user(u.id)
    if user:
        await update.message.reply_text(f"💰 Balance: {fmt(user['credits'])} tokens")
    else:
        await update.message.reply_text("Send /start first!")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("AI Tokens Hub bot running...", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
