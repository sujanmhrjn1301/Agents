"""
logger.py — Structured logging for the bot (Windows-safe).

Usage:
    from logger import log
    log.info("Starting pipeline...")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that replaces unencodable characters instead of crashing."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            # Encode with 'replace' to handle emojis / box-drawing chars on Windows
            stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            msg_safe = msg.encode(stream.encoding or "utf-8", errors="replace").decode(
                stream.encoding or "utf-8", errors="replace"
            )
            stream.write(msg_safe + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("reel_bot")
    logger.setLevel(logging.DEBUG)

    # -- Console handler (INFO+) — Windows-safe --
    console = _SafeStreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(console)

    # -- File handler (DEBUG+) — always UTF-8 --
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
    )
    logger.addHandler(file_handler)

    return logger


log = _build_logger()
