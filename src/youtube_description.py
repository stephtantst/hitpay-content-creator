"""AEO-optimized YouTube description generation.

Takes a freeform summary of a video (topic, talking points, transcript notes)
and produces a YouTube description in HitPay's house style, closing with a
"Learn more" link to the most relevant *published* blog post for the chosen
market.
"""
import json
import re

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.generator import _messages_create_with_retry
from src.database import list_posts

BLOG_BASE_URL = "https://hitpayapp.com/blog"

_BANNED_WORDS = (
    "seamlessly, unlock, revolutionise, revolutionize, game-changer, cutting-edge, "
    "empower, leverage, utilise, utilize, transformative, innovative, robust"
)

_MARKET_FACTS = {
    "SG": (
        "HitPay is a licensed payment institution regulated by the Monetary Authority of Singapore (MAS). "
        "50+ payment methods including PayNow, GrabPay, ShopeePay, cards. No monthly fees."
    ),
    "MY": (
        "HitPay is approved by Bank Negara Malaysia (BNM) as a registered merchant acquirer and approved "
        "money service business agent. 30+ payment methods including DuitNow QR, FPX, Touch 'n Go, GrabPay, cards. "
        "No monthly fees."
    ),
    "PH": (
        "HitPay is a registered operator of a payment system (OPS) regulated under the Bangko Sentral ng Pilipinas "
        "(BSP). 30+ payment methods including GCash, Maya, QR Ph, cards. No monthly fees. "
        "HitPay is Singapore-headquartered, serving the Philippines."
    ),
    None: (
        "HitPay is a Singapore-headquartered payment gateway serving 20,000+ businesses across Southeast Asia "
        "(Singapore, Malaysia, Philippines). No monthly fees, 10+ countries supported."
    ),
}


def _market_facts(market: str | None) -> str:
    return _MARKET_FACTS.get(market, _MARKET_FACTS[None])


def _score_post(terms: list[str], post: dict) -> int:
    haystack = " ".join([
        post.get("title") or "",
        post.get("meta_description") or "",
        post.get("overview") or "",
        post.get("keyword") or "",
        post.get("tags") or "",
    ]).lower()
    return sum(haystack.count(t) for t in terms)


def _shortlist_candidates(video_info: str, market: str | None, brand: str, limit: int = 15) -> list[dict]:
    """Return up to `limit` published posts most likely relevant to video_info.

    Filters by market when given (a market's own posts plus market-agnostic
    ones); falls back to the full published set if that yields too few
    candidates, so a Learn More link can (almost) always be produced.
    """
    published = list_posts(status="published", brand=brand)

    if market:
        candidates = [p for p in published if (p.get("country") or "") in (market, "", "SEA")]
        if len(candidates) < 5:
            candidates = published
    else:
        candidates = published

    if not candidates:
        return []

    terms = [t.lower() for t in re.split(r"\W+", video_info) if len(t) > 2]
    scored = sorted(candidates, key=lambda p: _score_post(terms, p), reverse=True)
    return scored[:limit]


VIDEO_TYPES = ("short", "video", "merchant_case_study")

_TITLE_STYLE_GUIDANCE = {
    "short": (
        "TITLE STYLE — YouTube Short:\n"
        "Write a short, punchy title (roughly 3–8 words) capturing a single hook, insight, or quote from the "
        "video info — the kind of line that makes someone stop scrolling. Sentence case (capitalize only the "
        "first word and proper nouns). No branding, no pipes, no hashtags, no emoji. "
        'Examples of the style (invented, do not reuse verbatim): "Setup took us 10 minutes", '
        '"The problem with manual invoicing", "Why merchants are dropping cash-only".'
    ),
    "video": (
        "TITLE STYLE — long-form YouTube video:\n"
        'Write a title in the format "{Series/Topic} | {Descriptive clause}" — a short topic or series tag, '
        "a pipe character, then a plain-language clause describing what the video covers. Sentence case. "
        'Examples of the style (invented, do not reuse verbatim): "HitPay Explains | How QR payments work in '
        'Southeast Asia", "Merchant Playbook | Setting up PayNow in under 2 minutes".'
    ),
}


def _build_prompt(video_info: str, market: str | None, candidates: list[dict], video_type: str) -> str:
    market_line = {
        "SG": "This video targets Singapore merchants.",
        "MY": "This video targets Malaysia merchants.",
        "PH": "This video targets the Philippines merchants.",
    }.get(market, "This video targets merchants across Southeast Asia broadly (no single market).")

    candidates_str = "\n".join(
        f'  - slug: "{c["slug"]}" | title: "{c["title"]}" | summary: "{(c.get("meta_description") or c.get("overview") or "")[:160]}"'
        for c in candidates
    ) or "  (none available)"

    example = json.dumps({
        "title": (
            "Setup took us 10 minutes"
            if video_type == "short"
            else "HitPay Explains | Collecting payments faster with PayNow"
        ),
        "description": (
            "💳 Manual invoicing. Chasing payments. No real-time visibility.\n\n"
            "In this video, we break down how HitPay helps Southeast Asian merchants collect payments faster "
            "with PayNow, GrabPay, and cards — all from one dashboard.\n\n"
            "What you'll learn:\n"
            "✅ How to set up a payment link in under 2 minutes\n"
            "💰 How to accept PayNow, GrabPay, and cards with no monthly fees\n"
            "📊 How to track every transaction from one dashboard\n\n"
            "HitPay is a MAS-licensed payment gateway serving 20,000+ businesses across Southeast Asia.\n\n"
            "👉 Learn more: [URL]\n\n"
            "#HitPay #PaymentGateway #Singapore #SME #PayNow"
        ),
        "source_post_slug": candidates[0]["slug"] if candidates else None,
        "source_post_title": candidates[0]["title"] if candidates else None,
    }, ensure_ascii=False, indent=2)

    return f"""You write YouTube video titles and descriptions for HitPay, a Southeast Asian payment gateway. The description must be AEO-optimized (Answer Engine Optimized): the opening lines should let an AI assistant or search engine understand exactly what the video is about and what HitPay offers, without needing to watch it.

{market_line}

VIDEO INFO (freeform notes from the user — this is your ONLY source of truth for claims, quotes, and numbers):
\"\"\"
{video_info}
\"\"\"

VERIFIED HITPAY FACTS FOR THIS MARKET (safe to cite):
{_market_facts(market)}

{_TITLE_STYLE_GUIDANCE[video_type]}

DESCRIPTION STRUCTURE (adapt to what the video info actually supports — do not force sections that don't fit):
1. A short 1–2 line hook naming the problem/pain point, emoji-led (1 emoji is enough).
2. A short paragraph giving context: what the video covers / who it's for.
3. If — and only if — the video info includes a direct quote from a named person, include it as: "Quote" – Name, Title. Otherwise, skip the quote entirely. NEVER invent a quote or a speaker.
4. A short bulleted list (emoji-led: ✅ 💰 📊 ⏱️ 🔁 etc.) of the concrete points, features, or results covered in the video. Only include specific numbers/stats/results that appear in the video info, or the verified facts above — never fabricate a statistic.
5. One closing sentence tying it back to the value proposition.
6. A line reading exactly: "👉 Learn more: [URL]" — [URL] is a literal placeholder, do not substitute a real URL yourself.
7. 4–6 relevant hashtags, no spaces, mixing brand/product/market tags (e.g. #HitPay #PayNow #Singapore).

STYLE RULES:
- Banned words: {_BANNED_WORDS}
- No fabricated testimonials, quotes, or statistics under any circumstance
- Factual, specific, concrete — not hype
- Total description length: 150–300 words

CANDIDATE PUBLISHED BLOG POSTS (pick the single most relevant one to link as "Learn more" — must copy the slug exactly as shown, or null if truly none are relevant):
{candidates_str}

OUTPUT: Raw JSON only, no markdown fences, matching this shape exactly:
{example}"""


def generate_youtube_description(
    video_info: str,
    market: str | None = None,
    brand: str = "hitpay",
    video_type: str = "video",
    merchant_brand_name: str | None = None,
) -> dict:
    """Generate an AEO-optimized YouTube title + description.

    `video_type` is one of "short", "video", or "merchant_case_study". For
    "merchant_case_study" the title is not generated by the model — it's the
    fixed HitPay format "{merchant_brand_name} | Builders @ HitPay" — while the
    description is still generated normally.

    Returns a dict: {title, description, source_post_slug, source_post_title, source_post_url, market, video_type}
    """
    if not video_info or not video_info.strip():
        raise ValueError("video_info is required")

    market = market or None
    if market not in (None, "SG", "MY", "PH"):
        raise ValueError(f"Unsupported market: {market}")

    video_type = video_type or "video"
    if video_type not in VIDEO_TYPES:
        raise ValueError(f"Unsupported video_type: {video_type}")

    if video_type == "merchant_case_study" and not (merchant_brand_name or "").strip():
        raise ValueError("merchant_brand_name is required for merchant_case_study")

    candidates = _shortlist_candidates(video_info, market, brand)
    slug_lookup = {c["slug"]: c for c in candidates}

    # Merchant case studies use a fixed title format, so the model only needs to
    # draft the description — reuse the "video" title style guidance for that call.
    prompt = _build_prompt(video_info, market, candidates, "video" if video_type == "merchant_case_study" else video_type)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        from json_repair import repair_json
        data = json.loads(repair_json(raw_text))

    if video_type == "merchant_case_study":
        title = f"{merchant_brand_name.strip()} | Builders @ HitPay"
    else:
        title = data.get("title", "").strip()

    description = data.get("description", "").strip()
    chosen_slug = data.get("source_post_slug")
    chosen = slug_lookup.get(chosen_slug)

    if chosen:
        url = f"{BLOG_BASE_URL}/{chosen['slug']}"
        description = description.replace("[URL]", url)
        source_post_slug = chosen["slug"]
        source_post_title = chosen["title"]
        source_post_id = chosen.get("id")
    else:
        # No valid pick — drop the Learn More line rather than leave a dangling placeholder.
        description = re.sub(r"\n*👉?\s*Learn more:\s*\[URL\]\n*", "\n", description).strip()
        url = None
        source_post_slug = None
        source_post_title = None
        source_post_id = None

    return {
        "title": title,
        "description": description,
        "source_post_id": source_post_id,
        "source_post_slug": source_post_slug,
        "source_post_title": source_post_title,
        "source_post_url": url,
        "market": market,
        "video_type": video_type,
    }
