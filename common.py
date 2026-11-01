"""AITokensHub - shared logic: users, credits, referrals, API keys."""
import os, json, secrets, time
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
KEYS_FILE = os.path.join(DATA_DIR, "api_keys.json")

# Config
STARTER_CREDITS = 100_000      # new user gets 100K tokens free
REFERRAL_REWARD = 1_000_000    # 1M tokens per successful referral
MAX_REFS_PER_DAY = 20          # anti-abuse: max referrals counted per day


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ---------- users ----------
def get_user(tg_id):
    users = load_json(USERS_FILE, {})
    return users.get(str(tg_id))


def register_user(tg_id, name="", referrer_id=None):
    """Register a new user. Returns (user, is_new, referral_credited)."""
    users = load_json(USERS_FILE, {})
    tid = str(tg_id)
    if tid in users:
        return users[tid], False, False
    user = {
        "tg_id": tid,
        "name": name or "",
        "credits": STARTER_CREDITS,
        "total_earned": STARTER_CREDITS,
        "total_used": 0,
        "referrals": 0,
        "referred_by": str(referrer_id) if referrer_id else "",
        "created_at": datetime.now().isoformat(),
        "refs_today": 0,
        "refs_day": datetime.now().strftime("%Y-%m-%d"),
    }
    users[tid] = user
    referral_credited = False
    # Credit the referrer
    if referrer_id and str(referrer_id) != tid:
        ref = users.get(str(referrer_id))
        if ref:
            today = datetime.now().strftime("%Y-%m-%d")
            if ref.get("refs_day") != today:
                ref["refs_day"] = today
                ref["refs_today"] = 0
            if ref.get("refs_today", 0) < MAX_REFS_PER_DAY:
                ref["credits"] += REFERRAL_REWARD
                ref["total_earned"] += REFERRAL_REWARD
                ref["referrals"] += 1
                ref["refs_today"] += 1
                referral_credited = True
    save_json(USERS_FILE, users)
    return users[tid], True, referral_credited


def add_credits(tg_id, amount):
    users = load_json(USERS_FILE, {})
    u = users.get(str(tg_id))
    if not u:
        return False
    u["credits"] += amount
    u["total_earned"] += amount
    save_json(USERS_FILE, users)
    return True


def deduct_credits(tg_id, amount):
    """Deduct credits. Returns True if sufficient balance."""
    users = load_json(USERS_FILE, {})
    u = users.get(str(tg_id))
    if not u or u["credits"] < amount:
        return False
    u["credits"] -= amount
    u["total_used"] += amount
    save_json(USERS_FILE, users)
    return True


# ---------- API keys ----------
def create_api_key(tg_id):
    keys = load_json(KEYS_FILE, {})
    key = "ath_" + secrets.token_urlsafe(24)
    keys[key] = {
        "tg_id": str(tg_id),
        "created_at": datetime.now().isoformat(),
        "last_used": "",
        "total_tokens": 0,
    }
    save_json(KEYS_FILE, keys)
    return key


def get_key_owner(api_key):
    keys = load_json(KEYS_FILE, {})
    return keys.get(api_key)


def touch_key(api_key, tokens_used=0):
    keys = load_json(KEYS_FILE, {})
    k = keys.get(api_key)
    if k:
        k["last_used"] = datetime.now().isoformat()
        k["total_tokens"] += tokens_used
        save_json(KEYS_FILE, keys)


def revoke_api_key(api_key, tg_id):
    keys = load_json(KEYS_FILE, {})
    k = keys.get(api_key)
    if k and k["tg_id"] == str(tg_id):
        del keys[api_key]
        save_json(KEYS_FILE, keys)
        return True
    return False


def list_user_keys(tg_id):
    keys = load_json(KEYS_FILE, {})
    return {k: v for k, v in keys.items() if v["tg_id"] == str(tg_id)}


# ---------- token estimation ----------
def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)
