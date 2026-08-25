"""Generate an Instagram caption from a few prompts + an optional photo.

Lean, stateless generator (no DB, no scheduling): the frontend sends keywords /
a topic, a market, and optionally a photo. Claude returns a ready-to-post caption
plus hashtags. The photo is passed straight to Claude's vision API as base64
*in the request* and is never written to disk or Vercel Blob — it exists only for
the duration of the call. Nothing about the image is stored.
"""
import json
import re
from pathlib import Path

from config import OPENROUTER_MODEL
from src.brand_config import get_brand_config
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient

_MARKET_NAMES = {
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "the Philippines",
}

# Vision formats Claude accepts. Kept in sync with the frontend accept="" list.
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

_BANNED = (
    "seamless, empower, innovate, frictionless, cutting-edge, robust, "
    "unlock, game-changer, revolutionary, elevate, supercharge"
)


def _brand_voice_snippet(brand: str) -> str:
    """Small on-brand voice reference, read from the repo's brand files."""
    cfg = get_brand_config(brand)
    candidates = (
        ["hitpay_brand_guidelines.md"] if brand == "hitpay"
        else ["smegrowthhub_writing_philosophy.md"]
    )
    repo_root = Path(__file__).resolve().parent.parent
    for name in candidates:
        p = repo_root / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
            # Keep it short — just enough to anchor voice, not blow up tokens.
            return f"{cfg.name} voice reference:\n{text[:2500]}"
    return f"Brand: {cfg.name}"


def _system_prompt(brand: str, market: str | None) -> str:
    market_line = ""
    if market in _MARKET_NAMES:
        market_line = (
            f"\nThis post is for the {_MARKET_NAMES[market]} ({market}) audience. "
            f"Use payment methods, currency, and references that fit that market."
        )
    return f"""You are the social writer for {get_brand_config(brand).name}, writing Instagram captions for small business owners.

{_brand_voice_snippet(brand)}
{market_line}

Instagram caption rules:
- Open with a strong first line (it's the hook shown before "more").
- Keep it scannable: short lines / small paragraphs, tasteful line breaks.
- Warm, human, concrete. Speak to real SME owners, not "businesses".
- A few relevant emojis are fine (Instagram-native), but don't overdo it.
- End with a light call to action.
- Do NOT use these buzzwords: {_BANNED}.
- No invented stats, rates, or claims.

Return ONLY valid JSON, no code fences, in this exact shape:
{{"caption": "the full caption text with line breaks", "hashtags": ["#tag1", "#tag2", ...]}}
Provide 8-15 relevant, specific hashtags (mix broad + niche + local). Do not put the hashtags inside the caption field."""


def _user_content(keywords: str, has_image: bool, image_b64: str | None, media_type: str | None):
    """Build the messages[].content for the caption request."""
    parts = []
    if has_image and image_b64:
        parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type or "image/jpeg",
                "data": image_b64,
            },
        })
        parts.append({
            "type": "text",
            "text": (
                "Write an Instagram caption that fits the attached photo — reference what's "
                "actually in the image so the caption and picture feel like one post.\n\n"
                f"Topic / keywords to work in: {keywords or '(none given — take the lead from the photo)'}"
            ),
        })
    else:
        parts.append({
            "type": "text",
            "text": f"Write an Instagram caption about: {keywords}",
        })
    return parts


def _parse(raw: str) -> dict:
    """Parse the model's JSON, tolerating stray code fences."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: treat the whole thing as the caption, pull any #hashtags out.
        tags = re.findall(r"#\w+", cleaned)
        caption = re.sub(r"#\w+", "", cleaned).strip()
        return {"caption": caption or cleaned, "hashtags": tags}
    caption = (data.get("caption") or "").strip()
    hashtags = [h if h.startswith("#") else f"#{h}" for h in (data.get("hashtags") or []) if h]
    return {"caption": caption, "hashtags": hashtags}


def generate_instagram_caption(
    keywords: str = "",
    market: str | None = None,
    brand: str = "hitpay",
    image_b64: str | None = None,
    image_media_type: str | None = None,
) -> dict:
    """Generate an Instagram caption (+ hashtags) from keywords and an optional photo.

    Args:
        keywords: topic / keywords / a short brief. Optional if a photo is given.
        market: "SG" | "MY" | "PH" | None.
        brand: "hitpay" | "smegrowthhub".
        image_b64: optional base64-encoded image data (no data: prefix). In-memory only.
        image_media_type: e.g. "image/jpeg". Required if image_b64 is set.

    Returns:
        {"caption": str, "hashtags": list[str], "full": str}
        `full` is caption + a blank line + the hashtags joined by spaces — the
        one-tap "copy the whole thing" string.
    """
    keywords = (keywords or "").strip()
    has_image = bool(image_b64)
    if not keywords and not has_image:
        raise ValueError("Give some keywords/a topic, or upload a photo.")

    client = OpenRouterClient()
    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=1500,
        system=_system_prompt(brand, market),
        messages=[{
            "role": "user",
            "content": _user_content(keywords, has_image, image_b64, image_media_type),
        }],
    )
    result = _parse(response.content[0].text)
    tags_line = " ".join(result["hashtags"])
    result["full"] = (result["caption"] + ("\n\n" + tags_line if tags_line else "")).strip()
    return result
