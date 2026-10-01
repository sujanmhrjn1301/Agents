"""
inbox.py — Auto-Inbox Agent: Instagram DM Auto-Replier via Meta Webhooks.

Runs a FastAPI server that:
  1. Verifies Meta Webhook subscriptions (GET /webhook)
  2. Receives incoming Instagram DMs in real-time (POST /webhook)
  3. Generates intelligent replies using OpenRouter (GPT-4o-mini)
  4. Sends replies back via the Meta Graph API

Usage:
    python -m instagram_reel_bot               # runs BOTH reel bot & inbox agent
    python -m instagram_reel_bot --inbox-only  # run only the inbox agent
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import PlainTextResponse

from dotenv import load_dotenv

# ── Load .env from the project root ─────────────────────────────────────
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# ── Lazy logger (shares the reel_bot logger) ─────────────────────────────
import logging

log = logging.getLogger("reel_bot.inbox")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    log.addHandler(_handler)
    log.setLevel(logging.DEBUG)

# ── Environment Variables ────────────────────────────────────────────────

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "autoreel_verify_2026")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
INBOX_PORT = int(os.getenv("INBOX_PORT", "8000"))

# ── Graph API base URL ──────────────────────────────────────────────────

def _get_graph_base() -> str:
    """Use graph.facebook.com for long-lived business tokens."""
    if INSTAGRAM_ACCESS_TOKEN.startswith("EAA"):
        return "https://graph.facebook.com/v19.0"
    return "https://graph.instagram.com/v19.0"

GRAPH_BASE = _get_graph_base()

# ── FastAPI App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="AutoReel Inbox Agent",
    description="Instagram DM auto-replier powered by OpenRouter LLM",
    version="1.0.0",
)


@app.on_event("startup")
async def _startup():
    log.info("=" * 60)
    log.info("📬 AutoReel Inbox Agent — ONLINE")
    log.info("   Webhook verify token: %s", WEBHOOK_VERIFY_TOKEN[:8] + "…")
    log.info("   Instagram Account ID: %s", INSTAGRAM_ACCOUNT_ID)
    log.info("   Graph API base: %s", GRAPH_BASE)
    log.info("   LLM model: %s", OPENROUTER_MODEL)
    log.info("=" * 60)


# ── GET /webhook — Meta Verification ────────────────────────────────────

@app.get("/webhook")
async def webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta sends a GET request during webhook setup.
    We must validate the mode + verify_token and echo back hub.challenge.
    """
    log.info("🔑 Webhook verification request — mode=%s", hub_mode)

    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        log.info("✅ Webhook verified successfully!")
        return PlainTextResponse(content=hub_challenge, status_code=200)

    log.warning("❌ Webhook verification FAILED — token mismatch")
    return PlainTextResponse(content="Forbidden", status_code=403)


# ── POST /webhook — Incoming Messages ───────────────────────────────────

@app.post("/webhook")
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming Instagram DM events and mentions from Meta.
    Immediately returns 200 OK, then processes the message in background.
    Handles:
      - Direct Messages  → entry[].messaging[]
      - Group Mentions   → entry[].changes[] where field='mentions'
    """
    body = await request.json()
    # Always log at INFO so we see every incoming event
    log.info("📩 Raw webhook payload: %s", body)

    obj = body.get("object", "")
    # Meta sends 'instagram' OR 'page' depending on account/app setup
    if obj not in ("instagram", "page"):
        log.warning("⚠️  Ignoring unknown webhook object type: %s", obj)
        return {"status": "ignored"}

    entries = body.get("entry", [])
    for entry in entries:

        # ── Path 1: Direct Messages (1:1 DMs) ───────────────────────────
        messaging_list = entry.get("messaging", [])
        for messaging in messaging_list:
            sender = messaging.get("sender", {})
            sender_id = sender.get("id", "")
            message = messaging.get("message", {})
            message_text = message.get("text", "")

            # Ignore echo (our own outbound messages)
            if sender_id == INSTAGRAM_ACCOUNT_ID:
                log.debug("Ignoring self-sent message echo from %s", sender_id)
                continue

            # Ignore non-text messages (images, stickers, reactions, etc.)
            if not message_text:
                log.info("📎 Non-text message from %s — skipping auto-reply", sender_id)
                continue

            log.info(
                "💬 DM from %s: %s",
                sender_id,
                message_text[:120] + ("…" if len(message_text) > 120 else ""),
            )
            background_tasks.add_task(_handle_dm, sender_id, message_text)

        # ── Path 2: Mentions in comments / group threads ─────────────────
        changes = entry.get("changes", [])
        for change in changes:
            field = change.get("field", "")
            value = change.get("value", {})

            if field == "mentions":
                # Someone tagged @account in a comment or group thread
                media_id = value.get("media_id", "")
                comment_id = value.get("comment_id", "")
                mentioned_in = "comment" if comment_id else "media/story"
                log.info("🏷️  Mention received — %s (media_id=%s, comment_id=%s)",
                         mentioned_in, media_id, comment_id)
                # For mentions we don't auto-DM (no sender PSID available here)
                # — log it so owner knows; expand later if needed
                continue

            if field in ("messages", "message_reactions"):
                # Some accounts deliver DMs under changes[].field='messages'
                sender_id = value.get("sender", {}).get("id", "")
                message_text = value.get("message", {}).get("text", "")

                if not sender_id or sender_id == INSTAGRAM_ACCOUNT_ID:
                    continue
                if not message_text:
                    log.info("📎 Non-text change-message from %s — skipping", sender_id)
                    continue

                log.info(
                    "💬 DM (via changes) from %s: %s",
                    sender_id,
                    message_text[:120] + ("…" if len(message_text) > 120 else ""),
                )
                background_tasks.add_task(_handle_dm, sender_id, message_text)

    return {"status": "ok"}


# ── Background DM Handler ───────────────────────────────────────────────

async def _handle_dm(sender_id: str, user_message: str) -> None:
    """Generate an LLM reply and send it back to the user via Graph API."""
    try:
        reply_text = await generate_dm_reply(user_message)
        log.info("🤖 Generated reply for %s: %s", sender_id, reply_text[:100])

        await send_reply(sender_id, reply_text)
        log.info("✅ Reply sent to %s", sender_id)

    except Exception as exc:
        log.error("❌ Failed to handle DM from %s: %s", sender_id, exc, exc_info=True)


# ── LLM Reply Generation (OpenRouter) ───────────────────────────────────

# Model dedicated ONLY for DM auto-replies — fast, cheap, conversational
_DM_REPLY_MODEL = "google/gemini-2.5-flash-lite"

DM_SYSTEM_PROMPT = """\
You are the Instagram assistant for this account. You reply to DMs on behalf of the account owner.

Your personality and communication style:
- You write in a relaxed Nepali-English (Nenglish) style — the way close friends talk on Instagram, WhatsApp, or social media in Nepal.
  Examples of this style:
    • "ayo bro, kasto thyo?"
    • "haha sahi cha yaar!"
    • "arre serious?? thats wild bro"
    • "k cha timro side ma?"
    • "lol sochna paryo 😂"
    • "haina ra?? ekdum sahi bho"
- Mix Nepali casual words naturally into English sentences (bro, yaar, ayo, sahi, thikai, ramro, haha, lol).
- Read the sender's tone and emotion first. Mirror it exactly:
  * They're excited → match their energy, use !!, emoji
  * They're sad/venting → warm and empathetic, no jokes
  * They're confused/asking → helpful and clear
  * They're flirty/funny → light banter, keep it chill
  * They're formal → slightly professional but still friendly
- NEVER reveal you are an AI. You are the account owner's assistant.
- NEVER use hollow phrases like "I'm just an AI" or "As an AI assistant".
- Keep replies SHORT — max 1–2 sentences. Complete thoughts only, never cut off mid-sentence.
- Do NOT add hashtags, links, or emojis unless the situation naturally calls for it.
- If someone asks about collabs, videos, or stories — be warm and invite them to share more.
"""


async def generate_dm_reply(user_message: str) -> str:
    """Send the user's DM to OpenRouter and return a concise, tone-matched Nenglish reply."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://instagram-reel-bot.local",
        "X-Title": "AutoReel Inbox Agent",
    }
    payload = {
        "model": _DM_REPLY_MODEL,
        "messages": [
            {"role": "system", "content": DM_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.85,
        # 80 tokens is enough for 1-2 short sentences; model is instructed to complete thoughts
        "max_tokens": 80,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    choice = data["choices"][0]
    reply = choice["message"]["content"].strip()

    # Safety: if the reply was cut mid-sentence due to token limit,
    # truncate to the last complete sentence so it never looks broken.
    finish_reason = choice.get("finish_reason", "stop")
    if finish_reason == "length" and reply:
        for end_char in (".", "!", "?", "…"):
            last = reply.rfind(end_char)
            if last != -1 and last > len(reply) // 2:
                reply = reply[: last + 1]
                break

    # Instagram DM character limit safety
    if len(reply) > 950:
        reply = reply[:947] + "…"

    return reply


# ── Send Reply via Meta Graph API ────────────────────────────────────────

_RESOLVED_TOKEN: str | None = None
_RESOLVED_ENDPOINT: str | None = None


async def _get_messaging_credentials() -> tuple[str, str]:
    """
    Resolve the correct Graph API messages endpoint and token.
    If INSTAGRAM_ACCESS_TOKEN is a User Token, automatically retrieves
    the connected Facebook Page's token and ID so /messages succeeds.
    """
    global _RESOLVED_TOKEN, _RESOLVED_ENDPOINT
    if _RESOLVED_ENDPOINT and _RESOLVED_TOKEN:
        return _RESOLVED_ENDPOINT, _RESOLVED_TOKEN

    token = INSTAGRAM_ACCESS_TOKEN
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{GRAPH_BASE}/me/accounts", params={"access_token": token})
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    page = data[0]
                    page_id = page["id"]
                    page_token = page.get("access_token", token)
                    _RESOLVED_ENDPOINT = f"{GRAPH_BASE}/{page_id}/messages"
                    _RESOLVED_TOKEN = page_token
                    log.info("📌 Resolved Instagram Messaging to Page '%s' (ID: %s)", page.get("name"), page_id)
                    return _RESOLVED_ENDPOINT, _RESOLVED_TOKEN
    except Exception as exc:
        log.warning("Could not auto-resolve Page token: %s", exc)

    _RESOLVED_ENDPOINT = f"{GRAPH_BASE}/me/messages"
    _RESOLVED_TOKEN = token
    return _RESOLVED_ENDPOINT, _RESOLVED_TOKEN


async def send_reply(recipient_id: str, text: str) -> dict:
    """
    Send a text message to an Instagram user via the Meta Graph API.
    Endpoint: POST /{page_id}/messages
    """
    url, token = await _get_messaging_credentials()
    params = {"access_token": token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, params=params, json=payload)

        if resp.status_code != 200:
            log.error(
                "Graph API error (%d): %s",
                resp.status_code,
                resp.text[:300],
            )
            resp.raise_for_status()

        result = resp.json()
        log.debug("Graph API send_reply response: %s", result)
        return result


# ── Health Check ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Simple health check for monitoring."""
    return {
        "status": "healthy",
        "agent": "auto_inbox",
        "account_id": INSTAGRAM_ACCOUNT_ID,
    }


# ── Privacy Policy (required by Meta for Live mode) ─────────────────────

from fastapi.responses import HTMLResponse

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    """Privacy policy page required by Meta to switch app to Live mode."""
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy — AutoReel</title>
<style>body{font-family:system-ui,sans-serif;max-width:700px;margin:40px auto;padding:0 20px;color:#333;line-height:1.6}h1{color:#1a1a2e}</style>
</head><body>
<h1>Privacy Policy</h1>
<p><strong>Last updated:</strong> October 2026</p>
<p>AutoReel ("we", "our") operates an automated Instagram content management tool. This policy explains how we handle data.</p>
<h2>Data We Collect</h2>
<ul>
<li><strong>Instagram DMs:</strong> We process incoming direct message text to generate automated replies. Messages are not stored permanently.</li>
<li><strong>Account Info:</strong> We use your Instagram Business Account ID and access token (provided by you) to publish content and respond to messages.</li>
</ul>
<h2>How We Use Data</h2>
<ul>
<li>To generate and send automated DM replies via the Meta Graph API.</li>
<li>To create and publish Instagram Reels on your behalf.</li>
</ul>
<h2>Data Sharing</h2>
<p>We do not sell, share, or transfer your data to third parties. Message content is sent to OpenRouter/Google AI APIs solely for generating replies and is not retained.</p>
<h2>Data Retention</h2>
<p>Message content is processed in memory and discarded immediately after a reply is sent. No chat logs are stored.</p>
<h2>Contact</h2>
<p>For questions about this policy, contact the app administrator.</p>
</body></html>"""


# ── Uvicorn Server Factories ─────────────────────────────────────────────

def create_uvicorn_server(host: str = "0.0.0.0", port: int | None = None):
    """Create a Uvicorn server instance that can be run via `await server.serve()` in an asyncio loop."""
    import uvicorn

    port = port or INBOX_PORT
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",  # keep server logs quiet so bot logs are clean
        loop="asyncio",
    )
    return uvicorn.Server(config)


def run_server(host: str = "0.0.0.0", port: int | None = None) -> None:
    """Start the FastAPI server synchronously with uvicorn."""
    import uvicorn

    port = port or INBOX_PORT
    log.info("🚀 Starting Inbox Agent on %s:%d …", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
