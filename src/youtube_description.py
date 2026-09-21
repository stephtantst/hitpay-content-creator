"""AEO-optimized YouTube description generation.

Takes a freeform summary of a video (topic, talking points, transcript notes)
and produces a YouTube description in HitPay's house style, closing with a
"Learn more" link to the most relevant *published* blog post for the chosen
market.
"""
import json
import re

from config import OPENROUTER_MODEL
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient
from src.database import list_posts

BLOG_BASE_URL = "https://hitpayapp.com/blog"

# Real, always-safe canonical URLs the model MAY emit verbatim when relevant.
# Anything topic-specific (a payment-method doc, a landing page, the changelog)
# is left as a fill-in slot so the URL is human-verified before publishing —
# the tool never invents a docs/LP path.
_SAFE_LINKS = {
    "Pricing": "https://hitpayapp.com/pricing",
    "Refunds": "https://docs.hitpayapp.com/payments/refund",
    "Docs home": "https://docs.hitpayapp.com",
}
_LINK_SLOT = "[ADD VERIFIED URL]"

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


VIDEO_TYPES = ("short", "video")

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

    safe_links_str = "\n".join(f'  - {label}: {url}' for label, url in _SAFE_LINKS.items())

    example = json.dumps({
        "title": (
            "Accept WeChat Pay for subscriptions"
            if video_type == "short"
            else "HitPay Explains | Accept WeChat Pay for recurring billing in Singapore"
        ),
        "description": (
            "#HitPay #WeChatPay #Subscriptions\n\n"
            "Singapore merchants can accept WeChat Pay for recurring billing on HitPay - subscriptions, "
            "memberships, and retainers, not only one-time checkout. HitPay is a MAS-licensed payment gateway "
            "with no monthly fees.\n\n"
            "How it works:\n"
            "1. Payment Methods > WeChat Pay > Enable Recurring Payments\n"
            "2. Attach WeChat Pay to your subscription or recurring plans\n"
            "3. Customers renew with WeChat Pay without re-entering a card\n\n"
            "Who it's for: Singapore merchants selling memberships, retainers, or subscriptions to customers "
            "who prefer WeChat Pay\n"
            "Available in: Singapore only for recurring WeChat Pay\n"
            "Refunds: WeChat Pay does not support refunds\n"
            "Fee / settlement: check the pricing page for current rates before quoting\n\n"
            "Links:\n"
            "- Learn more: [URL]\n"
            "- Pricing: https://hitpayapp.com/pricing\n"
            "- Docs: [ADD VERIFIED URL]\n"
            "- Landing page: [ADD VERIFIED URL]\n"
            "- Availability by market: [ADD VERIFIED URL]\n\n"
            "#HitPay #WeChatPay #Subscriptions #Singapore #RecurringBilling"
        ),
        "source_post_slug": candidates[0]["slug"] if candidates else None,
        "source_post_title": candidates[0]["title"] if candidates else None,
    }, ensure_ascii=False, indent=2)

    return f"""You write YouTube video titles and descriptions for HitPay, a Southeast Asian payment gateway. The description must be optimized for SEO and AEO (Answer Engine Optimization): a search engine or AI assistant should be able to read the opening lines and answer "what is this about, what does HitPay do, and where is it available" without watching the video. Structure, front-loading, and accurate market scoping matter more than storytelling.

{market_line}

VIDEO INFO (freeform notes from the user — this is your ONLY source of truth for claims, quotes, and numbers):
\"\"\"
{video_info}
\"\"\"

VERIFIED HITPAY FACTS FOR THIS MARKET (safe to cite):
{_market_facts(market)}

{_TITLE_STYLE_GUIDANCE[video_type]}

DESCRIPTION STRUCTURE — follow this order for every video. Omit a section only when the video info genuinely gives nothing for it; never pad or invent to fill one.

1. TOP HASHTAGS: exactly 3 hashtags on the first line, the most important brand/product/topic tags (e.g. "#HitPay #WeChatPay #Subscriptions"). No spaces inside a tag. Blank line after.
2. FRONT-LOADED SUMMARY (the single most important line for SEO/AEO): 1–2 plain declarative sentences that state, in the first line the reader sees after the tags — before any "Show more" cut-off — exactly what the video is about AND name HitPay with its core credential for this market (from the verified facts). Lead with the specific topic and the market (e.g. "Singapore merchants can accept WeChat Pay for recurring billing on HitPay..."). This must be keyword-rich and factual, not a teaser.
3. "How it works:" followed by 2–4 numbered steps, only if the video info describes a process/setup. Keep steps concrete and in the product's own terms. Use ">" for menu paths (e.g. "Payment Methods > WeChat Pay").
4. "Who it's for:" one line naming the target merchant/use case.
5. "Available in:" one line stating the market scope EXACTLY as the facts support it. Never claim a market the video info/facts don't support (e.g. if it's Singapore-only, write "Singapore only" — do not imply Malaysia or the Philippines). This line is a factual guardrail, not marketing.
6. "Refunds:" one line — ONLY if refunds are relevant to the topic and the behaviour is stated in the video info or verified facts. Otherwise omit.
7. "Fee / settlement:" one line — ONLY if fees/settlement are relevant. Do NOT state a specific rate or settlement timing unless it appears in the video info; instead point to the pricing page ("check the pricing page for current rates before quoting"). Never fabricate a number.
8. If — and only if — the video info includes a direct quote from a named person, add it on its own line as: "Quote" - Name, Title. Never invent a quote or a speaker.
9. "Links:" followed by a bulleted list ("- Label: URL"), each with a short human-readable label (contextual anchor text, never a naked URL). Compose it like this:
   - Always include "- Learn more: [URL]" as the FIRST link. [URL] is a literal placeholder — do not substitute a real URL yourself.
   - You MAY add any of these VERIFIED canonical links verbatim when relevant to the topic:
{safe_links_str}
   - For any topic-specific resource (a payment-method doc, the changelog, a product landing page, an availability-by-market page), add a labeled line with the literal placeholder "{_LINK_SLOT}" as the URL — e.g. "- Docs: {_LINK_SLOT}". Add a slot for each such resource that genuinely fits the topic (typically 2–4). NEVER invent or guess a docs/landing-page URL.
10. BOTTOM HASHTAGS: 4–6 hashtags on the final line, expanding the top set with market and long-tail tags (e.g. "#HitPay #WeChatPay #Subscriptions #Singapore #RecurringBilling"). No spaces inside a tag.

STYLE RULES:
- Banned words: {_BANNED_WORDS}
- Banned phrases (anywhere): "in this video", "this video covers", "this video shows", "this episode", "watch as", "we'll walk through". Write about the product/merchant directly, never about the video as an object.
- Use plain hyphens ("-") for breaks, never em dashes ("—").
- No fabricated testimonials, quotes, statistics, rates, settlement timings, or market availability under any circumstance. When unsure, use a link slot or point to the pricing page rather than assert.
- Factual, specific, scannable. Section labels ("How it works:", "Who it's for:", etc.) must appear exactly as written so answer engines can parse them.
- Total length excluding the two hashtag lines: 120–260 words.

CANDIDATE PUBLISHED BLOG POSTS (pick the single most relevant one to link as "Learn more" — must copy the slug exactly as shown, or null if truly none are relevant):
{candidates_str}

OUTPUT: Raw JSON only, no markdown fences, matching this shape exactly:
{example}"""


def generate_youtube_description(
    video_info: str,
    market: str | None = None,
    brand: str = "hitpay",
    video_type: str = "video",
    is_case_study: bool = False,
    merchant_brand_name: str | None = None,
) -> dict:
    """Generate an AEO-optimized YouTube title + description.

    `video_type` is "short" or "video" and always controls the title's base
    style. `is_case_study` is an independent flag for merchant case studies,
    which get a branded title suffix on top of that base style:
      - video + case study: title is NOT model-generated — it's the fixed
        format "{merchant_brand_name} | Builders @ HitPay".
      - short + case study: the model still writes a short punchy hook, and
        "{merchant_brand_name} x HitPay" is appended after a pipe, e.g.
        "Crowded events broke their checkout | Harmony Pets x HitPay".

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

    if is_case_study and not (merchant_brand_name or "").strip():
        raise ValueError("merchant_brand_name is required for a merchant case study")

    candidates = _shortlist_candidates(video_info, market, brand)
    slug_lookup = {c["slug"]: c for c in candidates}

    prompt = _build_prompt(video_info, market, candidates, video_type)

    client = OpenRouterClient()
    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
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

    if is_case_study and video_type == "video":
        title = f"{merchant_brand_name.strip()} | Builders @ HitPay"
    elif is_case_study and video_type == "short":
        title = f"{data.get('title', '').strip()} | {merchant_brand_name.strip()} x HitPay"
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
        # No valid pick — drop the Learn More line rather than leave a dangling
        # placeholder. Handles both the plain and bulleted ("- Learn more:") forms.
        # The [ADD VERIFIED URL] slots are intentional and left untouched.
        description = re.sub(r"\n*[-•●▪👉]*\s*Learn more:\s*\[URL\]\s*", "\n", description).strip()
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
