"""
brain.py — Step 1: AI Content Planner (OpenRouter → GPT-4o-mini)

Queries the LLM for a structured JSON execution plan containing:
  • caption, hashtags, voiceover_text, video_prompt, negative_prompt
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

import httpx

from .config import cfg
from .logger import log

# ── Structured output schema ────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a master storyteller and visual director specializing in short, viral World Folklore, Myths, and Ancient Legends for Instagram Reels.

Your mission is to uncover fascinating, little-known or iconic folk stories, mythical creatures, and ancient tales from diverse cultures across the globe (e.g. Japanese Yokai, Celtic fairy lore, Nordic sagas, West African Anansi tales, Slavic forest spirits, Mayan legends, Persian folklore, Polynesian myths, etc.). Donot limit to just specific country. you can also make various parts and leave the video at a cliffhanger so that user will search for other parts.

IMPORTANT — You must produce MULTI-SCENE visual storytelling. Each reel consists of 4–5 cinematic scenes that progress through the story visually, creating a dynamic, immersive experience (NOT a single static image repeated).

Your SOLE job is to produce a valid JSON object — no prose, no markdown fences, ONLY raw JSON:

{
  "caption":         "<engaging caption introducing the folk story, country of origin, and an interactive question to drive comments>",
  "hashtags":        ["#Folklore", "#Mythology", "#<Culture>Folklore", "#WorldLegends", "#ShortStories", "#Storytime", "#FairyTales", "#AncientMyths", "#ViralReels"],
  "voiceover_text":  "<captivating 25–30 second spoken voiceover (approx. 60–80 words). Must hook immediately, build tension, and end with a twist or emotional beat>",
  "scenes": [
    {
      "description": "<what is happening in this scene narratively>",
      "video_prompt": "<hyper-detailed cinematic video prompt: specific camera angle, lighting, environment details, character actions/expressions, atmosphere, color palette. Must feel like a different shot, not the same scene repeated. 9:16 vertical composition>",
      "duration_hint": 5
    }
  ],
  "negative_prompt": "<worst quality, blurry, distorted faces, extra limbs, ugly, text overlays, subtitles, watermark, static image, frozen, no motion>"
}

Storytelling & Visual Rules:
1. Every reel must feature a REAL or legendary folk story from a specific culture or country.
2. Hook formula: Start voiceover with the origin ("In ancient {country/culture}...", "According to {culture} lore...", "Deep in the {culture} forests..."), Did you know....
3. Voiceover length: 60 to 80 words (takes 25–30 seconds when read aloud). Tell a COMPLETE mini-story with setup → tension → payoff.
4. You MUST generate exactly 5 scenes. Each scene should be a DIFFERENT visual moment:
   - Scene 1: ESTABLISHING SHOT — wide landscape/setting that draws the viewer in (e.g. misty mountains, ancient forest at twilight, moonlit village)
   - Scene 2: CHARACTER INTRODUCTION — introduce the protagonist or mythical creature with a medium shot (e.g. a young warrior standing at a crossroads, a glowing fox spirit emerging from shadows)
   - Scene 3: RISING ACTION — dramatic moment building tension (e.g. a storm gathering, eyes glowing in darkness, ancient door creaking open)
   - Scene 4: CLIMAX — the most visually intense moment (e.g. transformation, battle, magical explosion, revelation)
   - Scene 5: RESOLUTION — emotional closing shot (e.g. sunrise over ruins, spirit fading into mist, character walking into distance)
5. Each scene's video_prompt must describe a DISTINCT visual with specific details:
   - Camera angle (wide shot, close-up, bird's eye, low angle, tracking shot)
   - Lighting (golden hour, moonlight, firelight, bioluminescent glow, dramatic shadows)
   - Motion/action (wind blowing, water flowing, character walking, leaves falling, fire crackling)
   - Color palette (warm amber, cool blue-silver, deep crimson, ethereal green)
6. duration_hint for each scene should be 5 or 6 seconds. Total across all scenes: 25–30 seconds.
7. Return ONLY the raw JSON object.
"""

USER_PROMPT = (
    "Select a fascinating, unique folk story or mythological legend from anywhere in the world. "
    "Craft a captivating 25-30 second mini-story with 5 breathtaking cinematic scene descriptions that progress through the narrative. "
    "Each scene must feel visually DIFFERENT — different camera angles, lighting, and subjects. "
    "Return the raw JSON now."
)


@dataclass
class ScenePlan:
    """A single visual scene within the reel."""
    description: str
    video_prompt: str
    duration_hint: int  # seconds


@dataclass
class ReelPlan:
    """Validated multi-scene execution plan produced by the LLM."""
    caption: str
    hashtags: List[str]
    voiceover_text: str
    scenes: List[ScenePlan]
    negative_prompt: str


async def generate_reel_plan() -> ReelPlan:
    """Call OpenRouter and return a validated multi-scene ReelPlan."""
    log.info("🧠 Generating reel plan via OpenRouter (%s)…", cfg.openrouter_model)

    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://instagram-reel-bot.local",
        "X-Title": "Instagram Reel Bot",
    }
    payload = {
        "model": cfg.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "temperature": 0.9,
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]
    log.debug("Raw LLM response:\n%s", raw)

    # ── Parse & validate ─────────────────────────────────────────────────
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try stripping markdown fences the model might sneak in
        cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(cleaned)  # let it raise if still broken

    required = {"caption", "hashtags", "voiceover_text", "scenes", "negative_prompt"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    # Parse scenes
    raw_scenes = data["scenes"]
    if not isinstance(raw_scenes, list) or len(raw_scenes) < 2:
        raise ValueError(f"Expected at least 2 scenes, got: {raw_scenes}")

    scenes = []
    for i, s in enumerate(raw_scenes):
        if not isinstance(s, dict) or "video_prompt" not in s:
            raise ValueError(f"Scene {i} missing 'video_prompt': {s}")
        scenes.append(ScenePlan(
            description=s.get("description", f"Scene {i+1}"),
            video_prompt=s["video_prompt"],
            duration_hint=int(s.get("duration_hint", 5)),
        ))

    plan = ReelPlan(
        caption=data["caption"],
        hashtags=data["hashtags"],
        voiceover_text=data["voiceover_text"],
        scenes=scenes,
        negative_prompt=data["negative_prompt"],
    )

    log.info(
        "✅ Reel plan ready — %d scenes, ~%d sec — caption: %s…",
        len(plan.scenes),
        sum(s.duration_hint for s in plan.scenes),
        plan.caption[:60],
    )
    for i, s in enumerate(plan.scenes):
        log.info("   🎬 Scene %d: %s", i + 1, s.description[:80])
    return plan
