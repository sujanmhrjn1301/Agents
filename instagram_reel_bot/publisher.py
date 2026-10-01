"""
publisher.py — Step 5: Instagram Publishing (Meta Graph API)

Implements the official 2-step Reels container workflow:
  1. POST /media           → create a media container (returns container_id)
  2. Poll container status → wait for FINISHED
  3. POST /media_publish   → publish the container
"""

from __future__ import annotations

import asyncio

import httpx

from .config import cfg
from .logger import log

def _get_graph_base() -> str:
    """Return the correct Meta Graph API endpoint based on token type."""
    token = cfg.instagram_access_token.strip()
    if token.startswith("IGQ"):
        return "https://graph.instagram.com/v21.0"
    return "https://graph.facebook.com/v21.0"


async def publish_reel(
    video_url: str,
    caption: str,
    hashtags: list[str] | None = None,
) -> str:
    """
    Publish a reel to Instagram via the Graph API.

    Parameters
    ----------
    video_url : str
        Publicly accessible URL of the final reel .mp4.
    caption : str
        Post caption text.
    hashtags : list[str], optional
        Hashtags to append to the caption.

    Returns
    -------
    str
        The published media ID.
    """
    full_caption = caption
    if hashtags:
        full_caption += "\n\n" + " ".join(hashtags)

    # ── 1. Create media container ────────────────────────────────────────
    log.info("📤 Creating Instagram media container…")
    container_id = await _create_container(video_url, full_caption)

    # ── 2. Wait for processing ───────────────────────────────────────────
    log.info("⏳ Waiting for Instagram to process the container…")
    await _poll_container_status(container_id)

    # ── 3. Publish ───────────────────────────────────────────────────────
    log.info("📢 Publishing reel…")
    media_id = await _publish_container(container_id)

    log.info("✅ Reel published!  Media ID: %s", media_id)
    return media_id


async def _create_container(video_url: str, caption: str) -> str:
    """POST to /{ig-user-id}/media to create a Reels container."""
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": cfg.instagram_access_token,
        "share_to_feed": "true",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{_get_graph_base()}/{cfg.instagram_account_id}/media",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    container_id = data.get("id")
    if not container_id:
        raise RuntimeError(f"No container ID in response: {data}")

    log.info("   Container created: %s", container_id)
    return container_id


async def _poll_container_status(container_id: str) -> None:
    """
    Poll GET /{container-id}?fields=status_code until FINISHED.

    Raises on ERROR or timeout.
    """
    elapsed = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while elapsed < cfg.ig_poll_timeout:
            await asyncio.sleep(cfg.ig_poll_interval)
            elapsed += cfg.ig_poll_interval

            resp = await client.get(
                f"{_get_graph_base()}/{container_id}",
                params={
                    "fields": "status_code,status",
                    "access_token": cfg.instagram_access_token,
                },
            )
            resp.raise_for_status()
            body = resp.json()

            status = body.get("status_code", "UNKNOWN")
            log.info("   Container status: %s (%d/%d s)", status, elapsed, cfg.ig_poll_timeout)

            if status == "FINISHED":
                return

            if status == "ERROR":
                detail = body.get("status", "no details")
                raise RuntimeError(
                    f"Instagram container processing failed: {detail}"
                )

    raise TimeoutError(
        f"Instagram container {container_id} did not finish within "
        f"{cfg.ig_poll_timeout}s"
    )


async def _publish_container(container_id: str) -> str:
    """POST to /{ig-user-id}/media_publish to make the reel live."""
    params = {
        "creation_id": container_id,
        "access_token": cfg.instagram_access_token,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{_get_graph_base()}/{cfg.instagram_account_id}/media_publish",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    media_id = data.get("id")
    if not media_id:
        raise RuntimeError(f"No media ID in publish response: {data}")

    return media_id
