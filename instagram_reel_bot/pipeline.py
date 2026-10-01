"""
pipeline.py — Master Orchestrator

Wires together all five steps into a single async pipeline
and provides a smart scheduled loop that posts within three
daily time windows:
  - Morning   : 09:00 – 12:00
  - Afternoon : 13:00 – 15:00
  - Evening   : 20:00 – 23:00
(All times in Nepal Time, UTC+5:45)
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .brain import generate_reel_plan
from .voice import generate_voice
from .video import generate_video
from .editor import merge_reel
from .uploader import upload_reel, delete_remote_reel
from .publisher import publish_reel
from .scheduler import get_or_create_schedule, NEPAL_TZ
from .config import cfg
from .logger import log


async def run_once(fast_test: bool = False) -> dict:
    """
    Execute the full reel pipeline exactly once.

    Parameters
    ----------
    fast_test : bool
        If True, provides an instant 5-second 9:16 vertical video clip
        for fast pipeline testing without waiting for cloud GPU queues.

    Returns a summary dict with keys: status, media_id, caption, error.
    """
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = cfg.output_dir / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {"status": "started", "timestamp": run_ts}

    try:
        # ── Step 1: Generate the plan ────────────────────────────────────
        log.info("=" * 60)
        log.info("[*] PIPELINE START [%s]%s", run_ts, " [FAST-TEST MODE]" if fast_test else "")
        log.info("=" * 60)

        plan = await generate_reel_plan()
        summary["caption"] = plan.caption
        summary["hashtags"] = plan.hashtags

        # Save plan to disk for auditability
        plan_data = {
            "caption": plan.caption,
            "hashtags": plan.hashtags,
            "voiceover_text": plan.voiceover_text,
            "scenes": [
                {"description": s.description, "video_prompt": s.video_prompt, "duration_hint": s.duration_hint}
                for s in plan.scenes
            ],
            "negative_prompt": plan.negative_prompt,
        }
        (run_dir / "plan.json").write_text(
            json.dumps(plan_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # ── Step 2 & 3: Voice + Video (concurrent) ──────────────────────
        log.info("-" * 60)
        log.info("Launching voice + video generation in parallel...")

        voice_path, video_path = await asyncio.gather(
            generate_voice(plan.voiceover_text, run_dir / "voice.mp3"),
            generate_video(
                scenes=[
                    {"video_prompt": s.video_prompt, "duration_hint": s.duration_hint}
                    for s in plan.scenes
                ],
                negative_prompt=plan.negative_prompt,
                output_path=run_dir / "video.mp4",
                fast_test=fast_test,
            ),
        )

        # ── Step 4: Merge ────────────────────────────────────────────────
        log.info("-" * 60)
        final_path = merge_reel(video_path, voice_path, run_dir / "final_reel.mp4")

        # ── Step 5: Upload & Publish ─────────────────────────────────────
        log.info("-" * 60)
        public_url = await upload_reel(final_path)
        media_id = await publish_reel(
            video_url=public_url,
            caption=plan.caption,
            hashtags=plan.hashtags,
        )

        summary.update({"status": "published", "media_id": media_id})
        log.info("=" * 60)
        log.info("[+] PIPELINE COMPLETE -- Media ID: %s", media_id)
        log.info("=" * 60)

        # ── Auto-cleanup: remove large media files & delete from Supabase ──
        await delete_remote_reel(public_url)
        _cleanup_run_dir(run_dir)

    except Exception as exc:
        summary.update({"status": "failed", "error": str(exc)})
        log.error("❌ Pipeline failed: %s", exc)
        log.debug(traceback.format_exc())
        log.info("📁 Local files preserved for debugging in: %s", run_dir)

    # Persist run summary
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return summary


def _cleanup_run_dir(run_dir: Path) -> None:
    """
    Remove large media files from a run directory after successful publish.
    Keeps plan.json and summary.json for audit trail.
    """
    import shutil

    keep = {"plan.json", "summary.json"}
    removed_mb = 0.0

    # Remove scene clips subfolder
    scenes_dir = run_dir / "scenes"
    if scenes_dir.exists():
        for f in scenes_dir.iterdir():
            removed_mb += f.stat().st_size / (1024 * 1024)
        shutil.rmtree(scenes_dir)

    # Remove top-level media files
    for f in run_dir.iterdir():
        if f.is_file() and f.name not in keep:
            removed_mb += f.stat().st_size / (1024 * 1024)
            f.unlink()

    log.info("🧹 Cleanup done — freed %.1f MB from %s", removed_mb, run_dir.name)


async def run_forever(fast_test: bool = False) -> None:
    """
    Smart scheduler — posts reels at fixed (but randomised-once) times within
    three daily windows:

      Window 1 — Morning   : 09:00 – 12:00  (Nepal Time)
      Window 2 — Afternoon : 13:00 – 15:00  (Nepal Time)
      Window 3 — Evening   : 20:00 – 23:00  (Nepal Time)

    Times are randomly chosen ONCE per day and stored in Supabase.
    Restarting the bot does NOT change today's schedule.
    Ctrl-C exits gracefully.
    """
    log.info("=" * 65)
    log.info("📅 AutoReel Smart Scheduler — Window-Based Posting (Nepal Time)")
    log.info("   Window 1 — Morning   : 09:00 – 12:00")
    log.info("   Window 2 — Afternoon : 13:00 – 15:00")
    log.info("   Window 3 — Evening   : 20:00 – 23:00")
    log.info("   Schedule is FIXED per day — stored in Supabase.")
    log.info("=" * 65)

    while True:
        now = datetime.now(NEPAL_TZ)

        # Load or create today's persistent schedule from Supabase
        schedule = get_or_create_schedule()

        # Build list of upcoming slots (skip any that have already passed today)
        upcoming: list[tuple[str, datetime]] = []
        for name, slot in schedule.as_list():
            if slot > now:
                upcoming.append((name, slot))
            else:
                log.info("   ⏭️  %s slot (%s) already passed — skipping.", name, slot.strftime("%I:%M %p"))

        if not upcoming:
            # All 3 slots already passed — wait until midnight + 1 min, then recalculate
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
            wait_secs = (tomorrow - now).total_seconds()
            log.info("🌙 All windows done for today. Waiting until tomorrow (%s NPT)…", tomorrow.strftime("%Y-%m-%d %I:%M %p"))
            await asyncio.sleep(wait_secs)
            continue

        for name, slot in upcoming:
            now = datetime.now(NEPAL_TZ)
            wait_secs = max(0.0, (slot - now).total_seconds())
            wait_h = int(wait_secs // 3600)
            wait_m = int((wait_secs % 3600) // 60)

            log.info(
                "⏰ Next upload: %s window @ %s — waiting %dh %dm",
                name,
                slot.strftime("%I:%M %p"),
                wait_h,
                wait_m,
            )

            await asyncio.sleep(wait_secs)

            log.info("🌟 %s window slot reached — starting reel pipeline…", name)
            summary = await run_once(fast_test=fast_test)

            if summary["status"] == "published":
                log.info("✅ Reel published in %s window!", name)
            else:
                log.warning("⚠️  Reel pipeline failed during %s window.", name)

        # All pending windows done — wait until tomorrow then recalculate
        now = datetime.now(NEPAL_TZ)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        wait_secs = (tomorrow - now).total_seconds()
        log.info("🌙 All windows done for today. Waiting until tomorrow (%s NPT)…", tomorrow.strftime("%Y-%m-%d %I:%M %p"))
        await asyncio.sleep(wait_secs)

