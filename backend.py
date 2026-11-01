"""AITokensHub backend - OpenAI-compatible API with credit system.
Forwards to OpenRouter free models. Deploy on Render."""
import os, time, json
from datetime import datetime
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()
import common as C

app = Flask(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model fallback chain: router first, then specific free models
MODELS = [
    "openrouter/free",
    "thinking-machines/inkling:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
]

START_TIME = datetime.now()


def _uptime():
    d = datetime.now() - START_TIME
    h, r = divmod(int(d.total_seconds()), 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


def _get_key():
    """Extract API key from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


def _call_openrouter(messages, model=None, max_tokens=1000):
    """Call OpenRouter with fallback chain. Returns (text, model_used, error)."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://aitokenshub.local",
        "X-Title": "AITokensHub",
    }
    tried = [model] if model else []
    tried += [m for m in MODELS if m not in tried]
    last_err = ""
    for m in tried:
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json={
                "model": m,
                "messages": messages,
                "max_tokens": max_tokens,
            }, timeout=60)
            if r.status_code == 200:
                d = r.json()
                text = d["choices"][0]["message"]["content"]
                return text, m, ""
            last_err = f"{m}: HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{m}: {type(e).__name__}"
    return "", "", last_err or "all models failed"


@app.route("/")
def index():
    return jsonify({
        "service": "AITokensHub",
        "uptime": _uptime(),
        "endpoints": ["/v1/chat/completions", "/v1/models"],
    })


@app.route("/v1/models")
def list_models():
    return jsonify({
        "object": "list",
        "data": [{"id": m, "object": "model"} for m in MODELS],
    })


@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def chat_completions():
    if request.method == "OPTIONS":
        return "", 204
    if not OPENROUTER_KEY:
        return jsonify({"error": "server misconfigured"}), 500

    api_key = _get_key()
    keyinfo = C.get_key_owner(api_key)
    if not keyinfo:
        return jsonify({"error": "invalid API key"}), 401

    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "messages required"}), 400
    max_tokens = min(int(data.get("max_tokens", 1000)), 4000)
    req_model = data.get("model", "")

    # Estimate input cost upfront
    input_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
    est_in = C.estimate_tokens(input_text)
    est_total = est_in + max_tokens

    user = C.get_user(keyinfo["tg_id"])
    if not user or user["credits"] < est_total:
        return jsonify({"error": "insufficient credits", "credits": user["credits"] if user else 0}), 402

    text, model_used, err = _call_openrouter(messages, req_model or None, max_tokens)
    if err:
        return jsonify({"error": f"upstream failed: {err}"}), 502

    # Deduct actual usage
    actual = C.estimate_tokens(input_text) + C.estimate_tokens(text)
    C.deduct_credits(keyinfo["tg_id"], actual)
    C.touch_key(api_key, actual)
    user = C.get_user(keyinfo["tg_id"])

    return jsonify({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_used,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": C.estimate_tokens(input_text),
            "completion_tokens": C.estimate_tokens(text),
            "total_tokens": actual,
        },
        "credits_remaining": user["credits"],
    })


@app.route("/v1/credits")
def credits():
    api_key = _get_key()
    keyinfo = C.get_key_owner(api_key)
    if not keyinfo:
        return jsonify({"error": "invalid API key"}), 401
    user = C.get_user(keyinfo["tg_id"])
    return jsonify({
        "credits": user["credits"],
        "total_earned": user["total_earned"],
        "total_used": user["total_used"],
    })


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5002))
    app.run(host="0.0.0.0", port=port)
