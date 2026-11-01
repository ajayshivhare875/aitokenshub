"""AITokensHub - shared logic: users, credits, referrals, API keys."""
import os, json, secrets, sys, time
from datetime import datetime

import db_store  # Supabase/Postgres backend; falls back to files when DATABASE_URL is unset

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
KEYS_FILE = os.path.join(DATA_DIR, "api_keys.json")

# Config
STARTER_CREDITS = 100_000      # new user gets 100K tokens free
REFERRAL_REWARD = 1_000_000    # 1M tokens per successful referral
MAX_REFS_PER_DAY = 20          # anti-abuse: max referrals counted per day


def _doc_key(path):
    return os.path.basename(path)


# Docs whose last load hit a DB error. While a doc is tainted, save_json
# REFUSES to write it to Supabase: our in-memory copy came from a failed
# read, and writing it would permanently wipe the real data. A later
# successful load_json clears the taint.
_tainted_docs = set()


def load_json(path, default):
    doc = _doc_key(path)
    if db_store.db_enabled():
        val = db_store.db_load(doc, db_store._MISSING)
        if val is db_store._DB_ERROR:
            # DB unreachable: this is NOT "no data". Serve the local file
            # (or defaults) so the bot keeps responding, but taint the doc
            # so no save can overwrite Supabase from this failed read.
            print(f"[storage] DB read failed for {doc!r}: using local copy, "
                  f"Supabase writes paused for this doc", file=sys.stderr, flush=True)
            _tainted_docs.add(doc)
        else:
            _tainted_docs.discard(doc)
            if val is not db_store._MISSING:
                return val
            # One-time seed: a local file left behind seeds the DB.
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        file_data = json.load(f)
                    db_store.db_save(doc, file_data)
                    return file_data
                except Exception:
                    pass
            return default
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    """Save a doc. A doc tainted by a failed read is NEVER written to
    Supabase: the in-memory copy may be empty defaults, and persisting it
    would wipe the real data (this is how deploys used to lose data)."""
    doc = _doc_key(path)
    if db_store.db_enabled():
        if doc in _tainted_docs:
            print(f"[storage] REFUSING Supabase write for {doc!r}: last read failed; "
                  f"kept local file copy only", file=sys.stderr, flush=True)
        else:
            db_store.db_save(doc, data)
    # Always keep the local file copy too, so nothing is ever lost.
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
