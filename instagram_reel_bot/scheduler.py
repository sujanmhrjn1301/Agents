"""
scheduler.py - Persistent Schedule Manager (Supabase-backed)

Stores today's 3 posting slots in Supabase reel_schedule table.
On every startup:
  - If today's row already exists: reuse the saved times (no random re-roll)
  - If no row for today: generate 3 random slots and save them

This means restarting the bot NEVER changes the schedule for the current day.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

import httpx

from .config import cfg
from .logger import log

# Nepal Standard Time = UTC + 5h 45m
NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))

# Upload windows: (label, start_hour, end_hour)  -- all in Nepal Time
WINDOWS: list[tuple[str, int, int]] = [
    ("Morning",   9,  12),
    ("Afternoon", 13, 15),
    ("Evening",   20, 23),
]


class DailySchedule(NamedTuple):
    """Three posting slots for a single day, in chronological order."""
    morning:   datetime
    afternoon: datetime
    evening:   datetime

    def as_list(self) -> list[tuple[str, datetime]]:
        return [
            ("Morning",   self.morning),
            ("Afternoon", self.afternoon),
            ("Evening",   self.evening),
        ]


# -- Internal helpers --------------------------------------------------------

def _supabase_headers() -> dict[str, str]:
    key = cfg.supabase_service_role_key
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _table_url() -> str:
    return f"{cfg.supabase_url}/rest/v1/reel_schedule"


def _random_slot(today: datetime, start_h: int, end_h: int) -> datetime:
    """Pick a random minute within [start_h:00, end_h:00) on today's date."""
    rand_minute = random.randint(0, (end_h - start_h) * 60 - 1)
    slot = today.replace(hour=start_h, minute=0, second=0, microsecond=0)
    return slot + timedelta(minutes=rand_minute)


def _generate_new_schedule(today: datetime) -> DailySchedule:
    """Create 3 new random slots for today."""
    slots = {label: _random_slot(today, sh, eh) for label, sh, eh in WINDOWS}
    return DailySchedule(
        morning=slots["Morning"],
        afternoon=slots["Afternoon"],
        evening=slots["Evening"],
    )


# -- Supabase persistence ----------------------------------------------------

def _load_from_supabase(date_str: str) -> DailySchedule | None:
    """
    Try to fetch today's row from Supabase.
    Returns a DailySchedule or None if not found / Supabase not configured.
    """
    if not (cfg.supabase_url and cfg.supabase_service_role_key):
        return None

    try:
        url = f"{_table_url()}?schedule_date=eq.{date_str}&select=morning_slot,afternoon_slot,evening_slot"
        r = httpx.get(url, headers=_supabase_headers(), timeout=10)
        if r.status_code != 200:
            log.warning("Could not read reel_schedule from Supabase (%d): %s", r.status_code, r.text[:200])
            return None

        rows = r.json()
        if not rows:
            return None  # No row for today yet

        row = rows[0]
        return DailySchedule(
            morning=datetime.fromisoformat(row["morning_slot"]).astimezone(NEPAL_TZ),
            afternoon=datetime.fromisoformat(row["afternoon_slot"]).astimezone(NEPAL_TZ),
            evening=datetime.fromisoformat(row["evening_slot"]).astimezone(NEPAL_TZ),
        )
    except Exception as exc:
        log.warning("Supabase read error (will generate new schedule): %s", exc)
        return None


def _save_to_supabase(date_str: str, schedule: DailySchedule) -> None:
    """
    Upsert today's schedule into Supabase.
    Does nothing gracefully if Supabase is not configured.
    """
    if not (cfg.supabase_url and cfg.supabase_service_role_key):
        return

    try:
        payload = {
            "schedule_date":   date_str,
            "morning_slot":    schedule.morning.isoformat(),
            "afternoon_slot":  schedule.afternoon.isoformat(),
            "evening_slot":    schedule.evening.isoformat(),
        }
        headers = {**_supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        r = httpx.post(_table_url(), headers=headers, json=payload, timeout=10)
        if r.status_code in (200, 201):
            log.info("Schedule saved to Supabase for %s", date_str)
        else:
            log.warning("Could not save schedule to Supabase (%d): %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("Supabase write error: %s", exc)


# -- Public API --------------------------------------------------------------

def get_or_create_schedule() -> DailySchedule:
    """
    Get today's posting schedule.

    - If Supabase has a row for today: return saved slots (no re-randomizing).
    - Otherwise: generate new random slots, persist them, then return.
    """
    now = datetime.now(NEPAL_TZ)
    date_str = now.date().isoformat()  # e.g. "2026-10-01"

    # Try to load existing schedule first
    existing = _load_from_supabase(date_str)
    if existing is not None:
        log.info("Loaded existing schedule from Supabase for %s:", date_str)
        for label, slot in existing.as_list():
            log.info("      %-12s -> %s", label, slot.strftime("%I:%M %p"))
        log.info("   (Restarting the bot will NOT change these times)")
        return existing

    # No existing schedule -- generate and save
    log.info("No saved schedule for %s -- generating new random slots...", date_str)
    schedule = _generate_new_schedule(now)
    _save_to_supabase(date_str, schedule)

    log.info("New schedule created and saved for %s:", date_str)
    for label, slot in schedule.as_list():
        log.info("      %-12s -> %s", label, slot.strftime("%I:%M %p"))

    return schedule
