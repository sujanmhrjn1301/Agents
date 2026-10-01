"""
voice.py — Step 2: TTS Voice Generation (edge-tts)

Converts voiceover_text → voice.mp3 using Microsoft Edge's free TTS engine.
"""

from __future__ import annotations

from pathlib import Path

import edge_tts

from .config import cfg
from .logger import log


async def generate_voice(text: str, output_path: Path | None = None) -> Path:
    """
    Synthesise speech from *text* and save as MP3.

    Parameters
    ----------
    text : str
        The voiceover script to synthesise.
    output_path : Path, optional
        Where to write the .mp3 file.  Defaults to ``<output_dir>/voice.mp3``.

    Returns
    -------
    Path
        Absolute path to the saved audio file.
    """
    if output_path is None:
        output_path = cfg.output_dir / "voice.mp3"

    log.info("🎙️  Generating voiceover (%d chars, voice=%s)…", len(text), cfg.tts_voice)

    communicate = edge_tts.Communicate(text, cfg.tts_voice)
    await communicate.save(str(output_path))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"TTS produced an empty file at {output_path}")

    size_kb = output_path.stat().st_size / 1024
    log.info("✅ Voice saved → %s (%.1f KB)", output_path.name, size_kb)
    return output_path
