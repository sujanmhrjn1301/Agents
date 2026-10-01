# 🎬 AutoReel — Instagram Reel Bot + DM Auto-Replier

Fully autonomous system that runs **two agents** concurrently:

1. **🎬 Reel Scheduler** — Plans, voices, renders, edits, and publishes Instagram Reels at 3 randomised daily windows (Morning / Afternoon / Evening).
2. **📬 Auto-Inbox Agent** — Listens for Instagram DMs via Meta Webhooks and replies instantly in casual Nepali-English (Nenglish) style using `gemini-2.5-flash-lite`.

No human intervention required — just start it and walk away.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                     AutoReel System (__main__.py)                     │
│                     asyncio.gather(reel, inbox)                      │
├──────────────────────────────┬────────────────────────────────────────┤
│                              │                                        │
│  🎬 REEL SCHEDULER          │  📬 AUTO-INBOX AGENT                   │
│  (pipeline.py)               │  (inbox.py — FastAPI + Uvicorn)        │
│                              │                                        │
│  ┌─────────┐  ┌─────────┐   │  GET  /webhook  → Meta verification    │
│  │ 1.BRAIN │→ │ 2.VOICE │   │  POST /webhook  → DM receive + reply   │
│  │GPT-4o-  │  │edge-tts │   │  GET  /health   → health check         │
│  │ mini    │  │(free)   │   │                                        │
│  └────┬────┘  └────┬────┘   │  DM flow:                              │
│       │            │        │  webhook → gemini-2.5-flash-lite →     │
│       ▼            ▼        │  Graph API → reply sent                │
│  ┌─────────────────────┐    │                                        │
│  │ 3. VIDEO ENGINE     │    ├────────────────────────────────────────┤
│  │ Pollinations FLUX   │    │  🕐 Upload Schedule (Nepal Time)       │
│  │ + Ken Burns motion  │    │  ┌────────────┬───────────────┐        │
│  │ OpenRouter fallback │    │  │ Morning    │ 09:00 – 12:00 │        │
│  └─────────┬───────────┘    │  │ Afternoon  │ 13:00 – 15:00 │        │
│            ▼                │  │ Evening    │ 20:00 – 23:00 │        │
│  ┌─────────────────────┐    │  └────────────┴───────────────┘        │
│  │ 4. EDITOR           │    │  Random time picked within each window │
│  │ moviepy merge       │    │  3 reels per day, fully randomised     │
│  │ voice + video → mp4 │    │                                        │
│  └─────────┬───────────┘    │                                        │
│            ▼                │                                        │
│  ┌─────────────────────┐    │                                        │
│  │ 5. PUBLISH          │    │                                        │
│  │ Supabase → IG API   │    │                                        │
│  └─────────────────────┘    │                                        │
└──────────────────────────────┴────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Install dependencies
cd Agents
pip install -r requirements.txt

# 2. Configure secrets
copy .env.example .env
# Edit .env with your real API keys (see Environment Variables below)

# 3. Run the AutoReel Generator (independent Reel Scheduler)
python -m instagram_reel_bot
```

That's it. The Reel Scheduler will:
- Calculate today's 3 random upload windows (Morning 9–12 / Afternoon 1–3 / Evening 8–11 Nepal Time)
- Sleep until each scheduled time, generate the reel, and publish automatically

---

## All CLI Modes (Independent Execution)

```bash
# 🎬 Default: Run ONLY the Reel Scheduler (no inbox server, port 8000 stays closed)
python -m instagram_reel_bot

# 📬 Run ONLY the DM Auto-Replier (independent inbox agent)
python -m instagram_reel_bot --inbox-only
# or directly:
python -m instagram_reel_bot.inbox

# 🚀 Run BOTH agents concurrently (opt-in)
python -m instagram_reel_bot --all

# ⚡ Generate exactly 1 reel and exit immediately
python -m instagram_reel_bot --once

# 🧪 Dry-run — full pipeline but skip upload & publish
python -m instagram_reel_bot --dry-run

# Fast-test — use instant test video for pipeline validation
python -m instagram_reel_bot --fast-test

# Custom inbox port
python -m instagram_reel_bot --inbox-port 9000
```

---

## Setting Up DM Auto-Replies (ngrok + Meta Webhooks)

The Auto-Inbox agent needs a public URL for Meta to send webhook events. Use **ngrok** for development:

```bash
# 1. Install ngrok (if not already installed)
winget install ngrok.ngrok

# 2. Add your authtoken (get from https://dashboard.ngrok.com)
ngrok config add-authtoken YOUR_TOKEN_HERE

# 3. Start tunnel (in a separate terminal)
ngrok http 8000
```

Then in the **Meta Developer Dashboard** → Your App → **Instagram** → **Webhooks**:

1. Set **Callback URL** to: `https://YOUR-NGROK-URL.ngrok-free.app/webhook`
2. Set **Verify Token** to: `autoreel_verify_2026` (or whatever is in your `.env`)
3. Subscribe to the **`messages`** field under **Instagram**

---

## Project Structure

```
Agents/
├── .env.example                    # Template — copy to .env
├── .env                            # Your secrets (git-ignored)
├── requirements.txt
├── README.md
│
├── instagram_reel_bot/
│   ├── __init__.py
│   ├── __main__.py                 # CLI entry point (runs both agents)
│   ├── config.py                   # Env loader + validated Config dataclass
│   ├── logger.py                   # Structured logging (console + file)
│   ├── brain.py                    # Step 1: OpenRouter content planner
│   ├── voice.py                    # Step 2: edge-tts voiceover
│   ├── video.py                    # Step 3: Motion imagery + Ken Burns engine
│   ├── editor.py                   # Step 4: moviepy merge + encode
│   ├── uploader.py                 # Supabase Storage upload
│   ├── publisher.py                # Step 5: Instagram Graph API publish
│   ├── pipeline.py                 # Smart scheduler (3 daily windows)
│   └── inbox.py                    # Auto-Inbox DM agent (FastAPI webhooks)
│
├── output/                         # Generated assets (per-run subdirectories)
│   └── 20261001_120000/
│       ├── plan.json
│       ├── voice.mp3
│       ├── video.mp4
│       ├── final_reel.mp4
│       └── summary.json
│
└── logs/
    └── bot.log
```

---

## Environment Variables

| Variable                    | Required | Description                                              |
|-----------------------------|----------|----------------------------------------------------------|
| `OPENROUTER_API_KEY`        | ✅       | OpenRouter API key (used for brain + DM reply fallback)  |
| `INSTAGRAM_ACCOUNT_ID`      | ✅       | Instagram Business/Creator account ID                    |
| `INSTAGRAM_ACCESS_TOKEN`    | ✅       | Long-lived Instagram/Facebook Graph API token            |
| `SUPABASE_URL`              | ✅       | Supabase project URL for reel video hosting              |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅       | Supabase service-role key for Storage API                |
| `SUPABASE_STORAGE_BUCKET`   | ❌       | Storage bucket name (default: `reels`)                   |
| `VISUAL_ENGINE`             | ❌       | `motion_image` / `siliconflow` / `gradio` (default: `motion_image`) |
| `SILICONFLOW_API_KEY`       | ❌       | SiliconFlow API key (optional visual fallback)           |
| `WEBHOOK_VERIFY_TOKEN`      | ❌       | Meta webhook verify token (default: `autoreel_verify_2026`) |
| `INBOX_PORT`                | ❌       | Webhook server port (default: `8000`)                    |
| `GRADIO_SPACE_ID`           | ❌       | Gradio Space for video (default: `Lightricks/ltx-video-distilled`) |
| `HF_TOKEN`                  | ❌       | HuggingFace token for priority Gradio access             |

---

## Key Design Decisions

- **Async-first** — the entire pipeline is `async/await` for efficient I/O.
- **Concurrent voice + video** — Steps 2 & 3 run in parallel via `asyncio.gather`.
- **Window-based scheduling** — 3 daily upload windows with randomised times instead of fixed intervals.
- **Tone-mirroring DM replies** — `gemini-2.5-flash-lite` reads sender emotion and replies in matching Nepali-English style.
- **Multi-tier visual engine** — Pollinations FLUX → OpenRouter Gemini → SiliconFlow → Gradio LTX as fallback chain.
- **Cutoff-safe replies** — if LLM output is truncated by token limit, the reply is trimmed to the last complete sentence.
- **Deterministic cleanup** — moviepy clips are closed in `finally` blocks; old run folders are cleaned after publish.
- **Per-run directories** — every pipeline run gets a timestamped folder with all assets + JSON summary.

## Prerequisites

- **Python 3.11+**
- **ffmpeg** installed and on PATH (required by moviepy for encoding)
- An **Instagram Business** or **Creator** account connected to a Facebook Page
- A long-lived **Instagram Graph API access token** with `instagram_content_publish` and `instagram_manage_messages` permissions
- **ngrok** (for development webhook tunneling)
