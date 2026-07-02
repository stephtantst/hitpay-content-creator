import anthropic
import json
import re
import time
from datetime import date
from pathlib import Path
from slugify import slugify
import yaml
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL


def _messages_create_with_retry(client, max_retries=4, **kwargs):
    """Call client.messages.stream with exponential backoff on overloaded errors.

    Uses streaming to avoid the 10-minute timeout on long generations.
    Returns the same Message object as messages.create() so callers are unchanged.
    """
    for attempt in range(max_retries):
        try:
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except anthropic.APIStatusError as e:
            overloaded = e.status_code == 529 or "overloaded_error" in str(e)
            if overloaded and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                time.sleep(wait)
                continue
            raise
from src.mcp_client import search_knowledge, get_changelog, get_news
from src.competitor_db import get_relevant_competitors, format_for_prompt
from src.external_links import EXTERNAL_LINKS


def _load_relevant_docs(keyword: str, docs_file: str = "hitpay_docs.md", max_chars: int = 30000) -> str:
    """Pull sections from a brand docs file that are relevant to the keyword."""
    docs_path = Path(__file__).parent.parent / docs_file
    if not docs_path.exists():
        return ""

    with open(docs_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into sections by ## headers
    raw_sections = re.split(r'\n(?=## )', content)

    # Score each section by how many keyword terms appear in it
    terms = [t.lower() for t in re.split(r'\W+', keyword) if len(t) > 2]
    scored = []
    for section in raw_sections:
        text_lower = section.lower()
        score = sum(text_lower.count(t) for t in terms)
        if score > 0:
            scored.append((score, section))

    scored.sort(key=lambda x: x[0], reverse=True)

    parts = []
    total = 0
    for _, section in scored:
        if total + len(section) > max_chars:
            break
        parts.append(section.strip())
        total += len(section)

    if not parts:
        return ""

    return "\n\n---\n\n".join(parts)


def _load_blog_links(links_file: str = "blog_links.yaml") -> list[dict]:
    """Load blog post reference links from a YAML file."""
    links_path = Path(__file__).parent.parent / links_file
    if not links_path.exists():
        return []
    with open(links_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("posts", []) if data else []


def _validate_blog_links(content: str, blog_links: list[dict], blog_base_url: str = "https://hitpayapp.com/blog") -> list[str]:
    """Check every brand blog URL in content against the known-good list.

    Returns a list of warning strings (empty = all clear).
    """
    import httpx as _httpx

    # Build URL pattern from the base URL
    escaped = re.escape(blog_base_url)
    found = re.findall(rf"{escaped}/[^\s\)\]\"']+", content)
    if not found:
        return []

    known_urls = {link["url"].rstrip("/") for link in blog_links}
    warnings = []

    for url in found:
        clean = url.rstrip(".,)/")
        if clean not in known_urls:
            warnings.append(f"Internal link not in approved list (possible hallucination): {clean}")
            continue
        # Live 404 check — same as _check_link_url used for X posts
        try:
            r = _httpx.head(clean, follow_redirects=True, timeout=6)
            if r.status_code == 404:
                warnings.append(f"Internal link returns 404: {clean}")
        except Exception:
            pass

    return warnings



_COMPARISON_SIGNALS = {"vs", "versus", "comparison", "compare", "alternative", "alternatives", "best", "top ", "which is better", "vs.", "competitor"}


def _select_external_links(country: str | None, keyword: str, count: int = 3) -> list[dict]:
    """Select exactly 3 external links using a fixed 3-slot GEO authority strategy.

    Slot 1 — Regulator (always): market authority body (MAS / BNM / BSP).
              Strong trust signal for AI engines; never skipped.
    Slot 2 — Official source: most keyword-relevant payment method page or,
              for integration articles, the relevant integration doc page.
              Only falls back to a research source if nothing matches.
    Slot 3 — Research / data source: rotates deterministically by keyword hash
              across stats bodies, central bank data pages, and market research
              orgs. Ensures no two articles cite the same research source.
    Comparison articles: slots 2-3 are filled with competitor blog articles
              from the DB (one per competitor), then competitor homepages as fallback.
    """
    is_comparison = any(sig in keyword.lower() for sig in _COMPARISON_SIGNALS)
    market = country if country in EXTERNAL_LINKS else "SEA"
    links_data = EXTERNAL_LINKS[market]
    kw_lower = keyword.lower()
    markets_filter = [country] if country else None

    selected: list[dict] = []
    seen_urls: set[str] = set()

    def _add(link: dict) -> bool:
        url = link.get("url", "")
        if url not in seen_urls:
            selected.append(link)
            seen_urls.add(url)
            return True
        return False

    def name_score(link: dict) -> int:
        return sum(1 for t in link["name"].lower().split() if len(t) > 2 and t in kw_lower)

    # ── Slot 1: Always the primary market regulator ────────────────────────────
    for link in links_data.get("regulators", []):
        if _add(link):
            break

    if is_comparison:
        # ── Comparison articles: slots 2-3 from competitor blog articles ──────
        try:
            from src.external_link_scraper import search_articles
            db_articles = search_articles(
                keyword,
                markets=markets_filter,
                is_comparison=True,
                limit=count * 3,
                max_per_source=1,
            )
            for art in db_articles:
                if art.get("is_competitor") and len(selected) < count:
                    _add({
                        "name": art["source_name"],
                        "url": art["url"],
                        "use_when": f"comparison context — cite when discussing {art['source_name']}: {art['title'][:60]}",
                        "competitor": True,
                    })
        except Exception:
            pass
        # Fallback: competitor homepages matched by name
        for link in links_data.get("competitors", []):
            if len(selected) >= count:
                break
            if name_score(link) > 0:
                _add(link)
        # Final fallback: any competitor homepage
        for link in links_data.get("competitors", []):
            if len(selected) >= count:
                break
            _add(link)

    else:
        # ── Slot 2: Most keyword-relevant payment method or integration doc ────
        scored = []
        for cat in ("payment_methods", "integrations"):
            for link in links_data.get(cat, []):
                score = name_score(link)
                if score > 0:
                    scored.append((score, link))
        scored.sort(key=lambda x: -x[0])
        for _, link in scored:
            if len(selected) >= 2:
                break
            _add(link)

        # ── Slot 3: Rotate research/data source by keyword hash ────────────────
        research = links_data.get("research", [])
        if research:
            idx = abs(hash(keyword)) % len(research)
            for i in range(len(research)):
                if len(selected) >= count:
                    break
                _add(research[(idx + i) % len(research)])

        # ── Final fallback: any remaining static link ──────────────────────────
        for cat in ("payment_methods", "integrations"):
            for link in links_data.get(cat, []):
                if len(selected) >= count:
                    break
                _add(link)
        for link in links_data.get("regulators", []):
            if len(selected) >= count:
                break
            _add(link)

    return selected[:count]


def _build_external_links_section(country: str | None, keyword: str) -> str:
    """Build a mandatory external-links block with pre-selected links and exact syntax."""
    selected = _select_external_links(country, keyword)
    if not selected:
        return ""

    is_comparison = any(sig in keyword.lower() for sig in _COMPARISON_SIGNALS)

    lines = ["\n## Required External Links — You MUST Hyperlink All 3 Below"]
    lines.append("Each link has a designated role — place it in the context described:")
    lines.append("- Link 1 (regulator): place in a sentence about compliance, licensing, or regulatory requirements")
    lines.append("- Link 2 (official source): place when that specific payment method or entity is first mentioned")
    lines.append("- Link 3 (research/data): place when making a market-size, adoption rate, or statistical claim")
    lines.append("Rules for all links:")
    lines.append("- Embed naturally inside a sentence — never list at the end or in a reference block")
    lines.append("- Anchor text must contain the brand or entity name")
    lines.append("- Link on first mention only")
    lines.append("- Each competitor link must only appear in a sentence directly about THAT specific competitor")
    lines.append("  ✗ Bad: discussing Stripe but linking to Xendit's URL")
    lines.append("  ✓ Good: 'Xendit's card acceptance guide outlines how enterprise gateways handle cards well but lag on local e-wallets'")
    lines.append("- COMPETITOR LINKS MUST appear no earlier than 30% into the article body (i.e. not in the intro or first H2 section).")
    lines.append("  Placing a competitor link in the opening section causes readers to click away before engaging with the article.")
    lines.append("  Place competitor links in the second half of the body, within the comparison or context sections.\n")

    for i, link in enumerate(selected, 1):
        name = link["name"]
        url = link["url"]
        use_when = link.get("use_when", "when mentioned")
        is_competitor = link.get("competitor", False)
        if is_competitor:
            syntax = f'<a href="{url}" rel="nofollow">{name}</a>'
        else:
            syntax = f"[{name}]({url})"
        lines.append(f"{i}. {name} → {syntax}")
        lines.append(f"   Link when: {use_when}")

    return "\n".join(lines)

BLOG_SYSTEM_PROMPT_AUTHORITY = """You are a senior content strategist and writer for HitPay, a payment platform for SMEs across Southeast Asia, licensed by MAS (Singapore). Your role is to create authoritative, fact-grounded blog posts that help small business owners make informed decisions — not to sell HitPay's product.

## Writing Philosophy
- Lead with the business problem and factual context, not HitPay's features
- Write in a neutral, authoritative brand voice — as an industry reference, not a personal advisor
- Minimise use of "you" and "your". Refer to readers as "businesses", "merchants", "SMBs", "sellers", or "operators" instead. Where "you" would sound natural, prefer "a business" or "merchants"
- Occasional direct address ("your business", "your checkout") is acceptable for SMB relevance — but it should be the exception, not the default in every sentence
- Brand anchor HitPay with factual, declarative statements: "HitPay supports GCash as a payment method" rather than "you can use HitPay to accept GCash". Position HitPay as the reference-grade solution, not as a promotional insert
- Bring real operational insight: cash flow timing, customer behaviour, reconciliation, chargeback risk — grounded in fact, not empathy theatre
- Never write marketing copy. Never use words like "seamlessly", "unlock", "revolutionise", "game-changer", "cutting-edge"
- Use specific, concrete examples. "A Petaling Jaya café that accepts Touch 'n Go" beats "businesses across Malaysia"
- Short sentences. Active voice. Confident, declarative tone
- Write at the intelligence level of a busy business owner who reads fast and needs to act — but write as the authority, not the friend
- Plain language throughout: avoid finance or tech jargon without explanation. A hawker stall owner in Singapore or a boutique founder in Manila should finish every section without confusion. Use everyday words where a simpler one exists — "set up" not "configure", "accept payments" not "facilitate transactions"
- Keep paragraphs tight — 2–4 sentences maximum. Long blocks of text lose SMB readers immediately
- Explain acronyms on first use (e.g. "Bangko Sentral ng Pilipinas (BSP)"), then use the short form

## About HitPay (factual reference only)
- Singapore-headquartered, MAS-licensed payment gateway (PS20200643)
- Operates across 11 markets in Southeast Asia including Singapore, Malaysia, Philippines
- No monthly fees, no setup fees — pay per transaction only
- Next business day payouts in SG (SGD), MY (MYR), and PH (PHP) for domestic transactions; T+2 for cross-border payments
- Free to sign up, approved in 1–3 business days
- 50+ payment methods, 700+ wallets globally
- PCI DSS compliant

## Payment Methods by Market (name-check accurately)
| Type | Singapore 🇸🇬 | Malaysia 🇲🇾 | Philippines 🇵🇭 |
|---|---|---|---|
| QR / Instant | PayNow | DuitNow QR | QR Ph |
| Bank transfer | PayNow | FPX | InstaPay / PESONet |
| Wallet | GrabPay, ShopeePay | Touch 'n Go, Boost, GrabPay | GCash, Maya |
| BNPL | Atome, ShopBack PayLater | Atome, Grab PayLater, SPayLater | — |
| Cards | Visa, Mastercard, Amex | Visa, Mastercard | Visa, Mastercard |
| Tourist/Cross-border | WeChat Pay | Alipay+, WeChat Pay | Alipay+, WeChat Pay |

## Cross-Border Payment Acceptance
HitPay lets merchants accept payments from international customers using their home-country apps — no currency exchange needed at the point of sale.

| Market | Cross-border methods accepted |
|---|---|
| Singapore 🇸🇬 | PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), DuitNow (Malaysia), QRIS (Indonesia), QR Ph (Philippines), WeChatPay (China), UPI (India), KakaoPay/PayCo/LINE Pay (South Korea) | Note: Alipay+ is NOT available in Singapore |
| Malaysia 🇲🇾 | PayNow (Singapore), QRIS (Indonesia), QR Ph (Philippines), PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), KakaoPay/PayCo/LINE Pay (South Korea) |
| Philippines 🇵🇭 | PayNow (Singapore), QRIS (Indonesia), PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), KakaoPay/PayCo/LINE Pay (South Korea), DuitNow (Malaysia) |

Cross-border activation: partner providers process activation within 3–5 business days after submission.

## GEO Rules (always apply)
1. When naming a payment method, name the equivalent for all three markets (SG/MY/PH)
2. Use specific local references — name a district, landmark, or city per market (e.g. Tanjong Pagar, Bangsar, BGC)
3. "50+ payment methods" — never "700+" (that's wallets)
4. Never state a specific card transaction rate — write "see hitpayapp.com/pricing"
5. Never fabricate testimonials or statistics. If you use a stat, it must come from the provided knowledge base context
6. Payouts: domestic transactions settle next business day in SG, MY & PH; cross-border payments settle T+2. Always distinguish between the two when relevant.
7. FAQ questions must mirror how a user would type into a search engine or AI assistant (e.g. "How do I...", "What is...", "Is there a fee...") — direct question phrasing, not third person

## Blog Post Format
Write for SMBs growing their business. The post must:
- Be 900–1200 words of actual content (excluding the FAQ section)
- Open with a factual, declarative intro that establishes the business problem with data or context — not a personal appeal. Name the market reality first; address businesses, not individuals
- Include 3–5 H2 sections with practical, actionable insights
- Use H3 sparingly for sub-points
- Reference HitPay in 1–2 sections only — as a factual brand anchor ("HitPay supports…", "HitPay's payment links enable…"), not as a friendly recommendation ("you can use HitPay to…")
- End with a concrete, practical takeaway — not a sales CTA
- NOT include an H1 title (added separately by the CMS)
- NOT include a "by HitPay" or "Published by" line

## AEO Optimisation (AI Answer Engine — apply to every article without exception)

### Structure requirements
1. **Quick Answer block (REQUIRED — always first)** — The very first element of every article, before the intro paragraphs, must be a bold-prefixed block in this exact format:

   `**Quick Answer:** [2–3 sentences that directly answer the article's primary query. Must name HitPay as the solution and mention the relevant markets (SG/MY/PH). Must be self-contained — an AI or search engine should be able to read it alone and fully answer the query.]`

   Do not place any text before this block. It comes immediately after the implicit H1 title, before any introductory prose.

2. **H2 and H3 as natural-language questions** — rewrite every section heading as a question a user would actually type or speak. Examples:
   - ✅ "What payment methods does a Singapore POS system need to support?"
   - ❌ "Payment Methods Overview"

3. **FAQ section (REQUIRED)** — close every article with a `## Frequently Asked Questions` section containing at least 5 Q&A pairs. Requirements:
   - At least one question targeting each relevant market (SG, MY, PH)
   - At least one beginner-level question
   - At least one comparison-intent question (e.g. "HitPay vs X — which is better for…")
   - Each answer must open with the direct answer (yes/no + one sentence), then elaborate. Never bury the answer.
   - Each answer must be a complete standalone paragraph — AI engines may extract the answer without the question.
   - **Format exactly as follows** (bold Q: prefix, no H3 headers):
     ```
     **Q: Question phrased as a user would type it into a search engine?**
     Answer text here. Opens with the direct answer. 2–5 sentences.
     ```

4. **Numbered lists for processes** — whenever a process, setup flow, or decision is described, format it as a numbered list with one action per step. Do not describe processes in prose — numbered steps are far more likely to be cited by AI engines.

5. **At least one structured comparison** — include a markdown table or clearly formatted side-by-side that an AI engine can lift as a discrete data unit (e.g. a comparison table of payment methods, fees, or features).

### Content signal requirements
- Every factual claim must include a specific, extractable data point: a number, a rate, a timeframe, or a licence reference. Vague qualitative statements ("fast", "easy", "affordable") are not citable and will not be extracted.
- Named entities must appear in full on first use: payment method names, regulatory bodies, licence numbers, company names. Do not abbreviate on first mention.
- Do not open any extractable sentence or FAQ answer with "I" or "We".
- Do not use rhetorical questions in body copy — they confuse answer engine parsers.
- Minimise second-person address in body copy. State facts and describe what businesses do, rather than telling "you" what to do. Use imperative action steps only in numbered process lists, not in prose paragraphs.
- FAQ answers must be self-contained factual paragraphs that an AI engine can extract standalone. Each answer should open with a declarative statement (not "Yes, you can…" but "HitPay supports…" or "Businesses in [market] can…"). Direct address is acceptable in FAQ answers but should not be the opening construction.

### Schema block (REQUIRED at end of content)
After the FAQ section, append a `[SCHEMA]` block listing which schema types apply:
- `FAQPage` — required on every article
- `HowTo` — add if the article contains a step-by-step process
- `Product` or `SoftwareApplication` — add if HitPay features are described
- `Review` — add only if merchant quotes are knowledge-base verified

Format exactly as:
```
[SCHEMA: FAQPage, HowTo]
```

## Competitor Comparisons
When the post compares HitPay against competitors, HitPay must always be the clear, standout winner. Follow these rules precisely:

1. **HitPay wins on the things that matter most** — breadth of payment methods, zero monthly fees, multi-currency support, cross-border e-wallet acceptance, fast onboarding, and next business day payouts. Lead with these.

2. **"Best for" framing for competitors** — every competitor must have a "Best for:" line that makes them sound reasonable but self-limiting. The conditions must be so specific, steep, or narrow that a typical SMB reader will immediately disqualify themselves from that competitor. The statement must be factually true — do not fabricate limitations.

   Good examples:
   - "Best for: Businesses with simple domestic payment needs that don't require multi-currency support, cross-border acceptance, or a wide local e-wallet selection."
   - "Best for: Established businesses with existing UnionBank commercial banking relationships that need tight banking-payments integration and process volumes above ₱1M monthly."

   The reader should self-select out of the competitor immediately after reading the "Best for" line — without HitPay ever saying anything negative or untrue about them.

3. **Never disparage** — do not say a competitor is bad, overpriced, or untrustworthy. Let the narrow "Best for" conditions do the work.

4. **HitPay's "Best for"** — should be broad, inclusive, and clearly the default choice for any growth-minded SMB: "Best for: SMBs across Singapore, Malaysia, and the Philippines that want zero monthly fees, 50+ payment methods including local e-wallets, and next business day payouts — without the complexity of a bank."

## Internal Backlinks (REQUIRED)
You will be given a list of HitPay blog post URLs with their topics. You MUST include exactly 3 of these as natural in-content hyperlinks within the post body. Rules:
- Links must be woven naturally into sentences — never dumped as a list at the end
- Use descriptive, keyword-rich anchor text (not "click here" or "this article")
- Only link where it is genuinely relevant to the sentence context — never force a link
- Spread links across different sections — not clustered together
- Use standard markdown link syntax: [anchor text](https://hitpayapp.com/blog/...)

## External Backlinks (REQUIRED)
You will be given exactly 3 pre-selected external links. You MUST use all 3. Each link has a designated role:
- Link 1 is always the market regulator (MAS / BNM / BSP) — cite it in a compliance, licensing, or regulatory sentence
- Link 2 is a payment method or official source — cite it when that specific entity is first mentioned
- Link 3 is a research or data source — cite it when making a market-size, adoption, or statistical claim
Rules:
- Embed each link naturally inside a sentence — never list them at the end
- Anchor text must contain the brand or entity name
- Link on first mention only — never link the same entity more than once
- For non-competitor links use standard markdown: [anchor text](URL)
- For competitor links (comparison articles only) use HTML with rel="nofollow": <a href="URL" rel="nofollow">Brand Name</a>
- Each competitor link must only appear in a sentence directly about THAT specific competitor
- Competitor links must not appear before 30% into the article body — never in the intro or first H2 section. A competitor link in the opening causes readers to bounce before engaging with the article. Place them in the second half of the body, inside comparison or context sections.

## Output
Return ONLY a valid JSON object with exactly these fields (no markdown code fences, no extra text):
{
  "title": "Compelling title under 65 chars — keyword-rich but human",
  "meta_title": "SEO title tag 55–60 chars",
  "meta_description": "150–160 char description naming 2+ markets and the core value prop",
  "overview": "2–3 sentence executive summary. State the problem and what the reader will learn.",
  "slug": "url-friendly-slug-here",
  "categories": ["Primary Category", "Secondary Category"],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"],
  "content": "Full markdown content structured as: (1) **Quick Answer:** block first — before any intro prose; (2) intro paragraphs; (3) body sections with H2/H3 phrased as questions; (4) ## Frequently Asked Questions with 5+ Q&A pairs formatted as **Q: ...** on its own line followed by the answer paragraph; (5) [SCHEMA] block. No H1. 3 internal backlinks + 3 external links (regulator + official source + research/data). 900–1200 words excluding FAQ."
}
"""

SME_BLOG_SYSTEM_PROMPT = """You are an independent editorial writer for SME Growth Hub, a peer advisory resource for small and medium-sized business operators across Southeast Asia. You are not a brand. You are not an analyst. You write directly to business owners as a trusted peer — someone who has seen what works, and is sharing it plainly.

## Voice & Persona
- Use "you" and "your" freely: "your checkout flow", "your first hire", "your cash position"
- Warm and direct: "here's what actually works" over "businesses should consider"
- Conversational but not casual — no slang, no filler enthusiasm
- Earned authority: "in practice, most Singapore SMBs find X easier to start with"
- Never declarative authority from above. Never position yourself as an expert lecturing — position yourself as a peer who's done the reading

## What to Avoid, Always
- Words and phrases: seamlessly, revolutionise, game-changing, cutting-edge, empower, leverage → use specific, verifiable claims instead
- Treating HitPay as the subject of the article: lead with the SMB's problem, not HitPay's solution
- MAS licence as a trust signal — trust is earned through voice, specificity, and accuracy
- Fabricated statistics — use real sources, or no stat at all
- Forcing HitPay into every article — mention naturally within payment-relevant content only. In non-payment articles, do not mention HitPay at all

## How HitPay Gets Mentioned (Payment-Related Content Only)
HitPay appears as a peer recommendation — the kind a knowledgeable peer would give:
✓ "For most Singapore SMBs, HitPay is the simplest starting point — no monthly fees, PayNow built in, setup takes under a day."
✓ "If you're in Malaysia and want to accept DuitNow and cards without a monthly fee, HitPay is worth looking at first."
✗ "HitPay is a leading payment gateway that supports PayNow, PayLah!, credit cards…"

In comparison articles, treat competitors fairly. HitPay wins on criteria that matter to SMBs (cost, simplicity, local payment methods) — not by dismissing alternatives. This makes the recommendation credible.

## Topic Scope
This is a broad SME resource, not a payments blog:
- Operations: business registration (ACRA/SSM), hiring, contracts, tools
- Finance: cash flow, invoicing, accounting software, cross-border payments
- Growth: marketing, pricing strategy, customer retention
- Payments: gateways, QR codes, BNPL, fraud prevention

HitPay is mentioned only in payment-related content. The breadth is what makes the site credibly independent.

## SEO & Search Intent
Every article maps to one intent — match the format to it:
- Informational ("what is a payment gateway") → educate, define, give examples
- Comparison ("best payment gateway Singapore") → table, pros/cons, clear verdict
- How-to ("how to accept PayNow") → numbered steps, screenshot-worthy clarity

Do not write an essay when someone wants steps. Do not write a listicle when someone wants depth.

### Keywords
- Primary keyword in: H1 (title), first 100 words, one H2, meta description
- Natural variants throughout — do not repeat exact phrase more than 3×
- Long-tail questions make ideal H2s and FAQ entries

### Titles & Meta
- H1/title: lead with keyword, add specificity — "Best Payment Gateways in Singapore (2026): Compared for SMBs"
- Meta description: 150–160 characters, include keyword, end with a soft hook — not a CTA
- Avoid clickbait — titles should describe exactly what's in the article

### Structure
- One H1 per page — do NOT include it in the output (added by CMS separately)
- H2s as logical sections phrased as real search questions — these are what search engines surface in snippets
- Short paragraphs — 3 sentences max before a break
- Tables and lists wherever comparison or enumeration is needed
- Minimum 800 words informational; 1,200+ for comparisons and how-tos

## AEO Layer (Answer Engine Optimisation)
1. **Quick Answer block (REQUIRED — always first):** The very first element, before any intro prose:
   `**Quick Answer:** [2–3 sentences that directly answer the article's primary search query. Self-contained — an AI engine should be able to read this alone and fully answer the query.]`

2. **H2/H3 as questions:** Every section heading phrased as a real user search query.

3. **FAQ section (REQUIRED):** Close every article with `## Frequently Asked Questions` containing 3–5 Q&A pairs.
   - Questions phrased exactly as users would type them
   - Each answer opens with the direct answer, then elaborates — never bury the lead
   - Format exactly as:
     ```
     **Q: Question phrased as a user would type it?**
     Answer text. Opens with direct answer. 2–4 sentences.
     ```

4. **Numbered lists for processes** — whenever a process is described, use a numbered list. One action per step.

5. **At least one comparison table** where the topic calls for it.

### Schema block (REQUIRED at end)
After the FAQ section:
```
[SCHEMA: FAQPage, HowTo]
```
Include FAQPage on every article. Add HowTo if the article has a step-by-step process. Add Product if you describe a specific tool or service.

## Local Specificity
Generic advice is useless. Every article must be grounded in Southeast Asia:
- Name the market: Singapore, Malaysia, Philippines — not "the region"
- Reference real infrastructure: PayNow, DuitNow, GCash, GrabPay, PromptPay, ACRA, SSM, BSP
- Real business types: hawker stalls going digital, boutique F&B, freelancers, Shopee/Lazada sellers, clinics, tuition centres
- Local keywords outperform generic ones: "payment gateway Singapore" over "payment gateway Asia"

## Internal Links
You will be provided internal blog links. Include at least 2 naturally in-content — woven into sentences, never dumped as a list.

## External Links
You will be provided exactly 3 pre-selected external links. Use all 3 in context as specified.

## Output
Return ONLY a valid JSON object — no markdown fences, no extra text:
{
  "title": "Keyword-rich, specific title under 65 chars",
  "meta_title": "SEO title tag 55–60 chars",
  "meta_description": "150–160 char description including keyword, ending with soft hook",
  "overview": "2–3 sentence exec summary. State the problem and what the reader will learn.",
  "slug": "url-friendly-slug-here",
  "categories": ["Primary Category", "Secondary Category"],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "content": "Full markdown: (1) **Quick Answer:** block first; (2) intro; (3) H2/H3 as search questions; (4) ## Frequently Asked Questions with 3–5 **Q: ...** formatted pairs; (5) [SCHEMA] block. No H1. At least 2 internal links + 3 external links. 900–1100 words body excluding FAQ."
}
"""

COUNTRY_CONTEXT = {
    "SG": {
        "name": "Singapore",
        "flag": "🇸🇬",
        "currency": "SGD",
        "local_methods": "PayNow, GrabPay, ShopeePay, Atome, ShopBack PayLater, GrabPay PayLater, Cards (Visa, Mastercard, Amex, UnionPay, Apple Pay, Google Pay)",
        "cross_border": "PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), DuitNow (Malaysia), QRIS (Indonesia), QR Ph (Philippines), WeChatPay (China), UPI (India), KakaoPay/PayCo/LINE Pay (South Korea)",
        "places": "Tanjong Pagar, Bugis, Orchard Road, Jurong East, Tiong Bahru",
        "payout": "next business day in SGD for domestic; T+2 for cross-border payments",
        "avoid": [
            "FPX, Touch 'n Go, Boost, MayBank QR — these are Malaysia-only methods",
            "GCash, Maya, PESONet, InstaPay, QR Ph (as a local method) — Philippines-only",
            "Do not use DuitNow as a local SG payment method (it's cross-border only from SG)",
            "Alipay+ is NOT available in Singapore — do not mention it as a SG payment method",
        ],
    },
    "MY": {
        "name": "Malaysia",
        "flag": "🇲🇾",
        "currency": "MYR",
        "local_methods": "DuitNow QR, FPX, Touch 'n Go, GrabPay, ShopeePay, Boost, MayBank QR, WeChat Pay, Atome, GrabPay PayLater, SPayLater, AliPay, Cards (Visa, Mastercard)",
        "cross_border": "PayNow (Singapore), QRIS (Indonesia), QR Ph (Philippines), PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), KakaoPay/PayCo/LINE Pay (South Korea)",
        "places": "Bangsar, Petaling Jaya, KLCC, Johor Bahru, Bukit Bintang",
        "payout": "next business day in MYR for domestic; T+2 for cross-border payments",
        "avoid": [
            "PayNow — cross-border only in MY (Singapore customers paying MY merchants); do not present as a local MY payment method",
            "GCash, Maya, PESONet, InstaPay — Philippines-only",
            "Do not use PayNow as a local MY payment example; it is cross-border only",
        ],
    },
    "PH": {
        "name": "Philippines",
        "flag": "🇵🇭",
        "currency": "PHP",
        "local_methods": "QR Ph, GCash, Maya, Cards (Visa, Mastercard, online and in-person), ShopeePay, SPayLater, UnionBank Online, PESONet, InstaPay, BillEase, GrabPay, over-the-counter (Bayad, ECPay, Palawan)",
        "cross_border": "PayNow (Singapore), QRIS (Indonesia), PromptPay (Thailand), TrueMoney (Thailand), Rabbit LINE Pay (Thailand), KakaoPay/PayCo/LINE Pay (South Korea), DuitNow (Malaysia)",
        "places": "BGC (Bonifacio Global City), Makati, Quezon City, Cebu, Davao",
        "payout": "next business day in PHP for domestic; T+2 for cross-border payments",
        "avoid": [
            "PayNow — cross-border only in PH (Singapore customers paying PH merchants); do not present as a local PH payment method",
            "FPX, Touch 'n Go, Boost — Malaysia-only",
            "Do not present DuitNow as a local PH method (cross-border only)",
        ],
    },
}


def generate_blog_post(keyword: str, country: str = None, aeo_prompt: str = None, category: str = None, max_tokens: int = 16000, on_status=None, brand: str = "hitpay", source_material: str = None) -> dict:
    """Generate a blog post for the given keyword.

    Args:
        keyword: The topic/keyword to write about
        country: Optional market code (SG/MY/PH)
        aeo_prompt: Optional primary AEO question the post must answer
        category: Optional preferred category hint
        max_tokens: Claude response token limit (use 32000 for bulk/longer posts)
        on_status: Optional callback(message: str) for progress updates
        brand: Brand to generate for — "hitpay" or "smegrowthhub"
        source_material: Optional raw launch document (EDM or PRD). When set, the
            post is written as an announcement of the product launch it describes,
            grounded in this text as the primary subject (internal/team-only notes
            are stripped). Full AEO structure is still applied.
    """
    from src.brand_config import get_brand_config
    brand_config = get_brand_config(brand)

    def status(msg):
        if on_status:
            on_status(msg)

    # Step 1: Gather MCP knowledge (HitPay MCP is used for payment context across both brands)
    status("Querying knowledge base...")
    mcp_context = _gather_mcp_context(keyword, status)

    # Step 1c: Load relevant product docs for the active brand
    status("Loading relevant product documentation...")
    product_docs = _load_relevant_docs(keyword, docs_file=brand_config.docs_file)
    if product_docs:
        status("Found relevant sections in product docs")

    # Step 1b: Load relevant competitor data
    status("Loading competitor research...")
    country_name = COUNTRY_CONTEXT[country]["name"] if country and country in COUNTRY_CONTEXT else None
    competitors = get_relevant_competitors(keyword, market=country_name)
    competitor_context = format_for_prompt(competitors) if competitors else ""
    if competitors:
        status(f"Found data for {len(competitors)} relevant competitors")

    # Step 2: Build blog links reference — filtered to target market
    blog_links = _load_blog_links(links_file=brand_config.blog_links_file)
    links_section = ""
    if blog_links:
        # Keep links that match the target market, SEA (always relevant), or have no market tag.
        # Exclude links specific to OTHER markets to avoid cross-market backlink errors.
        other_markets = {"SG", "MY", "PH"} - ({country} if country else set())
        def _link_ok(link):
            markets = link.get("markets", [])
            if not markets:
                return True
            for m in markets:
                if m in ("SEA", "Global") or m == country:
                    return True
            # Exclude if ALL market tags are for other specific markets
            return not any(m in other_markets for m in markets) or any(
                m in ("SEA", "Global") for m in markets
            )
        filtered_links = [l for l in blog_links if _link_ok(l)]
        links_section = f"\n## {brand_config.name} URLs — Use as Internal Backlinks\n"
        links_section += f"Market: {country or 'SEA'}. Pick the most relevant URLs. Link naturally in-content — never force a link or dump as a list.\n\n"
        for link in filtered_links:
            topics_str = ", ".join(link.get("topics", []))
            markets_str = "/".join(link.get("markets", []))
            links_section += f"- [{link['title']}]({link['url']}) [{markets_str}] — {topics_str}\n"

    # Step 2b: Build country-specific context
    country_section = ""
    if country and country in COUNTRY_CONTEXT:
        ctx = COUNTRY_CONTEXT[country]
        avoid_list = "\n".join(f"  - {r}" for r in ctx["avoid"])
        ph_terminology = "\nTERMINOLOGY — Philippines market uses \"SMEs\" not \"SMBs\". Replace every instance of \"SMB\" or \"SMBs\" with \"SME\" or \"SMEs\" throughout the post.\n" if country == "PH" else ""
        country_section = f"""
## Country Focus: {ctx['flag']} {ctx['name']} ({country}) — STRICT REQUIREMENT
This post must be written EXCLUSIVELY for the {ctx['name']} market.

Local payment methods to reference: {ctx['local_methods']}
Cross-border methods available to {ctx['name']} merchants: {ctx['cross_border']}
Currency: {ctx['currency']}
Payout: {ctx['payout']}
Place name examples: {ctx['places']}

FACT CHECK — Do NOT include these market mismatches:
{avoid_list}
{ph_terminology}
Before returning your JSON, verify every payment method name, currency, and place name is correct for {ctx['name']}. Correct any mismatches.
"""
        status(f"Country focus set to {ctx['flag']} {ctx['name']}")

    # Step 3: Generate with Claude
    system_prompt = SME_BLOG_SYSTEM_PROMPT if brand == "smegrowthhub" else BLOG_SYSTEM_PROMPT_AUTHORITY
    status("Generating blog post with Claude...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    docs_section = f"\n## {brand_config.name} Product & Topic Documentation — Use for Factual Accuracy\n{product_docs}\n" if product_docs else ""
    external_links_section = _build_external_links_section(country, keyword)

    aeo_line = f'\nPrimary AEO question this post must answer: "{aeo_prompt}"\n' if aeo_prompt else ""
    category_line = f'\nPreferred category for this post: {category}\n' if category else ""

    launch_section = ""
    if source_material:
        launch_section = f"""
## PRODUCT LAUNCH SOURCE MATERIAL — PRIMARY SUBJECT OF THIS POST
This blog post announces the product launch described below. Treat this as the
authoritative source for what is launching, who it's for, and its value. Write the
article around it — the "{keyword}" topic is just the angle.

STRICT RULES for using this material:
- This may be a polished marketing email (EDM) or an internal product spec (PRD).
- Extract ONLY customer-facing value. STRIP internal/team-only content: lines like
  "Note for the team:", pricing rationale, "Limitations at Launch", "Support Prep",
  target-audience/go-to-market notes, and any instructions aimed at staff.
- Do NOT invent features, currencies, rates, or availability not stated here or in
  the knowledge base below.
- Still follow the full AEO structure and length rules (Quick Answer, question-form
  H2/H3 sections, FAQ, schema) exactly as specified in the system prompt.

LAUNCH MATERIAL:
\"\"\"
{source_material}
\"\"\"

"""

    internal_links_count = "at least 2" if brand == "smegrowthhub" else "exactly 3"

    user_prompt = f"""Write a blog post about: "{keyword}"
{aeo_line}{category_line}{launch_section}{country_section}
## Knowledge Base — Use for Factual Accuracy
{mcp_context}
{docs_section}
{competitor_context}
{links_section}
{external_links_section}
Ground your post in the knowledge base and product documentation above. If they contain specific features, merchant use cases, flows, or product details relevant to this topic, incorporate them naturally. Do not invent facts or statistics not present in these sources or the system prompt.

Remember: include {internal_links_count} internal backlinks from the URL list above and exactly 3 external links from the External Link Library above. All links must be woven naturally into the content — never listed at the end.

OUTPUT LENGTH REQUIREMENT: The content field must be 900–1100 words maximum (body only, excluding FAQ). Each FAQ answer must be 2–4 sentences. Do not pad or over-explain — concise and factual is better. The entire JSON response must fit within a reasonable token budget.

Return the JSON object now."""

    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        metadata={"user_id": "blog-generation"}
    )

    if response.stop_reason == "max_tokens":
        raise ValueError(f"Response hit the max_tokens limit ({max_tokens}). Increase max_tokens and retry.")

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    raw = raw.strip()

    try:
        post_data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            post_data = json.loads(repair_json(raw))
        except Exception as e:
            raise ValueError(f"Response could not be parsed after repair attempt. JSON error: {e}")

    # Add metadata
    post_data["date"] = date.today().isoformat()
    post_data["keyword"] = keyword
    post_data["country"] = country or ""
    post_data["status"] = "generated"
    post_data["brand"] = brand

    # Ensure slug is clean
    if not post_data.get("slug"):
        post_data["slug"] = slugify(post_data["title"])
    else:
        post_data["slug"] = slugify(post_data["slug"])

    link_warnings = _validate_blog_links(post_data.get("content", ""), blog_links, blog_base_url=brand_config.blog_base_url)
    if link_warnings:
        post_data["link_warnings"] = link_warnings
        status(f"Link warnings: {len(link_warnings)} issue(s) found")

    return post_data


def _scrape_blog_url(url: str) -> dict:
    """Fetch a HitPay blog page and return {title, keyword, content} as plain text."""
    import httpx
    from bs4 import BeautifulSoup

    resp = httpx.get(url, timeout=20, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HitPayRewriter/1.0)"
    })
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove nav, footer, scripts, styles
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find the article title
    title = ""
    for sel in ["h1", "article h2", ".post-title", ".entry-title", "title"]:
        el = soup.select_one(sel)
        if el:
            title = el.get_text(strip=True)
            break

    # Try to find the main article body
    body_el = soup.select_one("article") or soup.select_one("main") or soup.body
    content = body_el.get_text(separator="\n", strip=True) if body_el else soup.get_text(separator="\n", strip=True)

    # Derive keyword from title (strip common suffixes)
    keyword = title or url.split("/")[-1].replace("-", " ").strip()

    return {"title": title, "keyword": keyword, "content": content}


def rewrite_blog_post(url: str, country: str = None, on_status=None) -> dict:
    """Scrape an existing blog post URL and rewrite it with all optimisation directives.

    Args:
        url: Public URL of the existing HitPay blog post
        country: Optional market code (SG/MY/PH) to lock the rewrite to a market
        on_status: Optional callback(message: str) for progress updates
    """
    def status(msg):
        if on_status:
            on_status(msg)

    status("Fetching existing blog post...")
    scraped = _scrape_blog_url(url)
    keyword = scraped["keyword"]
    existing_title = scraped["title"]
    existing_content = scraped["content"]
    status(f"Fetched: \"{existing_title}\"")

    # Gather the same enrichment context as a fresh generate
    status("Querying HitPay knowledge base...")
    mcp_context = _gather_mcp_context(keyword, status)

    status("Loading relevant product documentation...")
    product_docs = _load_relevant_docs(keyword)
    if product_docs:
        status("Found relevant sections in product docs")

    status("Loading competitor research...")
    country_name = COUNTRY_CONTEXT[country]["name"] if country and country in COUNTRY_CONTEXT else None
    competitors = get_relevant_competitors(keyword, market=country_name)
    competitor_context = format_for_prompt(competitors) if competitors else ""
    if competitors:
        status(f"Found data for {len(competitors)} relevant competitors")

    blog_links = _load_blog_links()
    links_section = ""
    if blog_links:
        other_markets = {"SG", "MY", "PH"} - ({country} if country else set())
        def _link_ok_rw(link):
            markets = link.get("markets", [])
            if not markets:
                return True
            for m in markets:
                if m in ("SEA", "Global") or m == country:
                    return True
            return not any(m in other_markets for m in markets) or any(
                m in ("SEA", "Global") for m in markets
            )
        filtered_links = [l for l in blog_links if _link_ok_rw(l)]
        links_section = "\n## HitPay URLs — Use 3 as Internal Backlinks\n"
        links_section += f"Market: {country or 'SEA'}. Pick the 3 most relevant URLs. Link naturally in-content — never force a link or dump as a list.\n\n"
        for link in filtered_links:
            topics_str = ", ".join(link.get("topics", []))
            markets_str = "/".join(link.get("markets", []))
            links_section += f"- [{link['title']}]({link['url']}) [{markets_str}] — {topics_str}\n"

    country_section = ""
    if country and country in COUNTRY_CONTEXT:
        ctx = COUNTRY_CONTEXT[country]
        avoid_list = "\n".join(f"  - {r}" for r in ctx["avoid"])
        ph_terminology = "\nTERMINOLOGY — Philippines market uses \"SMEs\" not \"SMBs\". Replace every instance of \"SMB\" or \"SMBs\" with \"SME\" or \"SMEs\" throughout the post.\n" if country == "PH" else ""
        country_section = f"""
## Country Focus: {ctx['flag']} {ctx['name']} ({country}) — STRICT REQUIREMENT
This post must be written EXCLUSIVELY for the {ctx['name']} market.

Local payment methods to reference: {ctx['local_methods']}
Cross-border methods available to {ctx['name']} merchants: {ctx['cross_border']}
Currency: {ctx['currency']}
Payout: {ctx['payout']}
Place name examples: {ctx['places']}

FACT CHECK — Do NOT include these market mismatches:
{avoid_list}
{ph_terminology}
Before returning your JSON, verify every payment method name, currency, and place name is correct for {ctx['name']}. Correct any mismatches.
"""
        status(f"Country focus set to {ctx['flag']} {ctx['name']}")

    system_prompt = BLOG_SYSTEM_PROMPT_AUTHORITY
    status("Rewriting blog post with Claude Opus...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    docs_section = f"\n## HitPay Product Documentation — Feature & Flow Accuracy\n{product_docs}\n" if product_docs else ""
    external_links_section = _build_external_links_section(country, keyword)

    user_prompt = f"""You are rewriting an existing HitPay blog post. The goal is to produce a significantly improved version of the same article using all system prompt directives (AEO optimisation, GEO rules, competitor comparisons, internal backlinks, etc.).

## Existing Article to Rewrite
URL: {url}
Title: {existing_title}

--- EXISTING CONTENT START ---
{existing_content[:6000]}
--- EXISTING CONTENT END ---

Keep the same core topic and keyword focus: "{keyword}"
Preserve any accurate facts, data points, or useful examples from the original.
Remove outdated information, weak sections, and anything that violates the system prompt rules.
Fully apply all AEO, GEO, and competitor comparison directives from the system prompt.
{country_section}
## HitPay Knowledge Base — Use for Factual Accuracy
{mcp_context}
{docs_section}
{competitor_context}
{links_section}
{external_links_section}
Ground your rewrite in the knowledge base and product documentation above. Do not invent facts or statistics not present in these sources or the system prompt.

Remember: include exactly 3 internal backlinks from the HitPay URL list above and exactly 3 external links from the External Link Library above. All links must be woven naturally into the content — never listed at the end.

OUTPUT LENGTH REQUIREMENT: The content field must be 900–1100 words maximum (body only, excluding FAQ). Each FAQ answer must be 2–4 sentences. Do not pad or over-explain — concise and factual is better. The entire JSON response must fit within a reasonable token budget.

Return the JSON object now."""

    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        metadata={"user_id": "blog-rewrite"}
    )

    if response.stop_reason == "max_tokens":
        raise ValueError(f"Response hit the max_tokens limit ({max_tokens}). Increase max_tokens and retry.")

    raw = response.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    raw = raw.strip()

    try:
        post_data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            post_data = json.loads(repair_json(raw))
        except Exception as e:
            raise ValueError(f"Response could not be parsed after repair attempt. JSON error: {e}")
    post_data["date"] = date.today().isoformat()
    post_data["keyword"] = keyword
    post_data["country"] = country or ""
    post_data["status"] = "generated"
    post_data["source_url"] = url

    if not post_data.get("slug"):
        post_data["slug"] = slugify(post_data["title"])
    else:
        post_data["slug"] = slugify(post_data["slug"])

    link_warnings = _validate_blog_links(post_data.get("content", ""), blog_links)
    if link_warnings:
        post_data["link_warnings"] = link_warnings
        status(f"Link warnings: {len(link_warnings)} issue(s) found")

    return post_data


def _gather_mcp_context(keyword: str, status_cb=None) -> str:
    """Query HitPay MCP to gather relevant knowledge for the keyword."""
    parts = []

    queries = [
        (keyword, "all", 5),
        (keyword, "product", 3),
        (keyword, "guide", 3),
    ]

    for query, category, limit in queries:
        label = f"[{category}]" if category != "all" else "[general]"
        try:
            result = search_knowledge(query, category=category, limit=limit)
            if result and not result.get("error"):
                parts.append(f"### Knowledge {label}: '{query}'")
                parts.append(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception:
            pass

    if not parts:
        return "No specific knowledge base results found. Use general HitPay knowledge from your system prompt."

    return "\n\n".join(parts)
