"""
video.py — Step 3: AI Visual & Video Generation

Supports multiple engines with automatic resilience & fallbacks:
  1. Motion Imagery (default): High-res 9:16 FLUX AI images + cinematic Ken Burns
     camera motion (slow dynamic zoom & pan) stitched with crossfade transitions.
     - 100% Free, infinite daily quota, generates in ~8–12 seconds, zero GPU timeouts.
     - Primary: Pollinations FLUX (free, fast, no API key needed).
     - Fallback: SiliconFlow FLUX (if SILICONFLOW_API_KEY is configured).
  2. SiliconFlow API: Cloud AI image/video generation fallback.
  3. Hugging Face Gradio Space: LTX-Video distilled (with auto-fallback to Motion
     Imagery if ZeroGPU quota is exceeded).
  4. Fast-Test: Instant local vertical clip for pipeline validation.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter
import numpy as np
from moviepy import VideoClip, VideoFileClip, concatenate_videoclips

from .config import cfg
from .logger import log


# ── Gradio Path Extraction (for HF spaces) ───────────────────────────────

def _extract_video_path(val: Any) -> str | None:
    """Extract local video file path from various Gradio return structures."""
    if not val:
        return None
    if isinstance(val, str) and (val.endswith(".mp4") or Path(val).exists()):
        return val
    if isinstance(val, dict):
        if "value" in val and val["value"] is not None:
            sub = _extract_video_path(val["value"])
            if sub:
                return sub
        video_val = val.get("video") or val.get("path") or val.get("url")
        if isinstance(video_val, str) and Path(video_val).exists():
            return video_val
        if isinstance(video_val, dict):
            sub = _extract_video_path(video_val)
            if sub:
                return sub
    if isinstance(val, (list, tuple)):
        for item in val:
            sub = _extract_video_path(item)
            if sub:
                return sub
    return None


# ── AI Image Generation Providers ─────────────────────────────────────────

async def _fetch_pollinations_image(
    prompt: str,
    output_path: Path,
    width: int = 720,
    height: int = 1280,
    seed: int | None = None,
) -> bool:
    """Download an AI image from Pollinations (FLUX model, completely free, no API key)."""
    clean_prompt = prompt.strip()
    # Enhance prompt for vertical 9:16 cinematic visuals
    enhanced_prompt = f"{clean_prompt}, 9:16 vertical composition, mythological fantasy art, cinematic lighting, 8k, hyper-detailed, masterpiece"
    encoded = urllib.parse.quote(enhanced_prompt)

    seed_param = f"&seed={seed}" if seed else ""
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&model=flux&nologo=true{seed_param}"
    )

    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "AutoReel/2.0"})
            if resp.status_code == 200 and len(resp.content) > 5000:
                output_path.write_bytes(resp.content)
                return True
            log.warning("Pollinations returned HTTP %d (%d bytes)", resp.status_code, len(resp.content))
    except Exception as exc:
        log.warning("Pollinations image generation failed: %s", exc)

    return False


async def _fetch_siliconflow_image(
    prompt: str,
    output_path: Path,
    width: int = 720,
    height: int = 1280,
) -> bool:
    """Download an AI image from SiliconFlow API (FLUX.1-schnell / SD3)."""
    if not cfg.siliconflow_api_key:
        return False

    url = "https://api.siliconflow.cn/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {cfg.siliconflow_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "black-forest-labs/FLUX.1-schnell",
        "prompt": f"{prompt}, vertical 9:16 cinematic, hyper-detailed, mythological fantasy",
        "image_size": f"{width}x{height}",
        "num_inference_steps": 4,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("images", [{}])[0].get("url")
                if img_url:
                    img_resp = await client.get(img_url)
                    if img_resp.status_code == 200:
                        output_path.write_bytes(img_resp.content)
                        return True
            log.warning("SiliconFlow image API error (%d): %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("SiliconFlow image generation error: %s", exc)

    return False


async def _fetch_openrouter_image(
    prompt: str,
    output_path: Path,
) -> bool:
    """Download an AI visual using OpenRouter's multimodal image models."""
    if not cfg.openrouter_api_key:
        return False

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://instagram-reel-bot.local",
    }
    payload = {
        "model": "google/gemini-2.5-flash-image",
        "messages": [
            {
                "role": "user",
                "content": f"Generate a high-detail 9:16 vertical photorealistic visual for this scene: {prompt}",
            }
        ],
    }

    try:
        import base64
        import re

        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})

                # Check OpenRouter image array: message.images[0].image_url.url
                images = msg.get("images", [])
                if images:
                    img_entry = images[0].get("image_url", {})
                    url_val = img_entry.get("url", "")
                    if url_val.startswith("data:image/"):
                        b64_data = url_val.split(",", 1)[1]
                        output_path.write_bytes(base64.b64decode(b64_data))
                        return True
                    elif url_val.startswith("http"):
                        img_resp = await client.get(url_val)
                        if img_resp.status_code == 200:
                            output_path.write_bytes(img_resp.content)
                            return True

                content = msg.get("content") or ""
                
                # Check for base64 data URI in text
                b64_match = re.search(r"data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", content)
                if b64_match:
                    raw_bytes = base64.b64decode(b64_match.group(1))
                    output_path.write_bytes(raw_bytes)
                    return True

                # Check for direct image URL: ![...](...) or raw url
                match = re.search(r"https?://[^\s\)\"']+\.(?:png|jpg|jpeg|webp)", content)
                if match:
                    img_resp = await client.get(match.group(0))
                    if img_resp.status_code == 200:
                        output_path.write_bytes(img_resp.content)
                        return True
    except Exception as exc:
        log.warning("OpenRouter visual generation fallback error: %s", exc)

    return False


async def _generate_openrouter_seedance_video(
    prompt: str,
    output_path: Path,
    poll_interval: int = 10,
    timeout: int = 300,
) -> bool:
    """
    Generate an authentic AI video clip using ByteDance SeaDance on OpenRouter.
    Uses the /api/v1/videos endpoint with asynchronous polling.
    """
    if not cfg.openrouter_api_key:
        return False

    model_name = getattr(cfg, "openrouter_video_model", "bytedance/seedance-2.0-fast")
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://instagram-reel-bot.local",
    }
    payload = {
        "model": model_name,
        "prompt": (
            f"{prompt}, 9:16 vertical composition, cinematic 8k masterpiece, "
            "silent video no audio no music no sound, visuals only"
        ),
        "audio": False,  # Disable audio — we stitch our own voiceover; avoids copyright filter errors
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://openrouter.ai/api/v1/videos", headers=headers, json=payload)
            if resp.status_code not in (200, 201, 202):
                log.warning("OpenRouter SeaDance init error (%d): %s", resp.status_code, resp.text[:200])
                return False

            init_data = resp.json()
            polling_url = init_data.get("polling_url")
            gen_id = init_data.get("id")

            if not polling_url and gen_id:
                polling_url = f"https://openrouter.ai/api/v1/videos/{gen_id}"

            if not polling_url:
                log.warning("OpenRouter SeaDance response missing polling_url: %s", init_data)
                return False

            log.info("   ⏳ OpenRouter SeaDance generation started (id=%s)...", gen_id)

            elapsed = 0
            while elapsed < timeout:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                poll_resp = await client.get(polling_url, headers=headers)
                if poll_resp.status_code != 200:
                    continue

                poll_data = poll_resp.json()
                status = poll_data.get("status", "").lower()

                if status in ("completed", "succeeded", "success"):
                    # Extract video URL
                    video_url = poll_data.get("video_url") or poll_data.get("output_url")
                    if not video_url and "data" in poll_data:
                        video_url = poll_data["data"].get("video_url") or poll_data["data"].get("url")

                    if video_url:
                        vid_resp = await client.get(video_url)
                        if vid_resp.status_code == 200:
                            output_path.write_bytes(vid_resp.content)
                            log.info("   ✅ SeaDance video clip downloaded (%.1fs)", elapsed)
                            return True

                elif status in ("failed", "error"):
                    log.warning("OpenRouter SeaDance failed: %s", poll_data.get("error"))
                    return False

                log.info("   ⏳ SeaDance status: %s (%ds elapsed)...", status, elapsed)

    except Exception as exc:
        log.warning("OpenRouter SeaDance video generation error: %s", exc)

    return False


def _create_fallback_canvas(prompt: str, output_path: Path, width: int = 720, height: int = 1280) -> None:
    """Create a high-quality stylized local canvas if all remote image APIs fail."""
    img = Image.new("RGB", (width, height), color=(18, 14, 28))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        r = int(18 + (y / height) * 35)
        g = int(14 + (y / height) * 20)
        b = int(28 + (y / height) * 45)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    img.save(str(output_path), "JPEG", quality=90)


async def _fetch_scene_image(
    prompt: str,
    output_path: Path,
    scene_index: int,
) -> Path:
    """
    Fetch or generate an image for a scene with resilient multi-provider fallback:
      1. SiliconFlow FLUX (if SILICONFLOW_API_KEY is configured)
      2. Pollinations FLUX (free, fast, high quality)
      3. OpenRouter Visual Model (google/gemini-2.5-flash-image)
      4. Stylized local fallback canvas
    """
    # Stagger calls slightly to avoid bursting free APIs
    if scene_index > 0:
        await asyncio.sleep(0.5 * scene_index)

    log.info("   🎨 Scene %d: Fetching AI visual...", scene_index + 1)
    t0 = time.time()

    # Step 1: If SiliconFlow key is provided, try SiliconFlow
    if cfg.siliconflow_api_key:
        ok = await _fetch_siliconflow_image(prompt, output_path, cfg.video_width, cfg.video_height)
        if ok:
            log.info("   ✅ Scene %d image generated via SiliconFlow (%.1fs)", scene_index + 1, time.time() - t0)
            return output_path

    # Step 2: Pollinations FLUX (free, fast)
    ok = await _fetch_pollinations_image(
        prompt=prompt,
        output_path=output_path,
        width=cfg.video_width,
        height=cfg.video_height,
        seed=int(time.time() * 1000) % 100000 + scene_index,
    )
    if ok:
        log.info("   ✅ Scene %d image generated via Pollinations FLUX (%.1fs)", scene_index + 1, time.time() - t0)
        return output_path

    # Step 3: OpenRouter Visual Generator fallback
    ok = await _fetch_openrouter_image(prompt, output_path)
    if ok:
        log.info("   ✅ Scene %d visual generated via OpenRouter (%.1fs)", scene_index + 1, time.time() - t0)
        return output_path

    # Step 4: Atmospheric canvas fallback
    log.warning("   ⚠️ Remote image generation failed; using atmospheric canvas for Scene %d", scene_index + 1)
    _create_fallback_canvas(prompt, output_path, cfg.video_width, cfg.video_height)

    return output_path

# ── Image Fitting (no stretching) ─────────────────────────────────────────

def _fit_image_to_frame(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Fit an image into target dimensions WITHOUT stretching.
    Maintains the original aspect ratio and fills any empty space
    with a heavily blurred version of the image itself (cinematic look).
    """
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    # If already the correct size, return as-is
    if src_w == target_w and src_h == target_h:
        return img

    # If aspect ratios are close enough (within 5%), just resize directly
    if abs(src_ratio - target_ratio) < 0.05:
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Create blurred background: stretch original to fill frame, then blur heavily
    bg = img.resize((target_w, target_h), Image.Resampling.BILINEAR)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=40))

    # Scale the foreground image to fit inside the frame (maintain aspect ratio)
    if src_ratio > target_ratio:
        # Image is wider than frame → fit to width
        new_w = target_w
        new_h = int(target_w / src_ratio)
    else:
        # Image is taller than frame → fit to height
        new_h = target_h
        new_w = int(target_h * src_ratio)

    fg = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Center the foreground on the blurred background
    x_offset = (target_w - new_w) // 2
    y_offset = (target_h - new_h) // 2
    bg.paste(fg, (x_offset, y_offset))

    return bg


# ── Ken Burns Motion Clip Generator ───────────────────────────────────────

def _create_motion_clip_sync(
    image_path: Path,
    output_path: Path,
    duration: float = 5.0,
    motion_type: str = "zoom_in",
) -> Path:
    """
    Transform a static 9:16 vertical image into a cinematic motion video clip
    using smooth Ken Burns camera moves (zoom in, zoom out, pan left, pan right).
    """
    w, h = cfg.video_width, cfg.video_height

    # Load and normalize image — FIT to frame with blurred background (no stretching)
    base_pil = Image.open(str(image_path)).convert("RGB")
    base_pil = _fit_image_to_frame(base_pil, w, h)

    def make_frame(t: float) -> np.ndarray:
        progress = min(max(t / duration, 0.0), 1.0)

        if motion_type == "zoom_in":
            # Smoothly zoom from 1.0x to 1.15x
            scale = 1.0 + 0.15 * progress
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            left = (w - crop_w) // 2
            top = (h - crop_h) // 2

        elif motion_type == "zoom_out":
            # Smoothly zoom out from 1.15x to 1.0x
            scale = 1.15 - 0.15 * progress
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            left = (w - crop_w) // 2
            top = (h - crop_h) // 2

        elif motion_type == "pan_right":
            # 1.12x scale, slowly pan camera across from left to right
            scale = 1.12
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            max_shift = w - crop_w
            left = int(max_shift * progress)
            top = (h - crop_h) // 2

        elif motion_type == "pan_left":
            # 1.12x scale, slowly pan camera across from right to left
            scale = 1.12
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            max_shift = w - crop_w
            left = int(max_shift * (1.0 - progress))
            top = (h - crop_h) // 2

        else:
            scale = 1.05
            crop_w = int(w / scale)
            crop_h = int(h / scale)
            left = (w - crop_w) // 2
            top = (h - crop_h) // 2

        cropped = base_pil.crop((left, top, left + crop_w, top + crop_h))
        resized = cropped.resize((w, h), Image.Resampling.BILINEAR)
        return np.array(resized)

    clip = VideoClip(make_frame, duration=duration)
    clip.write_videofile(
        str(output_path),
        fps=cfg.video_fps,
        codec="libx264",
        preset="ultrafast",
        bitrate="5000k",
        logger=None,
    )
    clip.close()
    return output_path


# ── Stitch Clips with Transitions ─────────────────────────────────────────

def _stitch_clips_sync(clip_paths: list[Path], output_path: Path) -> Path:
    """Concatenate multiple video clips with smooth crossfade transitions."""
    log.info("🎬 Stitching %d scene clips with crossfade transitions...", len(clip_paths))

    clips = []
    try:
        for i, clip_path in enumerate(clip_paths):
            clip = VideoFileClip(str(clip_path))
            clip = clip.without_audio()
            clips.append(clip)

        crossfade = 0.5
        if len(clips) > 1:
            final = concatenate_videoclips(clips, method="compose", padding=-crossfade)
        else:
            final = clips[0]

        total_dur = final.duration
        log.info("   Total video duration: %.2f sec", total_dur)

        final.write_videofile(
            str(output_path),
            codec="libx264",
            fps=cfg.video_fps,
            preset="fast",
            bitrate="5000k",
            logger=None,
        )
        final.close()

        size_mb = output_path.stat().st_size / (1024 * 1024)
        log.info("✅ Final visual stitched → %s (%.2f MB, %.1fs)", output_path.name, size_mb, total_dur)
        return output_path

    finally:
        for c in clips:
            try:
                c.close()
            except Exception:
                pass


# ── Motion Video Pipeline (Primary Engine) ───────────────────────────────

async def generate_multi_scene_motion_video(
    scenes: list[dict],
    output_path: Path | None = None,
) -> Path:
    """
    Primary Visual Engine:
      1. Generates 5 high-res 9:16 FLUX AI images for each scene in parallel.
      2. Converts each image into a cinematic camera motion clip (Ken Burns zoom/pan).
      3. Stitches all clips together with crossfade transitions into a continuous 25–30s reel.
    """
    if output_path is None:
        output_path = cfg.output_dir / "video.mp4"

    run_dir = output_path.parent
    scenes_dir = run_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    log.info("🌟 Launching Cinematic Motion Engine for %d scenes...", len(scenes))

    # Dynamic camera motion styles per scene
    motion_styles = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in"]

    # Step 1: Fetch all scene images concurrently
    image_tasks = []
    for i, scene in enumerate(scenes):
        img_path = scenes_dir / f"scene_{i+1:02d}.jpg"
        image_tasks.append(_fetch_scene_image(scene["video_prompt"], img_path, i))

    image_paths = await asyncio.gather(*image_tasks)

    # Step 2: Render motion video clips
    clip_paths: list[Path] = []
    for i, img_path in enumerate(image_paths):
        clip_path = scenes_dir / f"scene_{i+1:02d}.mp4"
        style = motion_styles[i % len(motion_styles)]
        duration = float(scenes[i].get("duration_hint", 5.0) or 5.0)

        log.info("   🎥 Rendering Scene %d motion clip (%s, %.1fs)...", i + 1, style, duration)
        await asyncio.to_thread(_create_motion_clip_sync, img_path, clip_path, duration, style)
        clip_paths.append(clip_path)

    # Step 3: Stitch together with crossfade transitions
    stitched_path = await asyncio.to_thread(_stitch_clips_sync, clip_paths, output_path)
    return stitched_path


# ── Hugging Face Gradio Video (Optional Engine) ───────────────────────────

def _generate_single_clip_sync(
    prompt: str,
    negative_prompt: str,
    output_path: Path,
    scene_index: int = 0,
) -> Path:
    """Generate a single video clip from a prompt using Gradio Hugging Face Space."""
    from gradio_client import Client

    log.info("   🎬 Scene %d: Connecting to %s...", scene_index + 1, cfg.gradio_space_id)
    client = Client(
        cfg.gradio_space_id,
        token=cfg.hf_token or None,
        verbose=False,
    )

    api_info = client.view_api(return_format="dict") or {}
    named_endpoints = api_info.get("named_endpoints", {})

    full_prompt = prompt
    if negative_prompt:
        full_prompt = f"{prompt} | Negative prompt: {negative_prompt}"

    log.info("   🎬 Scene %d: Generating clip...", scene_index + 1)

    video_source_path: str | None = None

    if "/text_to_video" in named_endpoints:
        res = client.predict(
            prompt=full_prompt,
            negative_prompt=negative_prompt or "blurry, jittery, distorted, low quality",
            input_image_filepath=None,
            input_video_filepath=None,
            height_ui=cfg.video_height,
            width_ui=cfg.video_width,
            mode="text-to-video",
            duration_ui=4.0,
            ui_frames_to_use=9,
            seed_ui=42 + scene_index,
            randomize_seed=True,
            ui_guidance_scale=1.0,
            improve_texture_flag=True,
            api_name="/text_to_video",
        )
        if isinstance(res, (list, tuple)) and len(res) > 0:
            video_source_path = _extract_video_path(res[0])
        else:
            video_source_path = _extract_video_path(res)
    else:
        target_api = None
        for candidate in ["/predict", "/generate", "/text2video"]:
            if candidate in named_endpoints:
                target_api = candidate
                break

        if target_api:
            res = client.predict(full_prompt, api_name=target_api)
        else:
            res = client.predict(full_prompt)

        video_source_path = _extract_video_path(res)

    if not video_source_path or not Path(video_source_path).exists():
        raise RuntimeError(f"Unexpected response from Gradio space: {video_source_path}")

    shutil.copy2(video_source_path, output_path)
    return output_path


async def generate_multi_scene_video(
    scenes: list[dict],
    negative_prompt: str = "",
    output_path: Path | None = None,
) -> Path:
    """Generate multi-scene video via Gradio with automatic fallback to motion video."""
    if output_path is None:
        output_path = cfg.output_dir / "video.mp4"

    run_dir = output_path.parent
    scene_dir = run_dir / "scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)

    log.info("🎬 Attempting Gradio Space video generation for %d scenes...", len(scenes))

    try:
        tasks = []
        for i, scene in enumerate(scenes):
            clip_path = scene_dir / f"scene_{i+1:02d}.mp4"
            tasks.append(
                asyncio.to_thread(
                    _generate_single_clip_sync,
                    prompt=scene["video_prompt"],
                    negative_prompt=negative_prompt,
                    output_path=clip_path,
                    scene_index=i,
                )
            )

        clip_paths = await asyncio.gather(*tasks)
        return await asyncio.to_thread(_stitch_clips_sync, list(clip_paths), output_path)

    except Exception as exc:
        log.warning("⚠️ Gradio video generation failed (%s). Falling back to Motion Image Engine!", exc)
        return await generate_multi_scene_motion_video(scenes=scenes, output_path=output_path)


# ── Fast Test Video ───────────────────────────────────────────────────────

async def generate_fast_test_video(output_path: Path | None = None) -> Path:
    """Instantly provide a real 5-second vertical 9:16 video clip for fast testing."""
    if output_path is None:
        output_path = cfg.output_dir / "video.mp4"

    log.info("⚡ Fast-test video mode: getting instant 9:16 vertical video...")

    cached = cfg.output_dir / "sample_cdn.mp4"
    if cached.exists() and cached.stat().st_size > 10000:
        shutil.copy2(cached, output_path)
        log.info("✅ Fast-test video ready from local cache → %s", output_path.name)
        return output_path

    # Fallback local canvas
    _create_fallback_canvas("Fast Test", output_path.parent / "test_frame.jpg", cfg.video_width, cfg.video_height)
    await asyncio.to_thread(
        _create_motion_clip_sync,
        output_path.parent / "test_frame.jpg",
        output_path,
        duration=5.0,
        motion_type="zoom_in",
    )
    return output_path


async def generate_multi_scene_seedance_video(
    scenes: list[dict],
    output_path: Path | None = None,
) -> Path:
    """
    Hybrid Video Engine — Motion Image PRIMARY, SeaDance SECONDARY:
      1. PRIMARY: Fetches a FLUX AI image for each scene, applies Ken Burns motion (zoom/pan).
         Fast, free, no quota limits, always works.
      2. SECONDARY (optional upgrade): Attempts SeaDance AI video per scene.
         If SeaDance succeeds it replaces the motion clip for that scene.
         If it fails (copyright, timeout, etc.) the motion clip is kept silently.
      3. Stitches all clips together with smooth crossfades.
    """
    if output_path is None:
        output_path = cfg.output_dir / "video.mp4"

    run_dir = output_path.parent
    scenes_dir = run_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    log.info("🌟 Launching Hybrid Video Engine (Motion PRIMARY + SeaDance SECONDARY) for %d scenes...", len(scenes))

    motion_styles = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in"]
    clip_paths: list[Path] = []

    # Step 1: Fetch all scene images concurrently (PRIMARY — always runs)
    log.info("   🎨 Step 1/3: Fetching FLUX AI images for all scenes...")
    image_tasks = [
        _fetch_scene_image(scene["video_prompt"], scenes_dir / f"scene_{i+1:02d}.jpg", i)
        for i, scene in enumerate(scenes)
    ]
    image_paths = await asyncio.gather(*image_tasks)

    # Step 2: Render motion clips from images (PRIMARY)
    log.info("   🎥 Step 2/3: Rendering Ken Burns motion clips...")
    for i, (img_path, scene) in enumerate(zip(image_paths, scenes)):
        clip_path = scenes_dir / f"scene_{i+1:02d}.mp4"
        style = motion_styles[i % len(motion_styles)]
        duration = float(scene.get("duration_hint", 5.0) or 5.0)
        log.info("      Scene %d: %s (%.1fs)", i + 1, style, duration)
        await asyncio.to_thread(_create_motion_clip_sync, img_path, clip_path, duration, style)
        clip_paths.append(clip_path)

    # Step 3: SeaDance AI upgrade — DISABLED (re-enable by setting VISUAL_ENGINE=seedance and uncommenting)
    # log.info("   🌊 Step 3/3: Attempting SeaDance AI upgrade per scene (fails silently)...")
    # for i, scene in enumerate(scenes):
    #     clip_path = scenes_dir / f"scene_{i+1:02d}.mp4"
    #     seedance_path = scenes_dir / f"scene_{i+1:02d}_seedance.mp4"
    #     prompt = scene["video_prompt"]
    #     ok = await _generate_openrouter_seedance_video(prompt, seedance_path)
    #     if ok and seedance_path.exists():
    #         seedance_path.replace(clip_path)
    #         log.info("      ✅ Scene %d upgraded to SeaDance AI video", i + 1)
    #     else:
    #         log.info("      ⏭️  Scene %d keeping motion clip (SeaDance unavailable)", i + 1)
    #         if seedance_path.exists():
    #             seedance_path.unlink(missing_ok=True)

    stitched_path = await asyncio.to_thread(_stitch_clips_sync, clip_paths, output_path)
    return stitched_path


# ── Main Video Generator Dispatcher ──────────────────────────────────────

async def generate_video(
    scenes: list[dict] | None = None,
    negative_prompt: str = "",
    output_path: Path | None = None,
    fast_test: bool = False,
    prompt: str = "",
) -> Path:
    """
    Main visual entrypoint.
    Dispatches to the configured visual engine:

      motion_image (default) — FLUX AI images + Ken Burns camera motion. Fast, free, reliable.
      seedance               — Motion image PRIMARY + SeaDance AI as optional upgrade per scene.
      gradio                 — Hugging Face Gradio Space video generation.
    """
    if fast_test:
        return await generate_fast_test_video(output_path)

    scenes_list = scenes or [{"video_prompt": prompt or "mythological folklore legend", "duration_hint": 5.0}]

    engine = (cfg.visual_engine or "motion_image").lower().strip()

    # seedance engine is currently DISABLED — falls through to pure motion_image
    # To re-enable SeaDance, uncomment Step 3 in generate_multi_scene_seedance_video above
    if engine in ("seedance", "bytedance", "openrouter_video"):
        log.info("🔕 SeaDance engine disabled — using motion_image instead.")
        return await generate_multi_scene_motion_video(
            scenes=scenes_list,
            output_path=output_path,
        )

    if engine == "gradio":
        return await generate_multi_scene_video(
            scenes=scenes_list,
            negative_prompt=negative_prompt,
            output_path=output_path,
        )

    # Default: pure motion_image (FLUX 9:16 + Ken Burns — no SeaDance attempt)
    return await generate_multi_scene_motion_video(
        scenes=scenes_list,
        output_path=output_path,
    )
