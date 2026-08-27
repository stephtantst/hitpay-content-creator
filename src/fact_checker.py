import re
from config import OPENROUTER_MODEL
from src.llm_client import OpenRouterClient

# Verified market facts sourced from official HitPay SEO/GEO audit — March 2026
MARKET_FACTS = {
    "sg": """
## Singapore — Verified HitPay Facts

**Regulatory:** HitPay is a licensed payment institution regulated by the Monetary Authority of Singapore (MAS). Licence number: PS20200643.

**Payment methods:** 50+ payment methods in Singapore (NOT "30+" or "700+")
- Key methods: PayNow, GrabPay, ShopeePay, Atome (BNPL), ShopBack PayLater, credit/debit cards, Apple Pay, Google Pay, bank transfers

**Verified transaction rates (hitpayapp.com/pricing):**
- PayNow (online): 0.65% + S$0.30
- Domestic cards: from 2.8% + S$0.50
- No setup fee, no monthly fee

**Social proof / stats:**
- 20,000+ businesses
- $1B+ in payments processed
- 10+ countries supported

**Tone:** HitPay is Singapore-headquartered.
""",
    "my": """
## Malaysia — Verified HitPay Facts

**Regulatory:** HitPay is approved by Bank Negara Malaysia (BNM) as a registered merchant acquirer and approved money service business agent.

**Payment methods:** 30+ payment methods in Malaysia (NOT "50+" — that count is for SG only)
- Key methods: DuitNow QR, FPX (online banking), Touch 'n Go eWallet, GrabPay, ShopeePay, Boost, Atome (BNPL), credit/debit cards, Apple Pay, Google Pay

**Verified transaction rates (hitpayapp.com/my/pricing):**
- DuitNow QR: 1.2%
- FPX (online banking): 1.8% + RM 0.40
- Domestic cards: from 1.2% + RM 1.00
- No setup fee, no monthly fee

**Local market data (safe to cite):**
- DuitNow QR processed 870 million transactions in 2024 — Malaysia's national payment standard
- Touch 'n Go has 26 million+ users
- FPX is the backbone of Malaysian online banking

**Social proof / stats:**
- 20,000+ businesses
- $1B+ in payments processed
- 10+ countries supported
""",
    "ph": """
## Philippines — Verified HitPay Facts

**Regulatory:** HitPay is a registered operator of a payment system (OPS) regulated under the Bangko Sentral ng Pilipinas (BSP).

**Payment methods:** 30+ payment methods in the Philippines (NOT "50+" — that count is for SG only)
- Key methods: GCash, Maya, QR Ph, GrabPay, ShopeePay, credit/debit cards, InstaPay, Apple Pay, Google Pay

**Verified transaction rates (hitpayapp.com/ph/pricing):**
- GCash: 2.3%
- QR Ph: 1.0% (or ₱20, whichever is higher)
- Local cards: 3% + ₱15
- No setup fee, no monthly fee

**Important context:**
- HitPay is Singapore-headquartered — always make the Philippines connection explicit in PH-market content
- GCash and QR Ph are the two highest-volume Philippines payment search terms
- GCash alone has dominant search share in the Philippines market

**Social proof / stats:**
- 20,000+ businesses
- $1B+ in payments processed
- 10+ countries supported
""",
}

FACT_CHECK_PROMPT = """You are a fact-checker for HitPay blog content. Your job is to review the article below and flag any claims that are inaccurate, outdated, or inconsistent with the verified facts provided.

## Verified Facts for this Market
{market_facts}

## Article to Review
---
{content}
---

## Instructions
Review the article carefully and report ONLY genuine factual issues. Be specific about what is wrong and what it should say instead.

Check for:
1. Wrong payment method counts (e.g., "50+" for MY/PH when it should be "30+")
2. Incorrect or missing transaction rates
3. Wrong or missing regulatory credentials (MAS/BNM/BSP)
4. Incorrect payment method names for this market (e.g., naming PayNow in a PH article)
5. Wrong stats (e.g., different business count, processing volume)
6. Claims about features HitPay doesn't offer in this market
7. Tone/branding violations: "seamlessly", "unlock", "revolutionise", "game-changer"

Format your response as a JSON object with this structure:
{{
  "market_detected": "sg|my|ph|unknown",
  "overall": "pass|warn|fail",
  "issues": [
    {{
      "severity": "critical|warning|info",
      "location": "short quote from article (first 60 chars of the problematic sentence)",
      "issue": "what is wrong",
      "fix": "what it should say instead"
    }}
  ],
  "summary": "One sentence summary of the check result"
}}

If no issues are found, return an empty "issues" array and overall "pass".
Respond with ONLY valid JSON — no markdown, no preamble."""


def _detect_market(post: dict, content: str) -> str:
    """Detect market from post metadata and content."""
    # Check tags and categories first
    tags = post.get("tags", "[]")
    cats = post.get("categories", "[]")
    slug = post.get("slug", "")
    keyword = post.get("keyword", "")

    combined = f"{tags} {cats} {slug} {keyword}".lower()
    content_lower = content.lower()

    # Explicit market signals
    if any(x in combined for x in ["singapore", " sg ", "-sg-", "/sg", "sg-"]):
        return "sg"
    if any(x in combined for x in ["malaysia", " my ", "-my-", "/my", "my-", "malaysian"]):
        return "my"
    if any(x in combined for x in ["philippines", " ph ", "-ph-", "/ph", "ph-", "philippine", "filipino"]):
        return "ph"

    # Fall back to content signals
    sg_signals = ["paynow", "grabpay singapore", "mas licence", "mas-licensed", "monetary authority of singapore"]
    my_signals = ["duitnow", "fpx", "touch 'n go", "touch n go", "bnm", "bank negara", "ringgit", " rm "]
    ph_signals = ["gcash", "qr ph", "maya", "instapay", "bsp", "bangko sentral", "piso", "peso", "₱"]

    sg_count = sum(content_lower.count(s) for s in sg_signals)
    my_count = sum(content_lower.count(s) for s in my_signals)
    ph_count = sum(content_lower.count(s) for s in ph_signals)

    scores = {"sg": sg_count, "my": my_count, "ph": ph_count}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


_SOCIAL_FACTS = """
## Settlement Timing — CRITICAL, always check
- Singapore HitPay Balance (PayNow, GrabPay, ShopeePay, all APMs, Borderless QR): T+1 CALENDAR DAY
  Do NOT say "next business day" for SG APMs — calendar day includes Saturday and Sunday
- Singapore Cards (Stripe): from T+1 Business Day
- Malaysia HitPay Balance (DuitNow, FPX, TnG, ShopeePay, all APMs, Borderless QR): T+2 CALENDAR DAYS
  NEVER say "next business day" or "T+1" for Malaysia — always T+2 calendar
- Malaysia Cards (Stripe): T+3 Business Days
- Philippines HitPay Balance (non-card, e.g. GCash, QR Ph, ShopeePay): T+1 Calendar Day
  (InstaPay for under PHP 50k, PESONet for over PHP 50k — 2–3 working days)
- Philippines Cards (online): T+7 Calendar Days; Cards (in-person): T+2 Calendar Days

## Payment Method Availability — CRITICAL
- NO Alipay in Singapore (WeChat Pay covers the Chinese visitor segment)
- NO Alipay in Philippines
- Alipay IS available in Malaysia
- WeChat Pay IS available in Philippines (cross-border)
- GCash is Philippines-only (available as cross-border in SG/MY)
- DuitNow is Malaysia-only (available as cross-border in SG/PH)
- FPX is Malaysia-only
- QR Ph is Philippines-only (available as cross-border in SG/MY)
- PayNow is Singapore-only (available as cross-border in MY/PH)
- Touch 'n Go is Malaysia-only (available as cross-border in SG/PH)
- GIRO Direct Debit is Singapore-only

## Verified Fees — only state these, no others
Singapore: PayNow 0.65% + S$0.30 | Domestic cards 2.8% + S$0.50
Malaysia: DuitNow QR 1.2% | FPX 1.8% + RM 0.40 | Domestic cards 1.2% + RM 1.00
Philippines: GCash 2.3% | QR Ph 1.0% (or ₱20, whichever is higher) | Local cards 3% + ₱15

## Recurring Billing — supported methods only
Singapore: Cards, GIRO, ShopeePay, GrabPay (PayNow is NOT recurring)
Malaysia: Cards, TnG, ShopeePay, GrabPay, ZaloPay (FPX is NOT recurring)
Philippines: Cards, ShopeePay (GCash is NOT recurring, QR Ph is NOT recurring)

## Do NOT accept these claims
- "next business day" for SG APMs (correct: T+1 calendar day — settles Saturday if collected Friday)
- "next business day" for Malaysia (correct: T+2 calendar days)
- "no-shows dropped", "sales increased", or any invented quantitative result
- Specific stats attributed to HitPay (user counts, processing volumes) without citing source
- "50+ payment methods" for Malaysia or Philippines (that count is Singapore only)
"""

_SOCIAL_FC_PROMPT = """Fact-check this HitPay social media post against the verified facts below.

MARKET: {market}

VERIFIED FACTS:
{facts}

POST CONTENT:
{content}

Check ONLY factual claims about: settlement timing, payment method availability, fees/MDR rates, market availability of specific features, and any specific stats or numbers.
Do NOT flag tone, style, CTAs, or claims you cannot verify either way from the facts above.

Return JSON only — no markdown:
{{
  "verdict": "pass" | "flag" | "fail",
  "issues": [
    {{
      "severity": "critical" | "warning",
      "claim": "exact short quote from the post",
      "issue": "what is wrong",
      "fix": "what it should say"
    }}
  ],
  "summary": "one sentence"
}}

verdict definitions:
  pass = no factual issues
  flag = imprecise but not wrong (e.g. T+1 calendar described as "next business day" — true on weekdays but misleading)
  fail = critical factual error that must be corrected before publishing"""


def fact_check_social_post(content: str, market: str) -> dict:
    """Fact-check a Threads or X post against verified HitPay facts.

    Args:
        content: Full post text (all parts of a thread concatenated, or a single tweet)
        market: "SG", "MY", or "PH"

    Returns:
        {"verdict": "pass"|"flag"|"fail", "issues": [...], "summary": "..."}
    """
    import json
    from src.generator import _messages_create_with_retry

    prompt = _SOCIAL_FC_PROMPT.format(
        facts=_SOCIAL_FACTS,
        market=market.upper(),
        content=content,
    )

    client = OpenRouterClient()
    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=600,
        system="You are a fact-checker. Return only valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def run_fact_check(post: dict, content: str) -> dict:
    """Run Claude-powered fact check on a blog post. Returns a dict with issues."""
    market = _detect_market(post, content)
    market_facts = MARKET_FACTS.get(market, "\n".join(MARKET_FACTS.values()))

    prompt = FACT_CHECK_PROMPT.format(
        market_facts=market_facts,
        content=content[:12000],  # limit to avoid token overflow
    )

    client = OpenRouterClient()
    response = client.messages.create(
        model=OPENROUTER_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    import json
    result = json.loads(raw)
    result["market_detected"] = market
    return result
