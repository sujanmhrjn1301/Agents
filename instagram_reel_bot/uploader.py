"""
uploader.py — File hosting for the final reel.

Provides two strategies:
  1. Supabase Storage — if SUPABASE_URL + service role key are configured.
  2. file.io          — zero-config temporary hosting (free, 1 download).

Instagram's Graph API requires a publicly accessible video_url,
so the reel must be hosted *somewhere* before publishing.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from .config import cfg
from .logger import log


async def upload_reel(file_path: Path) -> str:
    """
    Upload the final reel to a public URL.

    Returns
    -------
    str
        A publicly accessible URL pointing to the uploaded video.
    """
    if cfg.supabase_url and cfg.supabase_service_role_key:
        return await _upload_supabase(file_path)
    return await _upload_fileio(file_path)


# ── Strategy 1: Supabase Storage ─────────────────────────────────────────

async def _upload_supabase(file_path: Path) -> str:
    """
    Upload to a **public** Supabase Storage bucket via the REST API.

    Prerequisites:
      • Create a bucket (e.g. "reels") in your Supabase dashboard.
      • Set the bucket to **Public** so Instagram can fetch the URL.
    """
    bucket = cfg.supabase_storage_bucket
    # Unique filename to avoid collisions across runs
    object_name = f"{uuid.uuid4().hex}_{file_path.name}"

    upload_url = (
        f"{cfg.supabase_url}/storage/v1/object/{bucket}/{object_name}"
    )
    headers = {
        "Authorization": f"Bearer {cfg.supabase_service_role_key}",
        "apikey": cfg.supabase_service_role_key,
        "Content-Type": "video/mp4",
        "x-upsert": "true",  # overwrite if exists
    }

    log.info("☁️  Uploading to Supabase Storage (bucket=%s)…", bucket)

    async with httpx.AsyncClient(timeout=180) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(upload_url, headers=headers, content=f.read())

        resp.raise_for_status()

    # Public URL pattern for Supabase Storage public buckets
    public_url = (
        f"{cfg.supabase_url}/storage/v1/object/public/{bucket}/{object_name}"
    )

    log.info("✅ Uploaded to Supabase → %s", public_url)
    return public_url


# ── Strategy 2: file.io (zero-config fallback) ──────────────────────────

async def _upload_fileio(file_path: Path) -> str:
    """Upload to file.io — single-download temporary hosting."""
    log.info("☁️  Uploading to file.io (temporary host)…")

    async with httpx.AsyncClient(timeout=120) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                "https://file.io",
                files={"file": (file_path.name, f, "video/mp4")},
                data={"expires": "1d"},
            )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"file.io upload failed: {data}")

    url = data["link"]
    log.info("✅ Uploaded to file.io → %s", url)
    return url
