"""
__main__.py — Unified CLI Entry Point for AutoReel & Auto-Inbox Agents.

By default, runs BOTH agents concurrently in a single process:
  1. 🎬 Reel Publisher Agent — Generates and publishes folklore reels on schedule.
  2. 📬 Auto-Inbox Agent — Real-time Meta webhook listener answering DMs via OpenRouter.

Usage:
    python -m instagram_reel_bot                  # Runs BOTH agents 24/7 (default)
    python -m instagram_reel_bot --fast-test       # Runs BOTH agents with fast-test reels
    python -m instagram_reel_bot --once            # Generates 1 reel and exits (no inbox server)
    python -m instagram_reel_bot --inbox-only      # Runs only the Auto-Inbox webhook server
    python -m instagram_reel_bot --reel-only       # Runs only the Reel Bot 24/7 loop
    python -m instagram_reel_bot --dry-run         # Dry-run reel generation (skips upload/publish)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Ensure UTF-8 output on Windows consoles to prevent cp1252 emoji encode errors
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="instagram_reel_bot",
        description="🚀 AutoReel: 24/7 Autonomous Instagram Reel Generator + DM Auto-Replier",
    )
    parser.add_argument(
        "--fast-test",
        "--mock-video",
        dest="fast_test",
        action="store_true",
        help="Use fast-test mode for instant reel validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute reel steps (plan → voice → visual → merge) but skip upload & publish.",
    )
    parser.add_argument(
        "--once",
        "--single-run",
        dest="single_run",
        action="store_true",
        help="Generate exactly one reel and exit (does not start 24/7 loop or inbox server).",
    )
    parser.add_argument(
        "--inbox-only",
        "--inbox",
        action="store_true",
        help="Start only the Auto-Inbox webhook agent (no reel posting).",
    )
    parser.add_argument(
        "--reel-only",
        action="store_true",
        help="Start only the 24/7 Reel generator loop (default).",
    )
    parser.add_argument(
        "--with-inbox",
        "--all",
        dest="with_inbox",
        action="store_true",
        help="Run both the 24/7 Reel generator and the Auto-Inbox server concurrently.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Explicitly run the 24/7 reel loop (same as default).",
    )
    parser.add_argument(
        "--inbox-port",
        type=int,
        default=None,
        help="Port for the inbox webhook server (default: 8000 or INBOX_PORT env var).",
    )
    return parser.parse_args()


async def _run_unified_agents(fast_test: bool = False, port: int | None = None) -> None:
    """Run both the 24/7 Reel Bot and the Auto-Inbox webhook agent concurrently."""
    from .config import cfg
    from .inbox import create_uvicorn_server
    from .pipeline import run_forever
    from .logger import log

    inbox_port = port or cfg.inbox_port
    server = create_uvicorn_server(port=inbox_port)

    log.info("=" * 65)
    log.info("🚀 AutoReel System — BOTH AGENTS ACTIVE CONCURRENTLY")
    log.info("   Agent 1: 🎬 Reel Scheduler (3x daily — 9-12 / 1-3 / 8-11 NPT)")
    log.info("   Agent 2: 📬 Auto-Inbox Webhook Server (Port: %d)", inbox_port)
    log.info("=" * 65)

    # Run both the Uvicorn server and the Reel loop concurrently in asyncio
    await asyncio.gather(
        server.serve(),
        run_forever(fast_test=fast_test),
    )


async def _main() -> None:
    args = _cli()

    # ── Inbox-only mode ──────────────────────────────────────────────────
    if args.inbox_only:
        from .inbox import create_uvicorn_server
        server = create_uvicorn_server(port=args.inbox_port)
        await server.serve()
        return

    # Deferred import so --help doesn't load everything
    from .pipeline import run_once, run_forever
    from .logger import log

    # ── Dry-run mode ─────────────────────────────────────────────────────
    if args.dry_run:
        log.info("🧪 Dry-run mode — will skip upload & publish.")
        from . import pipeline as _p

        async def _noop_upload(path):
            log.info("   [dry-run] Skipping upload for %s", path)
            return "https://example.com/dry-run.mp4"

        async def _noop_publish(video_url, caption, hashtags=None):
            log.info("   [dry-run] Skipping publish (caption: %s…)", caption[:40])
            return "dry-run-media-id"

        _p.upload_reel = _noop_upload
        _p.publish_reel = _noop_publish

    # ── Single reel run mode ─────────────────────────────────────────────
    if args.single_run:
        summary = await run_once(fast_test=args.fast_test)
        if summary["status"] != "published" and not args.dry_run:
            sys.exit(1)
        return

    # ── Both agents mode (opt-in via --with-inbox / --all) ───────────────
    if args.with_inbox:
        await _run_unified_agents(fast_test=args.fast_test, port=args.inbox_port)
        return

    # ── Default mode: REEL SCHEDULER ONLY (Completely independent) ────────
    await run_forever(fast_test=args.fast_test)


def main() -> None:
    """Synchronous wrapper for the async entry point."""
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n👋 Shutting down AutoReel System gracefully…")


if __name__ == "__main__":
    main()
