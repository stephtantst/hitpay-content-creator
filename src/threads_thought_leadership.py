import json
import random
import re

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.generator import _messages_create_with_retry
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
    ],
    "SG": [
        ("heritage craft shop", "Chinatown", "tourists from mainland China and Japan", "Borderless QR"),
        ("hawker stall", "Maxwell Food Centre", "lunchtime office workers", "HitPay QR"),
        ("provision shop", "Toa Payoh", "longtime regulars and younger neighbours", "QR payments"),
        ("tailoring shop", "Little India", "customers ordering custom pieces from abroad", "payment link"),
        ("florist", "Tiong Bahru", "corporate clients with recurring flower subscriptions", "payment links"),
        ("bookshop", "Bukit Timah", "parents paying for tuition materials", "payment links"),
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
    ],
    "SEA": [
        ("small textile shop", "a heritage port city", "international tourists", "Borderless QR"),
        ("family-run guesthouse", "a coastal town", "guests booking from abroad", "payment link"),
        ("craft workshop", "an old town district", "tourists and collectors from overseas", "Borderless QR"),
        ("street food stall", "a city night market", "digital-first customers", "QR payments"),
        ("boutique dive operator", "an island destination", "foreign divers without local cash", "Borderless QR"),
    ],
}

THREADS_SYSTEM_PROMPT = """You are a brand storyteller for HitPay, a Southeast Asian payment platform.

You write short-form narratives for Meta Threads — the kind that feel human, specific, and earned. Your stories are told from HitPay's perspective ("we", "our merchants"), but they're really about the merchants: their daily friction, their workarounds, and what changes when payments stop being a problem.

VOICE:
- Warm and observational. The narrator has spent time listening to merchants.
- Specific and concrete: name cities, name the tourist home countries, name the human details (a calculator on the counter, a phone propped against the register, a notebook of hand-written totals).
- Understated. The emotional weight comes from detail, not adjectives.
- No superlatives. No marketing language. No claims. Just story.
- First person plural: "We", "our merchants", "she told us", "we had a partner in..."

STORYTELLING RULES:
- Every story needs a human detail — something physical or behavioural that anchors the merchant's world
- The problem is shown, not explained
- The tension builds through repetition: "Sometimes it worked. A lot of the time, it didn't."
- The resolution is functional and quiet — not a miracle, just: it works now
- The brand closer ("That's the kind of thing we build for.") should feel earned, not appended
- Callbacks: if you introduce a detail in Post 1, bring it back in the final post

DO NOT include: hashtags, statistics or percentages, product feature lists, emojis (except 🧵 at the end of Post 1 in multi-part threads), marketing buzzwords (seamless, empower, innovate, frictionless, cutting-edge, robust)"""


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

DO NOT include: hashtags, statistics or percentages, product feature lists, emojis (except 🧵 at the end of Post 1 in multi-part threads), marketing buzzwords (seamless, empower, innovate, frictionless, cutting-edge, robust)"""


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


def _recent_thread_signatures(limit: int = 10) -> tuple:
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


def _build_story_prompt(market: str | None, topic_hint: str | None, thread_size: int) -> str:
    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in slugs)

    # Always pick a fresh random seed for the concrete merchant/location grounding —
    # even when a topic_hint is supplied (e.g. from a blog post title in Repurpose All,
    # or the auto-selected HITPAY_TOPIC_POOL entry). Previously, supplying a topic_hint
    # skipped the seed entirely, leaving the model to invent its own "default" merchant —
    # which converged on the same handful of clichés across generations.
    market_key = market if market in _STORY_SEEDS else "SEA"
    recent_names, recent_locations = _recent_thread_signatures()
    candidates = [s for s in _STORY_SEEDS[market_key] if s[1] not in recent_locations] or _STORY_SEEDS[market_key]
    biz, location, customers, product = random.choice(candidates)

    seed_ctx = (
        f"Story seed (use as inspiration, not verbatim — invent your own specific human "
        f"detail, do not reuse this seed's exact wording across different generations):\n"
        f"- Merchant: {biz} in {location}\n"
        f"- Their customers: {customers}\n"
        f"- HitPay product that helped: {product}\n\n"
        f"Do NOT default to overused examples: {_OVERUSED_STORY_EXAMPLES}. "
        f"{_avoid_recent_repeats_clause(recent_names)}"
        f"Vary the merchant type, location, and any names every time."
    )
    if topic_hint:
        seed_ctx += f"\n\nTopic direction (steer the theme, not the characters): {topic_hint}"

    if market == "MY":
        mkt_ctx = "Market: Malaysia. Currency: ringgit (MYR). Relevant: DuitNow, FPX, Borderless QR, tourist areas in Penang/KL/Melaka/Langkawi."
    elif market == "SG":
        mkt_ctx = "Market: Singapore. Currency: SGD. Relevant: PayNow, hawker culture, tourist belts (Chinatown, Little India, Orchard)."
    elif market == "PH":
        mkt_ctx = "Market: Philippines. Currency: PHP. Relevant: GCash, QR Ph, island tourism (Palawan, Siargao, Boracay)."
    else:
        mkt_ctx = "Market: Southeast Asia broadly. Pick the most vivid and specific location from MY, SG, or PH."

    if thread_size == 1:
        fmt = (
            "Single post (no numbering): a complete micro-story. "
            "Human detail → friction → resolution → brand close. 200–450 characters."
        )
    elif thread_size == 3:
        fmt = (
            "3-part thread:\n"
            "Post 1/3 — The Scene: introduce the merchant, location, and a specific human detail that reveals the problem. End at the moment of friction. End post with 🧵\n"
            "Post 2/3 — The Tension: their workaround, the cost, the sale that didn't happen, the habit they developed just to cope.\n"
            "Post 3/3 — The Resolution: HitPay product introduced briefly and functionally. Callback to the opening human detail. End with a variation of: \"That's the kind of thing we build for.\""
        )
    else:  # 5
        fmt = (
            "5-part thread:\n"
            "Post 1/5 — The Scene: merchant, location, the telling human detail. End with 🧵\n"
            "Post 2/5 — The Problem: how widespread or frequent the friction was.\n"
            "Post 3/5 — The Breaking Point: one specific incident. A customer who left. A late night reconciling. A sale lost at the worst moment.\n"
            "Post 4/5 — The Shift: how they found HitPay. What changed. Functional, not miraculous.\n"
            "Post 5/5 — The Close: callback to the opening human detail. Brand closer: \"That's the kind of thing we build for.\""
        )

    return f"""Write a HitPay Threads story.

{seed_ctx}

{mkt_ctx}

FORMAT:
{fmt}

Each post: 200–450 characters. No hashtags. No emojis except 🧵 noted above. Warm, specific, observational tone.

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
            "Post 1/3 — The Scene: introduce the business owner, location, a specific human detail that reveals the problem. End with 🧵\n"
            "Post 2/3 — The Tension: the workaround, the cost, the moment it got frustrating.\n"
            "Post 3/3 — The Shift: what they changed or learned. Quiet resolution. End with a callback to the opening detail."
        )
    else:  # 5
        fmt = (
            "5-part thread:\n"
            "Post 1/5 — The Scene: business owner, location, the telling human detail. End with 🧵\n"
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

Each post: 200–450 characters. No hashtags. No emojis except 🧵 noted above. Warm, specific, observational tone.
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
) -> dict:
    import random
    from src.thought_leadership import HITPAY_TOPIC_POOL
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if brand == "hitpay" and topic_hint is None:
        topic_hint = random.choice(HITPAY_TOPIC_POOL)

    if brand == "smegrowthhub":
        system = SME_THREADS_SYSTEM_PROMPT
        prompt = _build_sme_story_prompt(market, topic_hint, thread_size)
        fallback = _SME_FALLBACK_URL
        url_pattern = None  # skip strict pattern check for SME
    else:
        system = THREADS_SYSTEM_PROMPT
        prompt = _build_story_prompt(market, topic_hint, thread_size)
        fallback = _FALLBACK_URL
        url_pattern = r"^https://hitpayapp\.com/blog/[a-zA-Z0-9_\-()/]+$"

    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=system + "\n\n" + _WRITING_STYLE_RULES,
        messages=[{"role": "user", "content": prompt}],
        metadata={"user_id": "threads-generation"}
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

    return data
