import json
import random
import re

from config import OPENROUTER_MODEL
from src.generator import _messages_create_with_retry
from src.llm_client import OpenRouterClient
from src.thought_leadership import _fetch_live_blog_slugs, _WRITING_STYLE_RULES

_FALLBACK_URL = "https://hitpayapp.com/blog/hitpay-rates"
_SME_FALLBACK_URL = "https://smegrowthhub.com/blog"

# (business_type, location, customer_type, hitpay_product)
_STORY_SEEDS: dict[str, list[tuple]] = {
    "MY": [
        ("textile shop", "Penang George Town", "tourists from Japan, South Korea, and China", "Borderless QR"),
        ("batik fabric shop", "Melaka", "tourists from mainland China", "Borderless QR"),
        ("night market vendor", "Kuala Lumpur", "customers who only had foreign cards or cash", "payment link"),
        ("homestay", "Cameron Highlands", "foreign guests booking from abroad", "payment link"),
        ("kopitiam", "Ipoh", "regular customers transitioning from cash to QR", "HitPay QR"),
        ("handicraft shop", "Langkawi", "duty-free shoppers and international tourists", "Borderless QR"),
        ("dental clinic", "Petaling Jaya", "patients wanting to pay in instalments", "payment links"),
        ("agritourism farm", "Kota Belud, Sabah", "visitors booking day-tour packages online from abroad", "payment link"),
        ("halal catering company", "Shah Alam", "corporate clients settling event invoices", "payment links"),
        ("homeware online seller", "Selangor", "buyers placing orders via Instagram DM", "payment link"),
        ("heritage guesthouse", "Georgetown, Penang", "guests booking directly from abroad", "payment link"),
        ("tuition centre", "Subang Jaya", "parents paying monthly tuition fees", "recurring payment links"),
        ("night market vendor", "Kota Kinabalu", "tourists and locals mixing cash with e-wallets", "DuitNow QR"),
    ],
    "SG": [
        ("heritage craft shop", "Chinatown", "tourists from mainland China and Japan", "Borderless QR"),
        ("hawker stall", "Maxwell Food Centre", "lunchtime office workers", "HitPay QR"),
        ("provision shop", "Toa Payoh", "longtime regulars and younger neighbours", "QR payments"),
        ("tailoring shop", "Little India", "customers ordering custom pieces from abroad", "payment link"),
        ("florist", "Tiong Bahru", "corporate clients with recurring flower subscriptions", "payment links"),
        ("bookshop", "Bukit Timah", "parents paying for tuition materials", "payment links"),
        ("co-working space", "one-north", "freelancers and startup teams on monthly desk plans", "payment links"),
        ("physiotherapy clinic", "Clementi", "patients booking one-off sessions or prepaid packages", "payment links"),
        ("kids activity studio", "Holland Village", "parents booking trial classes and term enrolments", "payment links"),
        ("specialty coffee roaster", "Kallang", "wholesale café clients on monthly standing orders", "recurring payment links"),
        ("online furniture maker", "Ubi", "customers paying deposits and balance instalments on custom pieces", "payment links"),
        ("bubble tea shop", "Jurong East", "school students and office workers switching from cash to QR", "HitPay QR"),
    ],
    "PH": [
        ("beach resort", "Panglao, Bohol", "foreign tourists from the US, Australia, and Europe", "Borderless QR"),
        ("craft market stall", "Intramuros, Manila", "tourists using WeChat Pay or Alipay", "Borderless QR"),
        ("boutique hotel", "Vigan, Ilocos Sur", "domestic tourists visiting the heritage district", "payment link"),
        ("sari-sari store", "Quezon City", "neighbourhood regulars", "GCash QR"),
        ("dive shop", "Moalboal, Cebu", "foreign divers paying in USD, EUR, or other wallets", "Borderless QR"),
        ("food stall", "Baguio night market", "domestic tourists paying by e-wallet", "GCash QR"),
        ("bakery", "Davao City", "OFW families paying with remittance funds", "GCash QR"),
        ("pearl and jewelry stall", "Puerto Princesa, Palawan", "cruise-ship tourists on a short shore stop", "Borderless QR"),
        ("coffee shop", "Tagaytay", "weekend day-trippers from Manila", "QR payments"),
        ("tricycle-terminal store", "Iloilo City", "commuters switching from coins to e-wallets", "GCash QR"),
        ("farm-to-table restaurant", "Davao City", "diners and corporate lunch clients booking weekly", "payment link"),
        ("balikbayan box agent", "Tondo, Manila", "overseas families sending parcels and payments from abroad", "payment link"),
        ("community bakery", "Iloilo City", "wholesale buyers and walk-in neighbourhood customers", "GCash QR"),
        ("surf charter operator", "General Luna, Siargao", "foreign surfers paying in USD or AUD before arrival", "Borderless QR"),
        ("dry goods store", "Cagayan de Oro", "market traders and sari-sari owners restocking in bulk", "GCash QR"),
    ],
    "SEA": [
        ("small textile shop", "a heritage port city", "international tourists", "Borderless QR"),
        ("family-run guesthouse", "a coastal town", "guests booking from abroad", "payment link"),
        ("craft workshop", "an old town district", "tourists and collectors from overseas", "Borderless QR"),
        ("street food stall", "a city night market", "digital-first customers", "QR payments"),
        ("boutique dive operator", "an island destination", "foreign divers without local cash", "Borderless QR"),
    ],
}

_CONTENT_APPROACHES: list[dict] = [
    {
        "name": "observation",
        "instruction": (
            "Write as a direct observation — a pattern HitPay notices across businesses. "
            "No named merchant, no invented scenario. Open with the insight itself. "
            "Post 2: context — which businesses this affects and why it plays out this way. "
            "Post 3: how HitPay addresses it. End with [URL] naturally in a sentence."
        ),
    },
    {
        "name": "calculation",
        "instruction": (
            "Open with a specific number or cost comparison. Use only verified HitPay rates or widely known market rates. "
            "Post 2: which business types this calculation matters to most, and what the trade-off looks like. "
            "Post 3: what HitPay enables across payment options. End with a practical prompt and [URL]."
        ),
    },
    {
        "name": "common_problem",
        "instruction": (
            "Name the friction point directly in Post 1 — no fictional character needed. "
            "What situation are many businesses in? "
            "Post 2: what businesses typically do about it, and why that workaround is imperfect. "
            "Post 3: the simpler option. HitPay appears briefly and practically. End with [URL]."
        ),
    },
    {
        "name": "practical_tip",
        "instruction": (
            "Open by framing what you're sharing — a specific thing a business owner can consider or do. "
            "Post 2: which business types benefit, when this is most useful. Use a short list of categories, not a single example. "
            "Post 3: how to get started with HitPay. End with [URL] in a natural sentence — not a pressure CTA."
        ),
    },
    {
        "name": "product_use_case",
        "instruction": (
            "Open with the problem the feature solves — not the feature name. "
            "Post 2: which businesses this suits. Use a short list of real business categories. "
            "Post 3: how the feature works, briefly. End with [URL]."
        ),
    },
    {
        "name": "merchant_observation",
        "instruction": (
            "Open by naming a pattern HitPay has observed across businesses we work with. "
            "Use 'we' and 'businesses we work with' — not a single invented merchant. "
            "Post 2: what drives the pattern and what it costs businesses in practice. "
            "Post 3: what changes. HitPay appears briefly. End with [URL]."
        ),
    },
]

THREADS_SYSTEM_PROMPT = """You are writing short posts for HitPay on Meta Threads. HitPay is a payment platform for small businesses in Southeast Asia.

VOICE:
- Plain and direct. Write like someone at HitPay sharing something genuinely useful with business owners — not a copywriter constructing a case study.
- First person plural when HitPay is the narrator: "we", "businesses we work with", "what we see".
- No performing restraint. If a sentence sounds written to sound understated, rewrite it.

CONTENT APPROACHES — vary these. Do not default to a merchant case study every time:
- Observation: a pattern HitPay notices across businesses — stated directly
- Calculation: a number or cost comparison that changes how a business owner thinks about fees or trade-offs
- Common problem: a friction point many SMEs face, described plainly without a fictional character
- Practical tip: a specific thing a business owner can do or consider
- Product use case: how a HitPay feature works and which business types it suits
- Merchant observation: a real pattern we see — told as "we" — not a reconstructed fictional scenario

WHAT MAKES A POST WORK:
- Lead with the business problem or insight. The reader should understand why this matters from the first sentence, without needing a character to demonstrate it.
- When describing who a feature suits, use a short list of real business categories — not a single invented character.
- End the final post with [URL] in a natural sentence. "Here's how it works: [URL]" and "More on this: [URL]" are fine endings. Not every post needs a hard CTA.

WHAT TO AVOID:
- Invented merchant scenarios with fabricated names, made-up locations, invented dialogue, or invented results ("no-shows dropped", "the queue stopped backing up", "customers stopped walking away") — if it's made up, don't write it
- Fake specificity that sounds falsely reported: invented street addresses, specific dates tied to fictional events, proprietary business details you can't know
- The formula: Problem → fictional merchant with invented detail → HitPay → invented result → Link
- Repeated CTA formula: "worth a look", "worth enabling", "worth fixing" — vary the ending, or drop the CTA entirely
- "Linked in bio", "link in bio" — never write these. Use [URL] naturally in the final post.

PRODUCT CLAIMS:
Only state fees, payment methods, payout speeds, or user numbers when supported by verified HitPay data. Describe the category rather than state a specific figure if unsure.

DO NOT include: hashtags, emojis, marketing buzzwords (seamless, empower, innovate, frictionless, cutting-edge, robust)"""


# SME Growth Hub story seeds — broader than payment scenarios
# (business_type, location, scenario, lesson)
_SME_STORY_SEEDS: dict[str, list[tuple]] = {
    "SG": [
        ("freelance designer", "Tanjong Pagar", "chasing late invoices from a client", "setting clear payment terms"),
        ("hawker stall", "Maxwell Food Centre", "moving from cash to digital payments", "separating business and personal finances"),
        ("boutique F&B", "Tiong Bahru", "hiring first part-time staff", "understanding CPF obligations"),
        ("tuition centre", "Bukit Timah", "switching accounting tools mid-year", "finding one that handles GST"),
        ("provision shop", "Toa Payoh", "realising the cost of holding too much inventory", "cash flow forecasting"),
        ("home baker", "Jurong East", "growing from marketplace to own website", "managing payment processing fees"),
    ],
    "MY": [
        ("freelance consultant", "Bangsar", "waiting 60 days on a RM8,000 invoice", "invoice payment terms"),
        ("kopitiam", "Petaling Jaya", "hiring first employee and discovering EPF paperwork", "payroll compliance"),
        ("home-based baker", "Johor Bahru", "switching from just Shopee to own website", "e-commerce platform choices"),
        ("tailoring shop", "Bukit Bintang", "realising cash didn't add up at month end", "bookkeeping basics"),
        ("beauty salon", "KLCC", "first SST registration threshold reached", "accounting software choice"),
        ("event caterer", "Ipoh", "managing seasonality and cash gaps", "cash flow planning"),
    ],
    "PH": [
        ("freelance developer", "BGC", "a client who paid in three separate currencies", "cross-border payment options"),
        ("sari-sari store", "Quezon City", "tracking daily sales without a system", "basic bookkeeping"),
        ("online fashion shop", "Makati", "first hire through an agency vs direct", "hiring costs"),
        ("surf school", "Siargao", "accepting foreign tourists without peso", "payment methods for tourists"),
        ("home cook", "Cebu", "growing from Facebook Marketplace to proper setup", "business registration steps"),
        ("freelance photographer", "Davao", "late-paying client and what changed after", "invoice terms"),
    ],
    "SEA": [
        ("freelance consultant", "a city CBD", "an unpaid invoice that changed how they work", "payment terms"),
        ("small café", "a heritage neighbourhood", "the first hire that nearly broke the budget", "hiring costs"),
        ("online seller", "a growing city", "realising the marketplace was eating their margin", "own store vs marketplace"),
        ("home baker", "a suburban area", "cash that never seemed to add up", "basic bookkeeping"),
        ("event vendor", "a tourist town", "a season that left them short on cash", "cash flow management"),
    ],
}

SME_THREADS_SYSTEM_PROMPT = """You are the Threads storyteller for SME Growth Hub, an independent editorial resource for small business operators across Southeast Asia.

You write short-form narratives for Meta Threads — the kind that feel human, specific, and earned. Your stories are told from the perspective of the business owner or a peer who knows them well. They're about the daily friction, the workarounds, and what changes when a business owner learns something that actually helps.

VOICE:
- Warm and observational. Write as someone who has spent time listening to these business owners.
- Specific and concrete: name cities, name the human details (a stack of unpaid invoices, a notebook of hand-written totals, a phone call to chase a client).
- Understated. The emotional weight comes from detail, not adjectives.
- No superlatives. No marketing language. No claims. Just story.
- First person is fine: "I've seen this" or simply describe what happened in third person.

STORYTELLING RULES:
- Every story needs a human detail — something physical or behavioural that anchors the owner's world
- The problem is shown, not explained
- The tension builds through repetition or accumulation
- The resolution is functional and quiet — not a miracle, just: it works now
- If you mention HitPay, it must feel like a peer recommendation in a payment-related story, not a brand placement
- In non-payment stories (hiring, cash flow, invoicing, registration), do not mention HitPay

DO NOT include: hashtags, statistics or percentages, product feature lists, emojis, marketing buzzwords (seamless, empower, innovate, frictionless, cutting-edge, robust)"""


_OVERUSED_STORY_EXAMPLES = (
    "a guesthouse owner in El Nido, a textile shop in Chinatown, a merchant named "
    "\"Maribel\", an island-hopping tour operator in Coron (often named \"Rodel\" or "
    "\"Gregorio\"), and a surf school in Siargao / Cloud 9 / General Luna — these have "
    "been used so often they now read as templates, not stories"
)

_ALL_SEED_LOCATIONS = (
    {loc for seeds in _STORY_SEEDS.values() for _, loc, _, _ in seeds}
    | {loc for seeds in _SME_STORY_SEEDS.values() for _, loc, _, _ in seeds}
)
_ALL_SEED_LOCATION_WORDS = {w for loc in _ALL_SEED_LOCATIONS for w in re.findall(r"[A-Za-z]+", loc)}

# Per-seed-location match keywords: the proper-noun words in each location string
# (e.g. "Baguio" from "Baguio night market", "Panglao"/"Bohol" from "Panglao, Bohol").
# Matching on these instead of the full location string is needed because generated
# prose freely reorders/drops parts of it ("a night market in Baguio City" instead of
# "Baguio night market").
_SEED_LOCATION_KEYWORDS = {
    loc: set(re.findall(r"[A-Z][a-zA-Z]+", loc)) for loc in _ALL_SEED_LOCATIONS
}

# Words that commonly open a story sentence but aren't the protagonist's name
# (used when scanning the opening sentence for a proper noun), plus brand/product
# terms that show up capitalized but aren't characters.
_NAME_EXTRACTION_STOPWORDS = {
    "we", "our", "the", "a", "an", "one", "when", "his", "her", "with", "for",
    "in", "on", "at", "by", "after", "before", "most", "some", "almost",
    "there", "beside", "every", "she", "he", "they", "it", "us", "you",
    "your", "first", "met", "partner",
    "hitpay", "gcash", "qr", "ph", "borderless", "duitnow", "fpx", "paynow",
    "maya", "alipay", "wechat", "instapay", "kakaopay", "sgd", "myr", "php",
}


def _extract_protagonist_name(content: str) -> str | None:
    """Best-effort extraction of the story's protagonist name from its opening
    sentence — these stories always introduce a named merchant up front, but the
    exact phrasing varies ("Rodel runs...", "We first met Crisanta...", "Our
    partner Ferdie owns..."), so this scans for the first capitalized word that
    isn't a sentence-starter, brand term, or known seed location."""
    first_sentence = (content or "").strip().split(".")[0][:150]
    for word in re.findall(r"[A-Z][a-z]{2,}", first_sentence):
        if word.lower() in _NAME_EXTRACTION_STOPWORDS or word in _ALL_SEED_LOCATION_WORDS:
            continue
        return word
    return None


def _recent_thread_signatures(limit: int = 20) -> tuple:
    """Return (protagonist_names, seed_locations) seen in the most recently created
    Threads posts, so a new generation can avoid repeating the same character or
    merchant setting (e.g. three different posts all featuring "Rodel" in Coron)."""
    from src.database import get_connection

    try:
        conn = get_connection()
        rows = conn.run(
            "SELECT content FROM threads_posts "
            "ORDER BY GREATEST(created_at, COALESCE(updated_at, created_at)) DESC LIMIT :lim",
            lim=limit,
        )
    except Exception:
        return set(), set()

    names, locations = set(), set()
    for (content,) in rows:
        if not content:
            continue
        name = _extract_protagonist_name(content)
        if name:
            names.add(name)
        for loc, keywords in _SEED_LOCATION_KEYWORDS.items():
            if any(kw in content for kw in keywords):
                locations.add(loc)
    return names, locations


def _avoid_recent_repeats_clause(recent_names: set) -> str:
    if not recent_names:
        return ""
    return f"Do NOT reuse these recently-used character names: {', '.join(sorted(recent_names))}. "


def _build_story_prompt(
    market: str | None,
    topic_hint: str | None,
    thread_size: int,
    reference_posts: list[str] | None = None,
    approach: dict | None = None,
) -> str:
    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in slugs)

    market_key = market if market in _STORY_SEEDS else "SEA"
    recent_names, recent_locations = _recent_thread_signatures()
    candidates = [s for s in _STORY_SEEDS[market_key] if s[1] not in recent_locations] or _STORY_SEEDS[market_key]
    biz, location, customers, product = random.choice(candidates)

    # Seed provides topic/market direction — not a mandatory story setting
    seed_ctx = (
        f"Topic context (for thematic direction — do not build a fictional scenario around this):\n"
        f"- Relevant business type for this market: {biz}\n"
        f"- Customer profile: {customers}\n"
        f"- HitPay product to feature: {product}\n\n"
        f"Draw on real business categories like these when giving examples. "
        f"Do not invent specific merchants, names, locations, or results."
    )
    if topic_hint:
        seed_ctx += f"\n\nTopic direction: {topic_hint}"

    if market == "MY":
        mkt_ctx = "Market: Malaysia. Currency: ringgit (MYR). Relevant: DuitNow, FPX, Borderless QR, tourist areas in Penang/KL/Melaka/Langkawi."
    elif market == "SG":
        mkt_ctx = "Market: Singapore. Currency: SGD. Relevant: PayNow, hawker culture, tourist belts (Chinatown, Little India, Orchard)."
    elif market == "PH":
        mkt_ctx = "Market: Philippines. Currency: PHP. Relevant: GCash, QR Ph, island tourism (Palawan, Siargao, Boracay)."
    else:
        mkt_ctx = "Market: Southeast Asia broadly. Pick the most vivid and specific location from MY, SG, or PH."

    approach_name = approach["name"] if approach else "observation"

    _fmt_1 = {
        "observation": (
            "Single post (no numbering): state the observation or insight directly. "
            "1-2 sentences on what HitPay notices across businesses. "
            "Brief note on who this applies to. End with [URL] naturally. 200–450 characters."
        ),
        "calculation": (
            "Single post (no numbering): open with the number or comparison. "
            "One sentence on what it means for the business owner. "
            "End with a practical prompt and [URL]. 200–450 characters."
        ),
        "common_problem": (
            "Single post (no numbering): name the friction directly. "
            "One sentence on why it's imperfect. One sentence on the simpler option with HitPay. "
            "End with [URL]. 200–450 characters."
        ),
        "practical_tip": (
            "Single post (no numbering): frame the tip and who it suits. "
            "One sentence on how to do it with HitPay. End with [URL]. 200–450 characters."
        ),
        "product_use_case": (
            "Single post (no numbering): lead with the problem the feature solves. "
            "One sentence on which businesses it suits. One sentence on how it works. "
            "End with [URL]. 200–450 characters."
        ),
        "merchant_observation": (
            "Single post (no numbering): name the pattern we see across businesses. "
            "One sentence on what drives it. One sentence on what HitPay changes. "
            "End with [URL]. 200–450 characters."
        ),
    }

    _fmt_3 = {
        "observation": (
            "3-part thread:\n"
            "Post 1/3 — State the observation directly. What does HitPay notice across businesses in this market? "
            "1-2 clear sentences. No fictional merchant.\n"
            "Post 2/3 — Context: which businesses this affects, why it happens, what it costs them.\n"
            "Post 3/3 — How HitPay addresses it. Brief and factual. End with [URL] naturally in a sentence."
        ),
        "calculation": (
            "3-part thread:\n"
            "Post 1/3 — Open with the number or cost comparison. Concrete and specific. "
            "Use only verified rates (HitPay MDR or known market rates).\n"
            "Post 2/3 — Which business types this matters to most. What the trade-off looks like in practice.\n"
            "Post 3/3 — What HitPay enables across payment options. End with a practical prompt and [URL]."
        ),
        "common_problem": (
            "3-part thread:\n"
            "Post 1/3 — Name the friction directly. The problem is the opener — no fictional character needed.\n"
            "Post 2/3 — What businesses typically do about it, and why the workaround is imperfect.\n"
            "Post 3/3 — The simpler option. HitPay appears briefly. End with [URL]."
        ),
        "practical_tip": (
            "3-part thread:\n"
            "Post 1/3 — Frame the insight or problem. Why does this matter for the business owner?\n"
            "Post 2/3 — Which business types benefit. Use a short list of categories, not a single example.\n"
            "Post 3/3 — The simple option with HitPay. End with [URL] in a natural sentence."
        ),
        "product_use_case": (
            "3-part thread:\n"
            "Post 1/3 — Lead with the problem the feature solves, not the feature name.\n"
            "Post 2/3 — Which businesses benefit. Use a short list: e.g. 'tuition centres, home businesses, pop-ups' "
            "rather than a single invented merchant.\n"
            "Post 3/3 — How the feature works, briefly. End with [URL]."
        ),
        "merchant_observation": (
            "3-part thread:\n"
            "Post 1/3 — Name a pattern HitPay has observed across businesses we work with. "
            "Use 'we' naturally. No invented merchant, no fabricated detail.\n"
            "Post 2/3 — What drives the pattern and what it costs businesses in practice.\n"
            "Post 3/3 — What changes. HitPay appears briefly. End with [URL]."
        ),
    }

    _fmt_5 = {
        "observation": (
            "5-part thread:\n"
            "Post 1/5 — State the core observation or insight.\n"
            "Post 2/5 — Which businesses this affects, and why.\n"
            "Post 3/5 — What the cost or friction looks like in practice.\n"
            "Post 4/5 — How HitPay addresses it.\n"
            "Post 5/5 — What changes. End with [URL] in a natural sentence."
        ),
        "calculation": (
            "5-part thread:\n"
            "Post 1/5 — Open with the calculation.\n"
            "Post 2/5 — Which business types this matters to.\n"
            "Post 3/5 — What the alternative looks like.\n"
            "Post 4/5 — How HitPay enables the comparison.\n"
            "Post 5/5 — Practical prompt for the reader. End with [URL]."
        ),
        "common_problem": (
            "5-part thread:\n"
            "Post 1/5 — Name the friction directly.\n"
            "Post 2/5 — How it plays out in practice.\n"
            "Post 3/5 — What the workaround looks like and why it's imperfect.\n"
            "Post 4/5 — The simpler option. HitPay introduced here.\n"
            "Post 5/5 — What it looks like once fixed. End with [URL]."
        ),
        "practical_tip": (
            "5-part thread:\n"
            "Post 1/5 — Frame the tip and why it matters.\n"
            "Post 2/5 — Which businesses benefit.\n"
            "Post 3/5 — What the current approach costs them.\n"
            "Post 4/5 — How to do it differently with HitPay.\n"
            "Post 5/5 — Practical next step. End with [URL]."
        ),
        "product_use_case": (
            "5-part thread:\n"
            "Post 1/5 — The problem the feature solves.\n"
            "Post 2/5 — Which businesses face this problem.\n"
            "Post 3/5 — What the friction looks like without the feature.\n"
            "Post 4/5 — How the HitPay feature works.\n"
            "Post 5/5 — Getting started. End with [URL]."
        ),
        "merchant_observation": (
            "5-part thread:\n"
            "Post 1/5 — Name the pattern we observe across businesses.\n"
            "Post 2/5 — What drives it.\n"
            "Post 3/5 — What it costs businesses.\n"
            "Post 4/5 — How HitPay changes it.\n"
            "Post 5/5 — What it looks like now. End with [URL]."
        ),
    }

    # Fallback: map any unknown approach name to 'observation'
    _fallback = "observation"

    if thread_size == 1:
        fmt = _fmt_1.get(approach_name, _fmt_1[_fallback])
    elif thread_size == 3:
        fmt = _fmt_3.get(approach_name, _fmt_3[_fallback])
    else:  # 5
        fmt = _fmt_5.get(approach_name, _fmt_5[_fallback])

    approach_section = ""
    if approach:
        approach_section = f"\nCONTENT APPROACH — {approach['name']}:\n{approach['instruction']}\n"

    reference_section = ""
    if reference_posts:
        examples = "\n---\n".join(reference_posts[:3])
        reference_section = f"""REFERENCE — examples of the tone and style to match:

---
{examples}
---

"""

    return f"""{reference_section}Write a HitPay Threads post.

{seed_ctx}

{mkt_ctx}
{approach_section}
FORMAT:
{fmt}

Each post: aim for 150–280 characters. 450 is the ceiling, not the target. No hashtags. No emojis. Short sentences, one point each.

LINK URL RULE:
Set link_url to https://hitpayapp.com/blog/{{slug}} using the most topically relevant slug.
If no clear match, default to: hitpay-rates

LIVE BLOG SLUGS:
{urls_list}

Return raw JSON only — no markdown fences:
{{"topic": "...", "posts": [...], "link_url": "https://hitpayapp.com/blog/..."}}"""


def _build_sme_story_prompt(market: str | None, topic_hint: str | None, thread_size: int) -> str:
    from src.brand_config import get_brand_config
    bc = get_brand_config("smegrowthhub")

    # Always pick a fresh random seed, even with a topic_hint — see _build_story_prompt
    # for why (skipping it let the model default to the same recurring clichés).
    market_key = market if market in _SME_STORY_SEEDS else "SEA"
    recent_names, recent_locations = _recent_thread_signatures()
    candidates = [s for s in _SME_STORY_SEEDS[market_key] if s[1] not in recent_locations] or _SME_STORY_SEEDS[market_key]
    biz, location, scenario, lesson = random.choice(candidates)

    seed_ctx = (
        f"Story seed (use as inspiration, not verbatim — invent your own specific human "
        f"detail, do not reuse this seed's exact wording across different generations):\n"
        f"- Business: {biz} in {location}\n"
        f"- Scenario: {scenario}\n"
        f"- What they learned: {lesson}\n\n"
        f"Do NOT default to overused examples: {_OVERUSED_STORY_EXAMPLES}. "
        f"{_avoid_recent_repeats_clause(recent_names)}"
        f"Vary the business type, location, and any names every time."
    )
    if topic_hint:
        seed_ctx += f"\n\nTopic direction (steer the theme, not the characters): {topic_hint}"

    if market == "MY":
        mkt_ctx = "Market: Malaysia. Reference real places (Bangsar, PJ, JB, Penang), real costs (EPF, SST), real tools."
    elif market == "SG":
        mkt_ctx = "Market: Singapore. Reference real places (Tanjong Pagar, Tiong Bahru, Jurong), real obligations (CPF, GST)."
    elif market == "PH":
        mkt_ctx = "Market: Philippines. Reference real places (BGC, Makati, Cebu, Siargao), real systems (BIR, DTI)."
    else:
        mkt_ctx = "Market: Southeast Asia broadly. Pick the most vivid and specific location from MY, SG, or PH."

    if thread_size == 1:
        fmt = (
            "Single post (no numbering): a complete micro-story. "
            "Human detail → friction → what changed. 200–450 characters."
        )
    elif thread_size == 3:
        fmt = (
            "3-part thread:\n"
            "Post 1/3 — The Scene: introduce the business owner, location, a specific human detail that reveals the problem.\n"
            "Post 2/3 — The Tension: the workaround, the cost, the moment it got frustrating.\n"
            "Post 3/3 — The Shift: what they changed or learned. Quiet resolution. End with a callback to the opening detail."
        )
    else:  # 5
        fmt = (
            "5-part thread:\n"
            "Post 1/5 — The Scene: business owner, location, the telling human detail.\n"
            "Post 2/5 — The Problem: how it kept coming up.\n"
            "Post 3/5 — The Breaking Point: one specific moment when it mattered most.\n"
            "Post 4/5 — The Shift: what changed and how they found the answer.\n"
            "Post 5/5 — The Close: callback to the opening detail. A quiet, earned ending."
        )

    return f"""Write an SME Growth Hub Threads story.

{seed_ctx}

{mkt_ctx}

FORMAT:
{fmt}

Each post: aim for 150–280 characters. 450 is the ceiling, not the target. No hashtags. No emojis. Short sentences, one point each.
Do not mention HitPay unless the story is directly about payments — and even then, only as a quiet peer recommendation.

LINK URL RULE:
Set link_url to {bc.blog_base_url}/{{slug}} using a descriptive slug matching the story topic.
If no specific slug, default to: {bc.blog_base_url}

Return raw JSON only — no markdown fences:
{{"topic": "...", "posts": [...], "link_url": "{bc.blog_base_url}/..."}}"""


def _cap_post(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 1)
    if cutoff <= 0:
        cutoff = limit - 1
    return text[:cutoff] + "…"


def generate_threads_story(
    market: str = None,
    topic_hint: str = None,
    thread_size: int = 3,
    brand: str = "hitpay",
    reference_posts: list[str] | None = None,
    _avoid_structure: str | None = None,
) -> dict:
    import random
    from src.thought_leadership import HITPAY_TOPIC_POOL
    client = OpenRouterClient()

    if brand == "hitpay" and topic_hint is None:
        topic_hint = random.choice(HITPAY_TOPIC_POOL)

    if brand == "smegrowthhub":
        system = SME_THREADS_SYSTEM_PROMPT
        prompt = _build_sme_story_prompt(market, topic_hint, thread_size)
        fallback = _SME_FALLBACK_URL
        url_pattern = None  # skip strict pattern check for SME
        chosen_structure = None
    else:
        system = THREADS_SYSTEM_PROMPT
        # Pick an approach that avoids repeating the last one used
        candidates = [a for a in _CONTENT_APPROACHES if a["name"] != _avoid_structure]
        chosen_approach = random.choice(candidates or _CONTENT_APPROACHES)
        prompt = _build_story_prompt(market, topic_hint, thread_size, reference_posts=reference_posts, approach=chosen_approach)
        fallback = _FALLBACK_URL
        url_pattern = r"^https://hitpayapp\.com/blog/[a-zA-Z0-9_\-()/]+$"

    response = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=2000,
        system=system + "\n\n" + _WRITING_STYLE_RULES,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    posts = data.get("posts")
    if not isinstance(posts, list) or not posts:
        raise ValueError(f"Expected posts array, got: {posts!r}")

    def _to_str(p) -> str:
        if isinstance(p, str):
            return p
        if isinstance(p, dict):
            return p.get("text") or p.get("content") or p.get("post") or p.get("body") or ""
        return str(p)

    data["posts"] = [_cap_post(_to_str(p)) for p in posts]

    # For HitPay stories, ensure the last post contains [URL].
    # The model sometimes writes "linked in bio" (a Threads platform idiom) instead.
    # Strip that hallucination and append [URL] so the URL gets substituted on use.
    _BIO_LINK_RE = re.compile(
        r'[\s\u2014\-]*(?:'
        r'[Tt]he full breakdown is linked in (?:our\s+)?bio|'
        r'[Ff]ull (?:breakdown|details?) (?:is\s+)?linked in (?:our\s+)?bio|'
        r'[Ff]ind (?:it|the link) in (?:our\s+)?bio|'
        r'[Ll]ink(?:ed)? in (?:our\s+)?bio'
        r')\.?',
        re.IGNORECASE,
    )
    if brand != "smegrowthhub" and data["posts"]:
        last = data["posts"][-1]
        # Don't append [URL] if the post already has the placeholder OR an actual blog URL
        _has_url = "[URL]" in last or "hitpayapp.com/blog" in last or "smegrowthhub.com" in last
        if not _has_url:
            cleaned = _BIO_LINK_RE.sub("", last).rstrip().rstrip("\u2014").rstrip()
            if not cleaned.endswith((".", "!", "?", "\u2026")):
                cleaned += "."
            data["posts"][-1] = _cap_post(cleaned + " [URL]")

    link_url = data.get("link_url") or fallback
    if url_pattern:
        if not re.match(url_pattern, link_url):
            link_url = fallback
        else:
            # Verify slug exists in live sitemap
            from src.thought_leadership import _is_valid_blog_url
            if not _is_valid_blog_url(link_url):
                link_url = fallback
    data["link_url"] = link_url
    if chosen_approach:
        data["structure"] = chosen_approach["name"]

    return data
