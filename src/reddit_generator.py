"""Repurpose a blog post into a Reddit deliverable.

A Reddit deliverable is intentionally different from the X/Threads/LinkedIn
drafts. Each one is:
  1. A merchant-voice OP — a title + body written as a real SME owner in the
     target market. NO HitPay branding anywhere in the body.
  2. A SEPARATE HitPay reply comment, written in the verified-account voice
     (drier, matter-of-fact, honest about limitations).

The OP body is stored in the shared `content` column so it reuses the same
status / scheduling / audit machinery as every other platform; the title,
subreddit, and reply live in dedicated columns.

Voice rules below are lifted from the r/HitPay_official daily-post playbook.
"""
import json
import random
import re

from config import OPENROUTER_MODEL
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient

DEFAULT_SUBREDDIT = "r/HitPay_official"

_MARKET_NAMES = {
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "the Philippines",
}

# Per-market slang guidance — never mix one market's slang into another's voice.
_MARKET_SLANG = {
    "SG": "Singapore English (Singlish). Keep it mostly clean English with at most a light natural touch (an occasional 'lah'/'ah'). Do NOT sprinkle vocabulary.",
    "MY": "Malaysian English (Manglish). Keep it mostly clean English with at most a light natural touch. Do NOT sprinkle Malay words for flavour, and NEVER use Filipino words.",
    "PH": "Filipino / Taglish. Full natural Taglish is welcome and wanted (e.g. 'tapos', 'lang', 'naman', 'kasi') — but only real, natural code-switching, never forced. NEVER use Malay/Singlish words.",
}


HITPAY_REDDIT_SYSTEM_PROMPT = """You write Reddit content for HitPay, a regulated payments FinTech for SMEs in Southeast Asia (MAS-licensed SG, BNM-approved MY, BSP OPS-licensed PH). You are building the r/HitPay_official community from a cold start.

Your job: turn a HitPay blog post into ONE Reddit deliverable made of two distinct parts.

PART 1 — THE OP (title + body):
- Written entirely in the voice of a REAL SME owner/merchant in the target market. First person.
- The OP is a genuine merchant post — sharing a real operational problem/experience, or asking for input on a decision they're weighing. It is NOT a product review or a testimonial.
- DEFAULT to brand-free: describe the problem, the trade-offs, what you're comparing, or the question you still have. Do NOT resolve it into "the switch was worth it" about an unnamed provider.
- BANNED — the coy testimonial: describing an obviously-HitPay setup (no monthly fee, PayNow/GrabPay/GCash working, next-day payout, WhatsApp invoices, WeChat/Alipay settling in local currency) and calling it "the switch" or "what I moved to" WITHOUT naming it. That reads as fake astroturfing.
- If (and only if) the post genuinely narrates that the merchant already switched and it worked, NAME HitPay explicitly as what they switched to — never a coy unnamed "the switch". The merchant may or may not already be a HitPay customer; both are fine. When unsure, keep the OP in problem/question mode and let the reply do the naming.
- The endorsement and the solution are the HITPAY REPLY's job, not the OP's. Let the OP pose the situation honestly; let the reply name HitPay and answer.
- Casual and warm. Lowercase-casual is fine. Short run-ons joined with "and" are fine. Humble closer.
- LENGTH: keep the OP body to 2-3 short paragraphs MAXIMUM. Tight and readable — a real Reddit post, not an essay. Do not pad.
- Ground it in the concrete facts from the blog post: the actual payment methods, fees, timelines, and market realities — but told as lived experience, not as a listicle.
- Include an honest limitation or a moment of doubt. A specific, genuine open question the merchant still has is good (e.g. whether BNPL is actually lifting basket size).
- COLD-START RULE: the post must read as a complete post, not engagement bait. A specific real question the merchant actually has is fine; a generic "what do you all think?" / "anyone else?" is not.

PART 2 — THE HITPAY REPLY (reply_comment):
- A separate comment from the VERIFIED HitPay account, replying to the OP.
- Drier, matter-of-fact, helpful. This is where HitPay may be named — as the answer to the OP's problem, stated plainly.
- NO intro/boilerplate line ("Hi, HitPay here!"). Just start with the substance.
- Localize the payment methods and fees to the OP's market. Be honest about limitations; do NOT oversell. If HitPay genuinely isn't the best fit for part of the problem, say so.
- Only claim product facts/fees/methods that appear in the source blog post. Do not invent rates or features.

SUBREDDIT: default to r/HitPay_official.

MARKET LOCALIZATION:
- Use only payment methods real for the OP's market: SG → PayNow, cards, GrabPay, WeChat Pay/Alipay; MY → DuitNow QR, FPX, Touch 'n Go, GrabPay; PH → GCash, Maya, QR Ph, cards, BNPL (BillEase/SPayLater).
- Match the market's slang register exactly (given below). NEVER mix one market's slang into another market's post.

BANNED — never in the OP or the reply:
- "honestly" or "real talk" as openers
- em dashes (—) anywhere
- salesperson signposting ("here's the part that actually matters", "the best part is")
- SEO keyword-stuffing, tidy rhetorical arcs, hashtags
- buzzwords: seamless, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust
- HitPay/product names in the OP body (reply only)

OUTPUT FORMAT — return a single raw JSON object, no markdown fences, no preamble:
{
  "subreddit": "r/HitPay_official",
  "title": "the OP title — lowercase-casual, merchant voice, no branding",
  "content": "the OP body — merchant voice, no branding, honest limitation, stands alone",
  "reply_comment": "the separate HitPay verified-account reply — plain, honest, localized",
  "visual_note": "optional suggestion for a screenshot/image, or null"
}"""


SME_REDDIT_SYSTEM_PROMPT = """You write Reddit content repurposed from SME Growth Hub, an independent editorial resource for small-business operators in Southeast Asia.

Turn the article into ONE Reddit deliverable of two parts.

PART 1 — THE OP (title + body):
- Voice of a real SME owner/operator in the target market. First person, casual, warm, humble.
- The OP shares a real problem/experience or asks about a decision — NOT a product review.
- LENGTH: keep the OP body to 2-3 short paragraphs MAXIMUM. Tight and readable — a real Reddit post, not an essay.
- Grounded in the article's concrete facts, told as lived experience — not a listicle.
- Include an honest limitation. A specific genuine open question is fine; generic "what do you all think?" reply-fishing is not.
- No brand names in the OP body, and BANNED: the coy testimonial — describing an obviously-specific provider's setup and calling it "the switch" without naming it. Keep the OP in problem/question mode; the reply does any naming.

PART 2 — THE REPLY (reply_comment):
- A helpful peer comment replying to the OP. Practical, specific.
- HitPay may be mentioned ONLY if the article is payment-related, and only as a light peer recommendation ("for SG merchants, HitPay is the simplest starting point") — never the subject. For non-payment topics, do NOT mention HitPay; make it null or a plain peer tip.

MARKET LOCALIZATION: use only payment methods/slang real for the OP's market. NEVER mix one market's slang into another's.

BANNED: "honestly"/"real talk" openers, em dashes, salesperson signposting, hashtags, SEO stuffing, buzzwords (seamless, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust).

OUTPUT FORMAT — single raw JSON object, no fences, no preamble:
{
  "subreddit": "r/HitPay_official",
  "title": "the OP title",
  "content": "the OP body",
  "reply_comment": "peer reply, or null",
  "visual_note": "optional suggestion or null"
}"""


# ── Anti-repetition variety seed ──────────────────────────────────────────────
# Without this, generations reconverge on the same "so i run a small shop near
# <place>" opening. Each call injects a random opening style + a fresh business/
# neighbourhood persona so the OP framing varies while the blog facts stay fixed.

_OPENING_STYLES = [
    "Open MID-SCENE: a specific customer or a specific moment at your counter or on your phone. Do not start by describing your business.",
    "Open with a specific number or cost you only recently noticed when you actually sat down and looked.",
    "Open with an exact question a customer asked you that you couldn't answer well.",
    "Open with something that changed for your business in the last few weeks.",
    "Open with a small confession: something you assumed or got wrong at first.",
    "Open with a late-night comparison you were doing between options.",
    "Open with a specific physical object, like a QR sticker, a notebook, a card terminal, or a phone screen.",
    "Open with a specific day or moment (a busy Saturday, the end of the month, a slow Tuesday).",
]

_BUSINESS_TYPES = [
    "a home bakery", "a bubble tea kiosk", "a secondhand bookshop", "an indoor plant shop",
    "a small print shop", "a bike repair corner", "a vintage clothing store", "a coffee cart",
    "a nail salon", "a weekend food stall", "a stationery shop", "a barbershop",
    "a skincare brand run from home", "a pet grooming shop", "a hardware store",
    "a florist", "a small online snack brand", "a pottery studio", "a tuition centre",
    "a phone-accessories stall",
]

_NEIGHBOURHOODS = {
    "SG": ["Tiong Bahru", "Geylang", "Toa Payoh", "Katong", "Ang Mo Kio", "Tampines", "Jurong", "Bedok"],
    "MY": ["Bangsar", "SS15 Subang", "Cheras", "George Town", "Petaling Jaya", "Johor Bahru", "Ipoh", "Mont Kiara"],
    "PH": ["Cubao", "Marikina", "Cebu", "Davao", "Alabang", "Pasig", "Quezon City", "Mandaluyong"],
}

_REPLY_OPENINGS = [
    "Open the reply by answering the OP's exact problem in the first sentence, no preamble.",
    "Open by picking up the specific detail the OP mentioned, then give the concrete fact.",
    "Lead with the single most relevant number, fee, or timeline for their situation.",
    "Lead with the specific payment method that fits their market, stated plainly.",
    "Open by naming the trade-off honestly first, then how HitPay handles it.",
    "Open with the practical next step they'd actually take, then why it works.",
]


def _variety_seed(market: str) -> str:
    opening = random.choice(_OPENING_STYLES)
    reply_opening = random.choice(_REPLY_OPENINGS)
    biz = random.choice(_BUSINESS_TYPES)
    hoods = _NEIGHBOURHOODS.get((market or "").upper())
    area = random.choice(hoods) if hoods else None
    persona = f"{biz}" + (f" in/around {area}" if area else "")
    return (
        "VARIETY (make THIS post distinct from previous ones):\n"
        f"- Persona seed for this post: {persona}. Use it only if it fits the blog topic; otherwise pick a different, equally specific small business — never default to a generic 'gift and lifestyle shop'.\n"
        f"- OP opening instruction: {opening}\n"
        "- BANNED OP openers (do NOT start with any of these): 'so i run a...', 'i run a small...', 'i've been running...', 'i own a small shop near...'. Vary sentence one every time.\n"
        f"- REPLY opening instruction: {reply_opening}\n"
        "- BANNED reply openers (do NOT start the reply with any of these): 'For [market] merchants...', 'If you're a [market] business...', 'Great question', 'Hey', 'Hi'. Vary how the reply opens every time."
    )


def _build_reddit_prompt(post: dict, market: str, brand: str, from_topic: bool = False) -> str:
    market_name = _MARKET_NAMES.get((market or "").upper(), "Southeast Asia (SG/MY/PH)")
    slang = _MARKET_SLANG.get((market or "").upper(), "Plain, clean English. No forced local slang.")

    if from_topic:
        # No blog article — generate a realistic post from just a topic hint.
        topic = (post.get("title") or post.get("keyword") or "").strip()
        return f"""Write ONE Reddit deliverable (OP + reply) for a {market_name} SME merchant, about this TOPIC.

TOPIC: {topic}
TARGET MARKET: {market_name}
SLANG REGISTER FOR THIS MARKET: {slang}

{_variety_seed(market)}

There is NO source article here — construct a realistic, specific merchant scenario around the topic. Ground the OP in believable day-to-day detail for a {market_name} business. In the HitPay reply, speak generally and accurately about the relevant HitPay capability, but do NOT invent specific rates, fees, or percentages you are not sure of — keep numbers vague ("no monthly fee", "next business day") rather than precise.

Return the JSON object following the output format exactly — nothing else."""

    source_label = "SME Growth Hub article" if brand == "smegrowthhub" else "HitPay blog post"
    return f"""Repurpose the following {source_label} into ONE Reddit deliverable (OP + reply).

POST TITLE: {post.get("title", "")}
PRIMARY KEYWORD: {post.get("keyword", "")}
TARGET MARKET: {market_name}
SLANG REGISTER FOR THIS MARKET: {slang}

{_variety_seed(market)}

FULL SOURCE CONTENT:
{post.get("content", "")}

Write the OP as a real merchant in {market_name}. Keep all product facts, fees, and methods drawn only from the source content above. Return the JSON object following the output format exactly."""


def generate_reddit_post(post: dict, market: str = None, brand: str = "hitpay", from_topic: bool = False) -> dict:
    """Repurpose a blog post (or generate from a topic hint) into a Reddit OP + HitPay reply.

    Returns: {"subreddit", "title", "content", "reply_comment", "visual_note", "usage"}
    """
    market = (market or post.get("country") or "").upper() or None
    brand = brand or post.get("brand") or "hitpay"
    system = SME_REDDIT_SYSTEM_PROMPT if brand == "smegrowthhub" else HITPAY_REDDIT_SYSTEM_PROMPT

    client = OpenRouterClient()
    msg = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": _build_reddit_prompt(post, market, brand, from_topic=from_topic)}],
    )

    raw = (msg.content[0].text if msg.content else "").strip()
    if not raw:
        raise ValueError("Reddit generation returned an empty response — please try again")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    else:
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(raw))
        except Exception as e:
            snippet = raw[:200].replace("\n", " ")
            raise ValueError(f"Could not parse Reddit response ({e}). Model returned: {snippet!r}")

    content = (data.get("content") or "").strip()
    if not content:
        raise ValueError("Reddit generation returned empty OP body")

    return {
        "subreddit": (data.get("subreddit") or DEFAULT_SUBREDDIT).strip() or DEFAULT_SUBREDDIT,
        "title": (data.get("title") or "").strip(),
        "content": content,
        "reply_comment": (data.get("reply_comment") or None),
        "visual_note": data.get("visual_note"),
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }
