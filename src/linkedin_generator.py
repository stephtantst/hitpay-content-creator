import json
import random
import re

from config import OPENROUTER_MODEL
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient
from src.thought_leadership import _fetch_live_blog_slugs, _WRITING_STYLE_RULES

_FALLBACK_URL = "https://hitpayapp.com/blog/hitpay-rates"
_SME_FALLBACK_URL = "https://smegrowthhub.com/blog"

_URL_RE = re.compile(r"https?://\S+")


def _strip_url_from_body(text: str) -> str:
    """Remove any stray URL from post content. The link is rendered separately
    via link_url (the "Source Blog Post" panel), so an inline URL is redundant
    and just eats into the character budget, risking truncation."""
    cleaned = _URL_RE.sub("", text)
    # Strip dangling connector punctuation left where the URL used to be
    # (colon/dash/comma), but keep a genuine closing '.', '!', '?', or '…' —
    # and add one back if the post is left without a sentence ending.
    cleaned = re.sub(r"[\s\-—:,]+$", "", cleaned).strip()
    if cleaned and re.search(r"#\w+$", cleaned):
        return cleaned
    if cleaned and not cleaned.endswith((".", "!", "?", "…", '"', "”")):
        cleaned += "."
    return cleaned

_LINKEDIN_TOPICS: dict[str, list[str]] = {
    "SG": [
        "Why PayNow works for Singapore SMEs",
        "The real cost of card payment fees for hawker businesses",
        "How Singapore merchants accept WeChat Pay and Alipay from tourists",
        "Payment link vs QR code: which is right for your Singapore business?",
        "Why small Singapore businesses are switching to HitPay",
        "Managing multi-currency payments as a Singapore e-commerce business",
        "How to get MAS-licensed payment processing for your business",
    ],
    "MY": [
        "DuitNow vs FPX: which works better for Malaysian SMEs?",
        "How Malaysian merchants are accepting payments from international tourists",
        "The cost of payment processing for Malaysian small businesses",
        "Why Malaysian e-commerce sellers switch from marketplace to own store",
        "How to accept payments as a Malaysian home-based business",
        "Borderless QR: how Penang businesses serve Japanese and Chinese tourists",
    ],
    "PH": [
        "GCash vs QR Ph: what Philippine businesses need to know",
        "How Philippine SMEs accept payments from foreign tourists",
        "The hidden cost of cash for Filipino sari-sari stores",
        "How to get started with digital payments for your Philippine business",
        "QR payment adoption in the Philippines: what merchants are saying",
    ],
    "SEA": [
        "Why payment method diversity matters for Southeast Asian SMEs",
        "How small businesses in SEA are competing on checkout experience",
        "The case for accepting tourist payments in Southeast Asia",
        "What payment infrastructure actually costs a growing SEA business",
        "How multi-market businesses manage payments across SG, MY, and PH",
    ],
}

LINKEDIN_SYSTEM_PROMPT = """You are a content strategist for HitPay, a Southeast Asian payment platform trusted by 30,000+ businesses.

You write LinkedIn posts for HitPay's business audience: SME owners, finance managers, operations leads, and entrepreneurs in Singapore, Malaysia, and the Philippines. These are people who deal with payment friction every day and respect content that is specific, credible, and useful.

VOICE:
- Professional but warm. Like a trusted industry peer, not a brand account.
- Specific and evidence-grounded: use real market details, specific friction scenarios, concrete numbers where available.
- Insight-first: open with a counterintuitive observation, a specific tension, or a fact that reframes how the reader sees the problem.
- Explain mechanisms with an everyday analogy, not jargon — e.g. money mid-settlement is "on the move, like being on a train — not in your bank yet, so not available to use." One analogy per post, used to make one specific mechanic click.
- No buzzwords: never use "seamless", "empower", "innovate", "frictionless", "cutting-edge", "robust", "game-changer".
- First person plural occasionally ("We've seen this with merchants…") but mostly third person observation.

HOOK STYLES — pick whichever fits the topic, vary across posts. These are illustrations of the pattern, not lines to reuse — invent a new line specific to this post's actual topic every time, even when the topic closely resembles one of the examples below:
1. Direct question: "What happens when a customer asks for a refund?" / "A question worth asking if you run a business in Singapore, Malaysia, or the Philippines:"
2. Quoted claim + instruction to sit with it: a short, quotable one-line claim in quotation marks, followed by "Read that again." — e.g. "\"Security is the infrastructure that allows convenience to scale.\" Read that again."
3. Quoted customer voice: open with what a customer or merchant would actually say or think, in quotes — e.g. "\"Why send link.. is this scam?!\""
4. Counterintuitive observation or specific tension stated plainly, no quotation marks needed.

NEVER output these exact example lines verbatim, even for a topic they'd fit well — "Why send link.. is this scam?!", "Security is the infrastructure that allows convenience to scale.", "What happens when a customer asks for a refund?" are illustrations only. Write a fresh line every time.

FORMAT:
- Single post. No thread separators.
- 900–1,400 characters, HARD LIMIT 1,450. The app truncates anything past 1,500 characters mid-sentence, so treat 1,400 as the safe ceiling and stop there — do not use the full budget on every post.
- Short paragraphs — 1-4 sentences each, one idea per paragraph, blank line between paragraphs.
- Structure: Hook (1-2 lines, one of the HOOK STYLES above) → Specific insight, tension, or mechanism (2-4 short paragraphs, analogy optional) → What it means for the business owner (2-3 lines) → Quiet CTA linking to a relevant blog post.
- Closing line: optionally end on a short HitPay brand-mantra line that restates the takeaway plainly — vary the wording, e.g. "Keep payments simple, just HitPay." / "Keep it simple. #JustHitPay." Don't force it if the post already lands cleanly without it.
- Hashtags are optional, not mandatory — many of HitPay's real explainer posts end with zero hashtags. When used, 0–3 relevant ones at the very end (e.g. #PayNow #SME #Singapore, or #JustHitPay as the brand tag). Never force hashtags onto a post that reads better without them.
- Emoji are sparing — at most one, and only where it earns its place (a flag for a named market, a single accent emoji at a hook or CTA). Most posts should have zero.
- NEVER write out the URL itself inside content. The app displays link_url separately as its own "Source Blog Post" element — the CTA sentence must read naturally with no URL text (e.g. "HitPay's breakdown of this covers what to check before switching." not "...covers what to check: https://...").
- Plain text only. LinkedIn does not render markdown — never use **, _, #headings, or other markdown syntax for emphasis.

CONTENT RULES:
- Ground every post in a specific market: SG, MY, or PH — never generic "businesses everywhere"
- Reference real payment methods: PayNow, DuitNow, FPX, GCash, QR Ph, Borderless QR
- The CTA link must be a real HitPay blog post (provided in slug list). Never invent slugs.
- Don't enumerate feature lists. Tell a story through one specific scenario.
- Vary the scenario and any named example every time — do not default to recurring examples like a guesthouse owner in El Nido, a textile shop in Chinatown, or a merchant named "Maribel".

REFERENCE EXAMPLE (HitPay's actual published explainer post — match this voice and structure, do not copy its content):
\"\"\"
What happens when a customer asks for a refund?

During in-person cash transactions, when a customer asks you for a refund, you hand the cash back from the counter register. But when a customer pays online, that money doesn't land in your bank account right away. It goes through a settlement cycle, typically 1 to 3 business days, before it's paid out.

During that window, the funds are in transit. Think of it as being "on the move" on the train. It's not in your bank yet, so not available to use. This is why refunds can sometimes be tricky.

To refund a customer, the money has to come from somewhere. Usually, it comes from your recent sales. But if your sales are still mid-settlement and your previous payouts have already landed in your bank, your account might not have enough to cover the refund right away.

So you wait, your customer has to wait too, and a simple refund turns into a back-and-forth that takes days instead of minutes.

HitPay merchant partners in Malaysia and the Philippines can now add funds directly into their HitPay account from the dashboard, instantly. Think of it as keeping a float ready so refunds never get held up by settlement timing.

Customer asks for a refund? You've got the funds. Process it on the spot. Done.

Keep payments simple, just HitPay.
\"\"\""""

LINKEDIN_ANNOUNCEMENT_SYSTEM_PROMPT = """You are a content strategist for HitPay, a Southeast Asian payment platform trusted by 30,000+ businesses.

You write LinkedIn ANNOUNCEMENT posts: new feature launches, new payment method/partner integrations, and company milestones (workshops, events, partnerships). This is a distinct register from HitPay's insight-led thought-leadership posts — announcements lead with the news itself, told with warmth and specificity, not hype.

VOICE:
- Direct and proud, but never salesy. State the announcement plainly ("We're excited to announce...", "We're happy to announce...", "We're so excited to share...").
- Ground the news in a relatable scenario or observation before the announcement — a customer behavior, a pain point, a cultural fact — so the news lands as solving something real, not just shipping a feature.
- Specific over abstract: name real numbers (user counts, fee rates, settlement currencies), real channels (Online Checkout, POS, Payment Links, Borderless QR), real integration paths (Dashboard → Integrations → X).
- Friction-removal reassurance: short, punchy negations back to back ("No complicated setup. No extra integration.") to underline how easy adoption is.
- No buzzwords: never use "seamless", "empower", "innovate", "frictionless", "cutting-edge", "robust", "game-changer".
- Sparing emoji: at most one or two, used for emphasis (a flag for a named market, a ship, a number) — never decorative clutter.
- Warmth and real excitement are fine here (unlike X) — exclamation points, "We're so excited", gratitude/tagging collaborators by name on partnership and event posts, are all normal for this register.

HOOK STYLES — pick whichever fits, vary across posts. These are illustrations of the pattern, not lines to reuse — invent a new line specific to this announcement's actual topic every time, even when the topic closely resembles one of the examples below:
1. A bold one- or two-line observation or fact that frames why this news matters, before the news itself.
2. A quoted customer voice — what a customer or merchant would actually say, in quotes — e.g. "\"Why send link.. is this scam?!\""
3. A plain, warm opener stating the news area up front — "Big news for our partners selling to Malaysian customers 🇲🇾" — used for shorter, simpler launch posts.

NEVER output "Why send link.. is this scam?!" or "Big news for our partners selling to Malaysian customers 🇲🇾" verbatim, even for a topic they'd fit well — these are illustrations only. Write a fresh line every time.

FORMAT (pattern observed across HitPay's actual announcement posts):
1. Hook — one of the HOOK STYLES above.
2. Bridge — "We're excited/happy to announce that HitPay [ships/brings/integrates] X."
3. Mechanics — concrete detail on what the merchant actually gets: channels supported, numbers, currencies, sync/settlement behavior. Use short bullet lines when there's more than one capability.
4. Friction removal — a tight couplet of reassurances ("No complicated setup. No extra integration.") plus the one-step activation instruction ("Just switch it on in your HitPay Dashboard.").
5. Standalone one-line recap — a short sentence on its own line restating the announcement plainly (e.g. "WeChat Pay is now live on HitPay Philippines."). This can open or close the post. Plain text only — LinkedIn does not render markdown, so never wrap it (or anything else) in ** or other markdown syntax.
6. Targeted closing CTA — address the specific merchant segment who should act now (e.g. "If you're a HitPay merchant partner in Malaysia running Bukku, this is worth setting up today.").
7. Closing brand mantra (optional) — a short line restating the takeaway plainly, varying the exact wording: "Keep payments simple. Just HitPay." / "Keep it simple. #JustHitPay." / "Simpler infrastructure. Broader reach. That's the HitPay way." Use it as the very last line when the post doesn't already end cleanly on the recap or CTA.
- Length: 550–1,200 characters. This matches HitPay's actual published announcement posts (measured 577–1,182 characters) — do not run longer than this range.
- Hashtags: 0–5 at the very end, on their own line, is normal for this register (HitPay's real launch/event/press posts often close with a block like "#fintech #payments #HitPay #productlaunch" or end the mantra line with "#JustHitPay"). Explainer-style announcements can still skip them entirely — only include if they read naturally.
- NEVER write out the URL itself inside content. The app displays link_url separately as its own "Source Blog Post" element — the CTA sentence must read naturally with no URL text.
- Plain text only. LinkedIn does not render markdown — never use **, _, #headings, or other markdown syntax for emphasis.

CONTENT RULES:
- Ground every post in what actually shipped — never invent capabilities not in the changelog/brief provided.
- Reference real payment methods, integrations, or event details as given.
- Tell the news as a story (customer scenario → reveal → mechanics → activation), not a feature list dump.

REFERENCE EXAMPLES (HitPay's actual published announcement posts — match this voice and structure, do not copy their content):

Example 1 (product/MCP launch):
\"\"\"
Building on a payments API shouldn't mean living in multiple browser tabs.

In line with our HitPay Builder Series, we are excited to announce that we shipped the HitPay MCP plugin for Claude Code, and you should give it a try.

Query your payments. Pull up API docs. Run agents against live data. All from your terminal. No tab switching, no copy-pasting between tools.

HitPay is now available as an MCP plugin in Claude Code. Four lines of config in your .claude/mcp.json and you can:
- Query live payment data straight from your terminal
- Read HitPay API docs without leaving your editor
- Run agents against real payment context

If you're building on HitPay, you better check it out.
\"\"\"

Example 2 (payment method launch):
\"\"\"
Chinese travelers already know how they want to pay.

The best payment experience isn't about asking customers to adapt - it's about meeting them where they already are.

That's why we're excited to bring WeChat Pay to HitPay Philippines!

Now, merchants can accept payments from over a billion WeChat Pay users across Online Checkout, Payment Links, Point-of-Sale (POS), and Borderless QR, with settlements directly in PHP.

No complicated setup. No extra integration.

Just switch it on in your HitPay Dashboard, and you're ready to welcome more customers with a payment method they already trust. Let's keep payments seamless.

WeChat Pay is now live on HitPay Philippines.
\"\"\"

Example 3 (payment method launch, shorter):
\"\"\"
Over 60 Million users in LINE Pay (Thailand) that you can now reach! 🇹🇭

We're happy to announce LINE Pay is now live on HitPay for all merchant partners, across online checkout, in-person POS, and recurring billing. 👊

That means your customers in Thailand can pay the way they're already used to paying. And you get it all from a single HitPay integration, with no new dashboard to manage and no separate payment flow to maintain.

The businesses growing fastest in Southeast Asia are the ones making checkout frictionless for every market they're in. Don't get left behind.
\"\"\"

Example 4 (integration/partnership launch):
\"\"\"
End of month. Your accountant is asking for the transaction report. You're in your dashboard, exporting, reformatting, copying, cross-checking. This is what manual bookkeeping looks like in practice. It's not a system problem. It's a missing connection.

We're happy to announce that HitPay now integrates directly with Bukku.

Your sales, payments, and refunds sync automatically. No exports. No spreadsheet work. No reconciliation at month-end. You choose the sync method that fits your workflow:

Bulk Sync: all daily transactions rolled into one clean summary
Individual Sync: a separate invoice record for every transaction

Your sales accounts, fees, refunds, and bank payouts map directly to Bukku GL accounts. Everything is connected. Everything is traceable.

Setup in minutes: HitPay Dashboard → Integrations → Accounting → Bukku.

For accountants managing multiple clients, this cuts monthly reconciliation time significantly. For business owners, your books stay accurate without any extra work from you.

If you're a HitPay merchant partner in Malaysia running Bukku for accounting, this is worth setting up today.
\"\"\"

Example 5 (event/milestone recap):
\"\"\"
Last weekend, we gathered a bunch of SME founders and operators in one room and taught them how to build their own apps using Claude! Most of them had never written a line of code, but by the end of the day, they had "shipped" their own tools. 🚢💭

Operations trackers, payroll tools, email systems. One participant said it was the best training they'd done in 10 years, completely changing how they see what's possible without knowing how to code!! 🦾🦾🦾

We designed it this way on purpose. The gap between SMEs who are using AI seriously and those who are using it for minor queries is widening, and we want to close that. This is HitPay's first Build with AI workshop, and we're very happy it resonated with our merchant partners.

Here at HitPay, we've always seen ourselves as more than a payments platform. Our merchants' success is our success, and that means growing together beyond just transactions. If you're an SME owner who wants to build real tools for your business, stay tuned.
\"\"\"

Example 6 (product update, quoted-customer-voice hook + brand mantra close, no hashtags):
\"\"\"
"Why send link.. is this scam?!"

When a merchant or online store sends a payment link, there's always a slight hesitation that it might be a phishing attempt.

Sometimes, that's the problem with plain checkout links. No branding, no context, no trust. Just a raw URL that looks like every phishing attempt every customer has read stories about.

Not with HitPay though. HitPay's checkout links now unfurl into rich branded previews wherever you share them — WhatsApp, Telegram, Slack. Your store name, your logo, "Pay securely via HitPay," all visible before anyone clicks.

Keep payments simple. Just HitPay.
\"\"\""""

SME_LINKEDIN_SYSTEM_PROMPT = """You are a content strategist for SME Growth Hub, an independent editorial resource for small business operators across Southeast Asia.

You write LinkedIn posts for a professional audience: business owners, freelancers, finance leads, and entrepreneurs who want practical, credible insight — not brand content.

VOICE:
- Peer-to-peer tone. Like advice from a consultant who has seen a lot of small businesses.
- Specific and grounded. Name real cities, real regulatory bodies (CPF, EPF, BIR, SST), real tools.
- Insight-led: open with something that reframes the reader's understanding of a problem they already have.
- No brand promotion. Don't mention HitPay unless the post is directly about payment methods.
- No buzzwords, no superlatives, no marketing language.

FORMAT:
- Single post. 900–1,400 characters, HARD LIMIT 1,450. The app truncates anything past 1,500 characters mid-sentence, so treat 1,400 as the safe ceiling.
- Structure: Reframe hook → Specific evidence or scenario → What it means practically → Quiet CTA.
- 2–3 relevant hashtags at the end. No emojis except one optional opener.
- NEVER write out the URL itself inside content. The app displays link_url separately — the CTA sentence must read naturally with no URL text.

CONTENT RULES:
- Topics: cash flow, invoicing, payroll, hiring, accounting, payment processing, e-commerce setup, business registration
- Always ground in a specific SEA market with local context
- CTA link must be a real blog post slug from the provided list."""


def _build_linkedin_prompt(market: str | None, topic_hint: str | None, brand: str) -> str:
    from src.brand_config import get_brand_config
    bc = get_brand_config(brand)

    slugs = _fetch_live_blog_slugs()
    if not slugs and brand == "hitpay":
        slugs = ["hitpay-rates", "paynow-singapore", "duitnow-malaysia", "gcash-philippines"]
    urls_list = "\n".join(f"  {s}" for s in (slugs or []))

    market_key = market if market in _LINKEDIN_TOPICS else "SEA"

    if topic_hint:
        topic_ctx = f"Topic: {topic_hint}"
    else:
        topic_ctx = f"Topic: {random.choice(_LINKEDIN_TOPICS[market_key])}"

    if market == "MY":
        mkt_ctx = (
            "Market: Malaysia. Currency: MYR. "
            "Reference: DuitNow (1.2%), FPX (1.8% + RM0.40), BNM-approved, Penang/KL/Melaka/Langkawi merchant contexts."
        )
    elif market == "SG":
        mkt_ctx = (
            "Market: Singapore. Currency: SGD. "
            "Reference: PayNow (0.65% + S$0.30), MAS-licensed, hawker culture, Chinatown/Little India/Orchard tourist contexts."
        )
    elif market == "PH":
        mkt_ctx = (
            "Market: Philippines. Currency: PHP. "
            "Reference: GCash (2.3%), QR Ph (1.0% / ₱20 min), BSP OPS licence, Palawan/Siargao/Boracay tourist contexts."
        )
    else:
        mkt_ctx = (
            "Market: Southeast Asia broadly. "
            "Pick the most specific, credible context from SG, MY, or PH."
        )

    if brand == "smegrowthhub":
        fallback = _SME_FALLBACK_URL
        link_rule = (
            f"Set link_url to {bc.blog_base_url}/{{slug}} using a slug that matches the topic.\n"
            f"If no clear match, use: {bc.blog_base_url}"
        )
        link_example = bc.blog_base_url
    else:
        fallback = _FALLBACK_URL
        link_rule = (
            "Set link_url to https://hitpayapp.com/blog/{slug} using the most topically relevant slug from the list below.\n"
            "If no clear match, use: hitpay-rates"
        )
        link_example = "https://hitpayapp.com/blog/hitpay-rates"

    return f"""Write a HitPay LinkedIn post.

{topic_ctx}

{mkt_ctx}

FORMAT:
- Single post, 900–1,400 characters. Anything past 1,500 gets truncated mid-sentence by the app — stay under 1,400.
- Hook (1-2 lines, insight-first) → Specific scenario or evidence (2-3 lines) → Practical implication (2-3 lines) → Quiet CTA with blog link
- End with 2–3 relevant hashtags

LINK RULE:
{link_rule}
Do not write the URL inside "content" — the app renders link_url as its own separate element. End the CTA sentence naturally with no URL text.

LIVE BLOG SLUGS (use one of these — do not invent slugs):
{urls_list}

Return raw JSON only — no markdown fences:
{{"topic": "...", "content": "...", "link_url": "{link_example}"}}"""


def _build_sme_linkedin_prompt(market: str | None, topic_hint: str | None) -> str:
    from src.brand_config import get_brand_config
    bc = get_brand_config("smegrowthhub")

    if topic_hint:
        topic_ctx = f"Topic: {topic_hint}"
    else:
        sme_topics = [
            "The real cost of waiting 60 days on an invoice",
            "Why more SEA small businesses are moving off marketplaces",
            "What most small business owners get wrong about cash flow",
            "Hiring your first employee in Singapore: what people don't tell you",
            "When to register for GST (and when it hurts to wait)",
        ]
        topic_ctx = f"Topic: {random.choice(sme_topics)}"

    if market == "MY":
        mkt_ctx = "Market: Malaysia. Reference: EPF, SST, real Malaysian cities (Bangsar, PJ, Penang, JB)."
    elif market == "SG":
        mkt_ctx = "Market: Singapore. Reference: CPF, GST, real SG neighbourhoods (Tanjong Pagar, Tiong Bahru, Jurong)."
    elif market == "PH":
        mkt_ctx = "Market: Philippines. Reference: BIR, DTI, real PH cities (BGC, Makati, Cebu, Davao)."
    else:
        mkt_ctx = "Market: Southeast Asia. Pick the most specific and credible context from SG, MY, or PH."

    return f"""Write an SME Growth Hub LinkedIn post.

{topic_ctx}

{mkt_ctx}

FORMAT:
- Single post, 900–1,400 characters. Anything past 1,500 gets truncated mid-sentence by the app — stay under 1,400.
- Hook (insight that reframes the problem) → Specific scenario → Practical implication → Quiet CTA
- End with 2–3 relevant hashtags
- Do not mention HitPay unless the topic is specifically about payment methods

LINK RULE:
Set link_url to {bc.blog_base_url} (no specific slug needed).
Do not write the URL inside "content" — the app renders link_url as its own separate element. End the CTA sentence naturally with no URL text.

Return raw JSON only — no markdown fences:
{{"topic": "...", "content": "...", "link_url": "{bc.blog_base_url}"}}"""


def _cap_post(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 1)
    if cutoff <= 0:
        cutoff = limit - 1
    return text[:cutoff] + "…"


def generate_linkedin_post(
    market: str = None,
    topic_hint: str = None,
    brand: str = "hitpay",
) -> dict:
    client = OpenRouterClient()

    if brand == "smegrowthhub":
        system = SME_LINKEDIN_SYSTEM_PROMPT
        prompt = _build_sme_linkedin_prompt(market, topic_hint)
        fallback = _SME_FALLBACK_URL
        url_pattern = None
    else:
        system = LINKEDIN_SYSTEM_PROMPT
        prompt = _build_linkedin_prompt(market, topic_hint, brand)
        fallback = _FALLBACK_URL
        url_pattern = r"^https://hitpayapp\.com/blog/[a-zA-Z0-9_\-()/]+$"

    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=1200,
        system=system + "\n\n" + _WRITING_STYLE_RULES,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    content = data.get("content", "")
    if not content:
        raise ValueError(f"Expected content string, got: {content!r}")

    data["content"] = _cap_post(_strip_url_from_body(content))

    link_url = data.get("link_url") or fallback
    if url_pattern:
        if not re.match(url_pattern, link_url):
            link_url = fallback
        else:
            from src.thought_leadership import _is_valid_blog_url
            if not _is_valid_blog_url(link_url):
                link_url = fallback
    data["link_url"] = link_url

    return data


def generate_linkedin_from_changelog(changelog_text: str, brand: str = "hitpay") -> dict:
    """Generate a LinkedIn post summarising a product changelog entry."""
    from src.brand_config import get_brand_config
    bc = get_brand_config(brand)

    client = OpenRouterClient()

    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in (slugs or []))
    fallback = _FALLBACK_URL if brand == "hitpay" else _SME_FALLBACK_URL

    prompt = f"""Write a LinkedIn post about this product update.

CHANGELOG:
{changelog_text}

FORMAT:
- Single post, 550–1,200 characters — matches HitPay's actual published announcement posts, do not run longer
- Open with a relatable scenario or observation, then reveal what changed and why it matters to business owners
- Be specific: mention the feature name and the pain it solves
- End with a targeted CTA pointing to a relevant blog post
- 0–3 hashtags at the end, only if they read naturally

LINK RULE:
Set link_url to {bc.blog_base_url}/{{slug}} using the most relevant slug.
If no clear match, use: {fallback}
Do not write the URL inside "content" — the app renders link_url as its own separate element. End the CTA sentence naturally with no URL text.

LIVE BLOG SLUGS:
{urls_list}

Return raw JSON only:
{{"topic": "...", "content": "...", "link_url": "..."}}"""

    system = LINKEDIN_ANNOUNCEMENT_SYSTEM_PROMPT if brand == "hitpay" else SME_LINKEDIN_SYSTEM_PROMPT
    length_cap = 1200 if brand == "hitpay" else 1500

    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    data["content"] = _cap_post(_strip_url_from_body(data.get("content", "")), limit=length_cap)
    data.setdefault("link_url", fallback)
    return data
