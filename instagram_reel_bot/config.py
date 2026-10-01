"""
config.py — Centralised configuration & environment variable loader.

All secrets and tunables live here so every other module can
`from config import cfg` without touching os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env from the project root (one level above this file) ──────────
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


def _require(key: str) -> str:
    """Return an env-var value or raise with a helpful message."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Add it to {_ENV_PATH} or export it in your shell."
        )
    return val


@dataclass(frozen=True)
class Config:
    """Immutable, validated runtime configuration."""

    # ── API keys ─────────────────────────────────────────────────────────
    openrouter_api_key: str = field(default_factory=lambda: _require("OPENROUTER_API_KEY"))
    instagram_account_id: str = field(default_factory=lambda: _require("INSTAGRAM_ACCOUNT_ID"))
    instagram_access_token: str = field(default_factory=lambda: _require("INSTAGRAM_ACCESS_TOKEN"))

    # ── Gradio / Hugging Face Video Generator ────────────────────────────
    gradio_space_id: str = field(default_factory=lambda: os.getenv("GRADIO_SPACE_ID", "Lightricks/ltx-video-distilled"))
    hf_token: str = field(default_factory=lambda: os.getenv("HF_TOKEN", ""))

    # ── Optional: Supabase Storage for hosting the final reel ────────────
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_role_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    supabase_storage_bucket: str = field(default_factory=lambda: os.getenv("SUPABASE_STORAGE_BUCKET", "reels"))

    # ── SiliconFlow (optional fallback for image & video) ────────────────
    siliconflow_api_key: str = field(default_factory=lambda: os.getenv("SILICONFLOW_API_KEY", ""))

    # ── Visual Engine ────────────────────────────────────────────────────
    # Options: "motion_image" (default, free & fast Ken Burns effects), "siliconflow", "gradio"
    visual_engine: str = field(default_factory=lambda: os.getenv("VISUAL_ENGINE", "motion_image"))

    # ── Auto-Inbox Agent Webhook ─────────────────────────────────────────
    inbox_port: int = field(default_factory=lambda: int(os.getenv("INBOX_PORT", "8000")))
    webhook_verify_token: str = field(default_factory=lambda: os.getenv("WEBHOOK_VERIFY_TOKEN", "autoreel_verify_2026"))

    # ── Model / endpoint tunables ────────────────────────────────────────
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"))

    # ── TTS defaults ─────────────────────────────────────────────────────
    tts_voice: str = "en-US-ChristopherNeural"  # male; swap for "en-US-JennyNeural" etc.

    # ── Video spec ───────────────────────────────────────────────────────
    video_width: int = 720
    video_height: int = 1280  # 9:16 vertical
    video_fps: int = 30

    # ── Polling ──────────────────────────────────────────────────────────
    gradio_poll_interval: int = 10   # seconds between status checks
    gradio_poll_timeout: int = int(os.getenv("GRADIO_POLL_TIMEOUT", "1200"))   # max wait (20 min default)
    ig_poll_interval: int = 5
    ig_poll_timeout: int = 120

    # ── Scheduling ───────────────────────────────────────────────────────
    loop_interval_minutes: int = int(os.getenv("LOOP_INTERVAL_MINUTES", "60"))

    # ── Filesystem ───────────────────────────────────────────────────────
    output_dir: Path = Path(__file__).resolve().parent.parent / "output"

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


# Singleton — import this everywhere
cfg = Config()
