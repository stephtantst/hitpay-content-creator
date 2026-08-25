import json
import re

from config import OPENROUTER_MODEL
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient
from src.mcp_client import get_changelog
from src.thought_leadership import _fetch_live_blog_slugs

_FALLBACK_URL = "https://hitpayapp.com/blog/hitpay-rates"

_STYLE_RULES = """STYLE RULES:
- No hashtags, no @ mentions
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust
- Keep it factual and specific — what the feature does, not how amazing it is
- Product names should match HitPay's own terminology exactly"""


def _clean_entry_title(raw: str) -> str:
    """Strip the boilerplate suffix HitPay's changelog MCP appends to titles."""
    for sep in (" - HitPay Changelog |", " – HitPay Changelog |", " | HitPay Changelog"):
        if sep in raw:
            return raw.split(sep)[0].strip()
    return raw.strip()


def _slug_to_label(url: str) -> str:
    """Turn a changelog URL slug into a readable phrase."""
    slug = url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").replace("_", " ").title()


def _extract_changelog_text(mcp_result: dict) -> str:
    """Extract readable text from MCP changelog result."""
    if not mcp_result:
        return ""

    raw_text = None
    if "content" in mcp_result:
        for item in mcp_result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                raw_text = item["text"]
                break
    elif "text" in mcp_result:
        raw_text = mcp_result["text"]

    # If the text is a JSON array of entry objects (HitPay MCP format), format them
    if raw_text:
        try:
            entries = json.loads(raw_text)
            if isinstance(entries, list):
                lines = []
                for e in entries:
                    if not isinstance(e, dict):
                        lines.append(str(e))
                        continue
                    title = _clean_entry_title(e.get("title", ""))
                    if not title:
                        title = _slug_to_label(e.get("url", ""))
                    date = e.get("date") or ""
                    url = e.get("url", "")
                    line = f"• {title}"
                    if date:
                        line += f" ({date})"
                    if url:
                        line += f" — {url}"
                    lines.append(line)
                return "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            pass
        return raw_text

    # Structured entries list
    if "entries" in mcp_result:
        lines = []
        for e in mcp_result["entries"]:
            if isinstance(e, dict):
                title = _clean_entry_title(e.get("title", ""))
                date = e.get("date", "")
                desc = e.get("description", e.get("content", ""))
                lines.append(f"• {date} — {title}: {desc}".strip("— "))
            else:
                lines.append(str(e))
        return "\n".join(lines)

    return json.dumps(mcp_result)


def _count_entries(changelog_text: str, limit: int) -> int:
    count = changelog_text.count("•")
    if count > 0:
        return count
    count = sum(1 for line in changelog_text.splitlines() if line.strip())
    return count if count > 0 else limit


def _market_ctx_x(market: str | None) -> str:
    if market == "SG":
        return "Frame updates in terms of Singapore merchants — PayNow, SGQR, MAS-licensed context."
    if market == "MY":
        return "Frame updates in terms of Malaysia merchants — DuitNow, FPX, BNM-approved context."
    if market == "PH":
        return "Frame updates in terms of Philippines merchants — GCash, QR Ph, BSP OPS-licensed context."
    return ""


def _market_ctx_threads(market: str | None) -> str:
    if market == "SG":
        return "Frame in Singapore context — PayNow, hawker culture, MAS-licensed platform."
    if market == "MY":
        return "Frame in Malaysia context — DuitNow, FPX, BNM-approved platform."
    if market == "PH":
        return "Frame in Philippines context — GCash, QR Ph, BSP OPS-licensed platform."
    return "Frame for Southeast Asia broadly — Singapore, Malaysia, Philippines merchants."


def _build_x_roundup_prompt(changelog_text: str, market: str | None, n_entries: int) -> str:
    slugs = _fetch_live_blog_slugs()
    slugs_str = "\n".join(f"  {s}" for s in slugs)
    thread_size = min(max(3, n_entries), 7)
    market_note = _market_ctx_x(market)

    example = json.dumps({
        "topic": "HitPay product updates",
        "tweets": [
            "1/4 A few things we shipped recently that are now live for all merchants. 🧵",
            "2/4 Payment links now support instalment scheduling — split a S$1,200 invoice across 3 months, "
            "no manual follow-up. Clients get a link, you get paid in stages.",
            "3/4 Borderless QR now works offline. Merchant shows the QR, customer scans with WeChat Pay or Alipay. "
            "Transaction queues and clears when connectivity returns.",
            "4/4 Full changelog: [URL]",
        ],
        "link_url": "https://hitpayapp.com/blog/hitpay-rates",
        "visual_note": None,
    }, ensure_ascii=False)

    return f"""You are the @hitpay_app content writer. Write a {thread_size}-tweet thread summarising recent HitPay product updates.

BRAND: HitPay — MAS-licensed (SG), BNM-approved (MY), BSP OPS-licensed (PH). No monthly fees. 50+ payment methods.
TONE: Product-focused, clear, honest. Not hype — the kind of update a founder sends to their team.
AUDIENCE: Merchants and SME owners in Southeast Asia.
{market_note}

CHANGELOG ENTRIES:
{changelog_text}

CONTENT FORMAT:
- Tweet 1: Short opener — "here's what we shipped recently" tone. Number it 1/{thread_size}. End with 🧵
- Middle tweets: One tweet per major feature/update. Concrete — what it does, not how great it is. Number each.
- Last tweet: Changelog link + [URL] as a literal placeholder. Keep it minimal.
- Each tweet: 180–280 chars.

{_STYLE_RULES}

LINK URL RULE:
Set link_url to https://hitpayapp.com/blog/{{slug}} for the most relevant article, or hitpay-rates if none fits.

LIVE BLOG SLUGS:
{slugs_str}

OUTPUT: Raw JSON only — no markdown fences.
{example}

IMPORTANT: [URL] in the final tweet is a literal placeholder — never substitute the real URL."""


def _build_x_individual_prompt(changelog_text: str, market: str | None) -> str:
    market_note = _market_ctx_x(market)

    example = json.dumps([
        {
            "title": "Instalment scheduling for payment links",
            "tweet": "Payment links now support instalment scheduling — split an invoice into monthly payments, "
                     "no manual tracking. The link handles reminders and collection automatically. [URL]",
            "link_url": "https://hitpayapp.com/blog/payment-link",
        }
    ], ensure_ascii=False)

    return f"""You are the @hitpay_app content writer. For each changelog entry below, write a single standalone announcement tweet.

BRAND: HitPay — payment platform for SMEs in Southeast Asia.
TONE: Clear product announcement. Like a founder posting to their followers — factual, not promotional.
{market_note}

CHANGELOG ENTRIES:
{changelog_text}

INSTRUCTIONS:
- Return a JSON array — one object per changelog entry.
- Each object: {{"title": "<entry title>", "tweet": "<announcement text>", "link_url": "<url or null>"}}
- Tweet: 180–280 chars. What the feature does + [URL] at the end if a blog link is relevant.
- [URL] is a literal placeholder — do not substitute a real URL into the tweet text.
- link_url: set to https://hitpayapp.com/blog/hitpay-rates or a relevant slug, or null.
- Do NOT combine multiple entries into one tweet. One entry = one tweet.

{_STYLE_RULES}

OUTPUT: Raw JSON array only — no markdown fences.
{example}"""


def _build_threads_roundup_prompt(changelog_text: str, market: str | None, n_entries: int) -> str:
    mkt_ctx = _market_ctx_threads(market)
    thread_size = min(max(2, n_entries // 2), 5)

    if thread_size == 1:
        fmt = "Single post: a brief, human summary of what shipped recently. 200–450 chars. No numbering."
    else:
        fmt = (
            f"{thread_size}-part thread:\n"
            f"Post 1 — Opener: 'Here's what we shipped recently.' Warm, not stiff. End with 🧵\n"
            f"Middle posts: one post per 1–2 features. Concrete — what it does, who it helps. 200–450 chars each.\n"
            f"Final post — Close: point to the full changelog. End with [URL] as a literal placeholder."
        )

    example = json.dumps({
        "topic": "recent HitPay updates",
        "posts": [
            "A few things we shipped recently that are now live for all merchants. 🧵",
            "Payment links now support instalment scheduling. Split a S$1,200 invoice into 3 monthly payments "
            "— the link handles reminders and collection automatically. No chasing.",
            "Full changelog: [URL]",
        ],
        "link_url": "https://hitpayapp.com/blog/hitpay-rates",
    }, ensure_ascii=False)

    return f"""Write a HitPay Threads update thread summarising recent product changes.

VOICE: Warm, direct, like a founder updating their community. Specific and functional — no superlatives.
{mkt_ctx}

CHANGELOG ENTRIES:
{changelog_text}

FORMAT:
{fmt}

No hashtags. No emojis except 🧵 noted above. No marketing buzzwords (seamless, empower, innovative, robust, cutting-edge).

LINK URL RULE:
Set link_url to https://hitpayapp.com/blog/{{slug}} for the most relevant article, or hitpay-rates if none fits.

Return raw JSON only — no markdown fences:
{example}

IMPORTANT: [URL] is a literal placeholder — never substitute a real URL into post text."""


def _build_threads_individual_prompt(changelog_text: str, market: str | None) -> str:
    mkt_ctx = _market_ctx_threads(market)

    example = json.dumps([
        {
            "title": "Instalment scheduling for payment links",
            "post": "Payment links now support instalment scheduling. Split a large invoice into monthly payments "
                    "— the link handles reminders and collection automatically. No chasing needed. [URL]",
            "link_url": "https://hitpayapp.com/blog/payment-link",
        }
    ], ensure_ascii=False)

    return f"""For each HitPay changelog entry below, write a single Threads post announcing the update.

VOICE: Founder talking to their community. Factual, clear, warm. Not a press release.
{mkt_ctx}

CHANGELOG ENTRIES:
{changelog_text}

INSTRUCTIONS:
- Return a JSON array — one object per changelog entry.
- Each object: {{"title": "<entry title>", "post": "<announcement text>", "link_url": "<url or null>"}}
- Post: 200–450 chars. What the feature does + [URL] at end if relevant. [URL] is a literal placeholder.
- link_url: set to https://hitpayapp.com/blog/hitpay-rates or a relevant slug, or null.
- One post per entry — do NOT combine entries.
- No hashtags, no emojis, no buzzwords.

Return raw JSON array only — no markdown fences.
{example}"""


def generate_x_from_changelog(
    market: str = None,
    limit: int = 10,
    brand: str = "hitpay",
) -> dict:
    """Fetch MCP changelog and generate: one X roundup thread + one tweet per entry."""
    client = OpenRouterClient()

    mcp_result = get_changelog(limit=limit)
    changelog_text = _extract_changelog_text(mcp_result)
    if not changelog_text.strip():
        raise ValueError("No changelog entries returned from MCP — check HITPAY_MCP_URL is configured.")

    n = _count_entries(changelog_text, limit)

    roundup_resp = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_x_roundup_prompt(changelog_text, market, n)}],
    )
    roundup_raw = roundup_resp.content[0].text.strip()
    roundup_raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", roundup_raw, flags=re.DOTALL)
    roundup = json.loads(roundup_raw)

    individual_resp = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": _build_x_individual_prompt(changelog_text, market)}],
    )
    individual_raw = individual_resp.content[0].text.strip()
    individual_raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", individual_raw, flags=re.DOTALL)
    individuals = json.loads(individual_raw)

    return {
        "roundup": roundup,
        "individual": individuals,
        "market": market,
        "changelog_limit": limit,
    }


def generate_threads_from_changelog(
    market: str = None,
    limit: int = 10,
    brand: str = "hitpay",
) -> dict:
    """Fetch MCP changelog and generate: one Threads roundup thread + one post per entry."""
    client = OpenRouterClient()

    mcp_result = get_changelog(limit=limit)
    changelog_text = _extract_changelog_text(mcp_result)
    if not changelog_text.strip():
        raise ValueError("No changelog entries returned from MCP — check HITPAY_MCP_URL is configured.")

    n = _count_entries(changelog_text, limit)

    roundup_resp = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_threads_roundup_prompt(changelog_text, market, n)}],
    )
    roundup_raw = roundup_resp.content[0].text.strip()
    roundup_raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", roundup_raw, flags=re.DOTALL)
    roundup = json.loads(roundup_raw)

    individual_resp = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": _build_threads_individual_prompt(changelog_text, market)}],
    )
    individual_raw = individual_resp.content[0].text.strip()
    individual_raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", individual_raw, flags=re.DOTALL)
    individuals = json.loads(individual_raw)

    return {
        "roundup": roundup,
        "individual": individuals,
        "market": market,
        "changelog_limit": limit,
    }
