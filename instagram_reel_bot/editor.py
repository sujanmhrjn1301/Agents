"""
editor.py — Step 4: Post-Production (moviepy)

Merges voice.mp3 + video.mp4 → final_reel.mp4
Handles duration matching (loop video or trim audio) and ensures
the output meets Instagram Reels specs (9:16, H.264, AAC).
"""

from __future__ import annotations

from pathlib import Path

from moviepy import (
    AudioFileClip,
    VideoFileClip,
    CompositeAudioClip,
    vfx,
)

from .config import cfg
from .logger import log


def merge_reel(
    video_path: Path,
    audio_path: Path,
    output_path: Path | None = None,
) -> Path:
    """
    Stitch video and audio into a final Instagram-ready reel.

    Logic:
    - If video is shorter than audio → loop the video to match.
    - If audio is shorter than video → pad with silence (video plays out).
    - Final output is encoded as H.264 + AAC at 9:16 resolution.

    Parameters
    ----------
    video_path : Path
        Input video clip (.mp4).
    audio_path : Path
        Input voiceover audio (.mp3).
    output_path : Path, optional
        Destination for the final reel.  Defaults to ``<output_dir>/final_reel.mp4``.

    Returns
    -------
    Path
        Absolute path to the rendered final reel.
    """
    if output_path is None:
        output_path = cfg.output_dir / "final_reel.mp4"

    log.info("🎞️  Starting post-production merge…")

    video_clip: VideoFileClip | None = None
    audio_clip: AudioFileClip | None = None

    try:
        video_clip = VideoFileClip(str(video_path))
        audio_clip = AudioFileClip(str(audio_path))

        v_dur = video_clip.duration
        a_dur = audio_clip.duration
        log.info("   Video duration: %.2f s  |  Audio duration: %.2f s", v_dur, a_dur)

        # ── Duration matching ────────────────────────────────────────────
        target_dur = max(v_dur, a_dur)

        if v_dur < target_dur:
            # Loop video to cover the full audio length
            log.info("   Looping video to match audio length (%.2f s)…", target_dur)
            video_clip = video_clip.with_effects([vfx.Loop(duration=target_dur)])
        elif a_dur < target_dur:
            # Trim video to audio length (keeps it tight)
            log.info("   Trimming video to match audio length (%.2f s)…", a_dur)
            video_clip = video_clip.subclipped(0, a_dur)
            target_dur = a_dur

        # ── Resolution enforcement (9:16) ────────────────────────────────
        w, h = video_clip.size
        if w != cfg.video_width or h != cfg.video_height:
            log.info("   Resizing %dx%d → %dx%d", w, h, cfg.video_width, cfg.video_height)
            video_clip = video_clip.resized((cfg.video_width, cfg.video_height))

        # ── Attach audio ─────────────────────────────────────────────────
        video_clip = video_clip.with_audio(audio_clip)

        # ── Render ───────────────────────────────────────────────────────
        log.info("   Rendering final reel → %s …", output_path.name)
        video_clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=cfg.video_fps,
            preset="medium",
            bitrate="5000k",
            logger=None,  # suppress moviepy's own progress bar
        )

        size_mb = output_path.stat().st_size / (1024 * 1024)
        log.info("✅ Final reel saved → %s (%.2f MB)", output_path.name, size_mb)
        return output_path

    finally:
        # Deterministic cleanup — release file handles
        if audio_clip is not None:
            audio_clip.close()
        if video_clip is not None:
            video_clip.close()
