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
import re

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.generator import _messages_create_with_retry

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
- NO HitPay branding in the body. Do NOT name HitPay, do NOT name any product, do NOT pitch. The OP is a merchant telling a small, true-feeling story or asking a genuine question rooted in a real operational problem.
- Casual and warm. Lowercase-casual is fine. Short run-ons joined with "and" are fine. Humble closer.
- LENGTH: keep the OP body to 2-3 short paragraphs MAXIMUM. Tight and readable — a real Reddit post, not an essay. Do not pad.
- Ground it in the concrete facts from the blog post: the actual payment methods, fees, timelines, and market realities — but told as lived experience, not as a listicle.
- Include an honest limitation or a moment of doubt. Real merchants are not sure of everything.
- COLD-START RULE: the post must stand on its own. Do NOT end on an open-ended "what do you all think?" / "anyone else?" fishing-for-replies question. Land on the merchant's own experience or a quiet resolution.

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
- LENGTH: keep the OP body to 2-3 short paragraphs MAXIMUM. Tight and readable — a real Reddit post, not an essay.
- Grounded in the article's concrete facts, told as lived experience — not a listicle.
- Include an honest limitation. The post must stand alone (no open-ended reply-fishing).
- No brand names in the body.

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


def _build_reddit_prompt(post: dict, market: str, brand: str) -> str:
    market_name = _MARKET_NAMES.get((market or "").upper(), "Southeast Asia (SG/MY/PH)")
    slang = _MARKET_SLANG.get((market or "").upper(), "Plain, clean English. No forced local slang.")
    source_label = "SME Growth Hub article" if brand == "smegrowthhub" else "HitPay blog post"
    return f"""Repurpose the following {source_label} into ONE Reddit deliverable (OP + reply).

POST TITLE: {post.get("title", "")}
PRIMARY KEYWORD: {post.get("keyword", "")}
TARGET MARKET: {market_name}
SLANG REGISTER FOR THIS MARKET: {slang}

FULL SOURCE CONTENT:
{post.get("content", "")}

Write the OP as a real merchant in {market_name}. Keep all product facts, fees, and methods drawn only from the source content above. Return the JSON object following the output format exactly."""


def generate_reddit_post(post: dict, market: str = None, brand: str = "hitpay") -> dict:
    """Repurpose a blog post into a Reddit OP + HitPay reply.

    Returns: {"subreddit", "title", "content", "reply_comment", "visual_note", "usage"}
    """
    market = (market or post.get("country") or "").upper() or None
    brand = brand or post.get("brand") or "hitpay"
    system = SME_REDDIT_SYSTEM_PROMPT if brand == "smegrowthhub" else HITPAY_REDDIT_SYSTEM_PROMPT

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": _build_reddit_prompt(post, market, brand)}],
    )

    raw = msg.content[0].text.strip()
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
            raise ValueError(f"Could not parse Reddit response: {e}")

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
