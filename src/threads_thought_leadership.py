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

_STORY_STRUCTURES: list[dict] = [
    {
        "name": "default",
        "instruction": (
            "Open Post 1 with a 'we spoke with / we met / we were talking to' framing that grounds the story as something HitPay witnessed. "
            "Show the problem through observable behaviour, not explanation. Build through recurring patterns, not a single turning point. "
            "Resolve quietly and functionally. The final line should be specific to this story — "
            "a callback to the opening detail, a quiet observation, or simply: what it means now. "
            "Vary sentence length within each post: mix longer observations with shorter ones."
        ),
    },
    {
        "name": "before_after",
        "instruction": (
            "Structure the story as a clean before/after contrast. "
            "Use plain, factual language. Let the change speak for itself — "
            "no emotional arc, just: what it was, what it is now. "
            'A closing line like "That\'s it." or "It\'s a small thing. But it wasn\'t." works well.'
        ),
    },
    {
        "name": "overheard",
        "instruction": (
            "Reconstruct something the merchant said — a comment, a question, a realisation — "
            "as if you heard it directly in conversation. Build the whole story around that quoted moment. "
            "The narrator is a listener, not an explainer. The merchant's voice carries the weight."
        ),
    },
    {
        "name": "day_in_the_life",
        "instruction": (
            "Follow a single merchant through one transaction moment in present tense. "
            "The reader watches it happen. No backstory, no explanation — start mid-scene. "
            "The payment friction (and resolution) is shown in real time, not recounted."
        ),
    },
    {
        "name": "the_thing_that_didnt_work",
        "instruction": (
            "Open with the thing that kept failing — the workaround, the friction, the repeated cost. "
            "Let the failure accumulate across the first posts. "
            "Resolution comes late and is understated: not a miracle, just: it stopped happening."
        ),
    },
]

THREADS_SYSTEM_PROMPT = """You are writing short posts for HitPay on Meta Threads. HitPay is a payment platform for small businesses in Southeast Asia.

The posts are short merchant observations — told plainly, like you're telling a colleague what you noticed. Not a mini case study. Not a story with a moral. Just: here's what was happening at this business, and here's what changed.

VOICE:
- Plain and direct. Write like a person, not a brand.
- Always open with a "we spoke with / we met / we were talking to" framing — this grounds the story as something HitPay actually witnessed or heard directly. E.g. "We spoke with a bakery owner in Iloilo...", "We were talking to a florist in Tiong Bahru...", "One of our partners runs a pearl stall in Puerto Princesa...". Vary the opener — don't use the same phrase every time.
- Specific: name the city, name the business type, use details that only someone who was there would notice (a hand-written sign, a coin float kept under the counter, the way the queue stacks up near the door).
- First person plural throughout: "we", "she told us", "we noticed", "one of our partners" — the narrator is present, not omniscient.
- No performing restraint. If a sentence sounds like it was written to sound understated, rewrite it.

WHAT WORKS:
- Focus on recurring patterns, not perfectly timed one-off anecdotes. "Customers kept asking for GCash" is more believable than one pivotal Saturday interaction with a single regular.
- Show observable behaviour, not internal thoughts. Replace "the owner knew it was happening" with what she actually did — like telling customers "cash only" and pointing at the ATM down the road.
- Use details specific to the business — a bakery should sound like a bakery. Morning queues, trays out of the oven, regular buying habits. Details that don't directly sell the product make the scene feel real.
- Include dialogue sparingly. A short, natural quote ("May GCash?") often does more than a narrated exchange.
- Vary sentence length naturally. Some sentences carry more context; others land short. Each post should read like a paragraph, not a list of fragments.
- End the final post with a CTA that speaks to the reader and includes [URL] naturally in the sentence. It should feel like a peer recommendation, not a brand push.

WHAT TO AVOID:
- Perfect story arcs. Don't force beginning → conflict → resolution → lesson. Real business stories are often just observations.
- Overly neat timelines: "The following Saturday... By Monday... That Thursday" — exact sequences feel manufactured unless they're genuinely important.
- Convenient cause-and-effect: business changes are rarely immediate or perfectly attributable to one event.
- Omniscient narrator: only describe what could realistically be observed or reported. Don't explain what the owner was thinking.
- Generic labels ("a regular customer", "a market trader") where a more specific detail would work better.
- Over-explaining the takeaway. Let readers infer it. End with a small operational observation ("people stopped asking if they accepted GCash") not a tidy success statement.
- The AI storytelling cadence: not every thread needs to follow problem → turning point → solution → CTA. Vary the structure.
- Sentences where every line has the same short rhythm — it starts to sound robotic.
- Craft-signalling phrases: "which was the part that stayed with us", "as it turns out", "that's the whole story."
- Stock closing lines or anything that sounds like it was written to be the ending.
- "Linked in bio", "link in bio", "link in our bio", "full breakdown is linked in bio", or any variation — NEVER write these. The placeholder [URL] will be replaced with the actual article URL. Use it naturally in a sentence in the final post's CTA, e.g. "If you're switching to QR, this is how she set it up: [URL]"

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
    structure: dict | None = None,
) -> str:
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

    structure_name = structure["name"] if structure else "default"

    _fmt_1 = {
        "default": (
            "Single post (no numbering): a complete micro-story. "
            "Human detail → friction → resolution → reader-involving CTA with [URL]. 200–450 characters."
        ),
        "before_after": (
            "Single post (no numbering): two states, clean cut. One or two sentences for State A — "
            "specific and concrete. One sentence for what changed. One sentence for State B. "
            "End with a reader-involving CTA and [URL]. 200–450 characters."
        ),
        "overheard": (
            "Single post (no numbering): open with a direct quote from the merchant — something they "
            "said in passing that contains the whole story. One sentence of context. "
            "End with a reader-involving CTA and [URL]. 200–450 characters."
        ),
        "day_in_the_life": (
            "Single post (no numbering): present tense. One transaction moment, observed in real time. "
            "Start mid-scene. End with a reader-involving CTA and [URL]. 200–450 characters."
        ),
        "the_thing_that_didnt_work": (
            "Single post (no numbering): open with the recurring failure — name it plainly, once. "
            "One sentence of accumulation. The resolution is one quiet sentence. "
            "End with a reader-involving CTA and [URL]. 200–450 characters."
        ),
    }

    _fmt_3 = {
        "default": (
            "3-part thread:\n"
            "Post 1/3 — Open with 'We spoke with / We met / We were talking to [merchant] in [city]...' Introduce what they do and the recurring pattern that was creating friction. End post with 🧵\n"
            "Post 2/3 — What they were doing about it — the workaround, the habit, the thing they told customers. Observable behaviour, not their internal reasoning.\n"
            "Post 3/3 — What changed. HitPay introduced briefly. Close with a reader-involving CTA and [URL]."
        ),
        "before_after": (
            "3-part thread:\n"
            "Post 1/3 — Open with 'We spoke with / We met / We were talking to [merchant] in [city]...' One specific observable detail from how things worked before. End post with 🧵\n"
            "Post 2/3 — What that kept costing — described in terms of what actually happened, not what it meant.\n"
            "Post 3/3 — What it looks like now. One concrete detail. Close with a reader-involving CTA and [URL]."
        ),
        "overheard": (
            "3-part thread:\n"
            "Post 1/3 — Open with a direct quote from the merchant — something they said to us — followed by one line of context: who they are and where. End post with 🧵\n"
            "Post 2/3 — The situation behind the quote. What we observed. What they told us.\n"
            "Post 3/3 — What changed. The quote lands differently with the context. Close with a reader-involving CTA and [URL]."
        ),
        "day_in_the_life": (
            "3-part thread:\n"
            "Post 1/3 — Open with 'We were at [merchant]'s [business] in [city] when...' Drop into a specific moment in present tense. End post with 🧵\n"
            "Post 2/3 — Still present tense. The friction arrives.\n"
            "Post 3/3 — How it resolved. HitPay appears briefly. Close with a reader-involving CTA and [URL]."
        ),
        "the_thing_that_didnt_work": (
            "3-part thread:\n"
            "Post 1/3 — Open with 'We spoke with / We met [merchant] in [city]...' Describe the recurring pattern that kept not working — what customers kept doing, what the owner kept having to say. End post with 🧵\n"
            "Post 2/3 — How the pattern kept playing out. What it looked like day to day.\n"
            "Post 3/3 — What changed and what it looks like now. Close with a reader-involving CTA and [URL]."
        ),
    }

    _fmt_5 = {
        "default": (
            "5-part thread:\n"
            "Post 1/5 — Open with 'We spoke with / We met / We were talking to [merchant] in [city]...' Introduce what they do and the recurring friction. End with 🧵\n"
            "Post 2/5 — The pattern in more detail — how frequent, what it looked like in practice.\n"
            "Post 3/5 — A specific moment that illustrates the pattern at its worst. Observable, not narrated.\n"
            "Post 4/5 — What changed. How HitPay came into it. Functional, not dramatic.\n"
            "Post 5/5 — What it looks like now. A small operational observation. Close with a reader-involving CTA and [URL]."
        ),
        "before_after": (
            "5-part thread:\n"
            "Post 1/5 — Open with 'We spoke with / We met [merchant] in [city]...' One observable detail from how things were. End with 🧵\n"
            "Post 2/5 — A second detail from the same period — different angle.\n"
            "Post 3/5 — The moment the cost of it became hard to ignore.\n"
            "Post 4/5 — One concrete detail from how things are now.\n"
            "Post 5/5 — A second detail from the current state. Close with a reader-involving CTA and [URL]."
        ),
        "overheard": (
            "5-part thread:\n"
            "Post 1/5 — A direct quote from the merchant — something they said to us — with just enough context to place it. End with 🧵\n"
            "Post 2/5 — Who said it. What their business is. What we observed.\n"
            "Post 3/5 — The situation behind the quote in full.\n"
            "Post 4/5 — What changed.\n"
            "Post 5/5 — The quote again. It reads differently now. Close with a reader-involving CTA and [URL]."
        ),
        "day_in_the_life": (
            "5-part thread:\n"
            "Post 1/5 — Open with 'We were at [merchant]'s [business] in [city] when...' Present tense. End with 🧵\n"
            "Post 2/5 — A customer. The transaction begins.\n"
            "Post 3/5 — The friction, in real time.\n"
            "Post 4/5 — How it resolved. HitPay appears.\n"
            "Post 5/5 — The scene ends. One observation. Close with a reader-involving CTA and [URL]."
        ),
        "the_thing_that_didnt_work": (
            "5-part thread:\n"
            "Post 1/5 — Open with 'We spoke with / We met [merchant] in [city]...' The recurring failure — what kept happening. End with 🧵\n"
            "Post 2/5 — Another instance of it.\n"
            "Post 3/5 — The time it cost something real.\n"
            "Post 4/5 — What they changed.\n"
            "Post 5/5 — What it looks like now. Close with a reader-involving CTA and [URL]."
        ),
    }

    if thread_size == 1:
        fmt = _fmt_1.get(structure_name, _fmt_1["default"])
    elif thread_size == 3:
        fmt = _fmt_3.get(structure_name, _fmt_3["default"])
    else:  # 5
        fmt = _fmt_5.get(structure_name, _fmt_5["default"])

    structure_section = ""
    if structure:
        structure_section = f"\nNARRATIVE STRUCTURE — {structure['name']}:\n{structure['instruction']}\n"

    reference_section = ""
    if reference_posts:
        examples = "\n---\n".join(reference_posts[:3])
        reference_section = f"""REFERENCE — examples of the tone and style to match:

---
{examples}
---

"""

    return f"""{reference_section}Write a HitPay Threads story.

{seed_ctx}

{mkt_ctx}
{structure_section}
FORMAT:
{fmt}

Each post: aim for 150–280 characters. 450 is the ceiling, not the target. No hashtags. No emojis except 🧵 noted above. Short sentences, one point each.

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

Each post: aim for 150–280 characters. 450 is the ceiling, not the target. No hashtags. No emojis except 🧵 noted above. Short sentences, one point each.
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
        # Pick a structure that avoids repeating the last one used
        candidates = [s for s in _STORY_STRUCTURES if s["name"] != _avoid_structure]
        chosen_structure = random.choice(candidates or _STORY_STRUCTURES)
        prompt = _build_story_prompt(market, topic_hint, thread_size, reference_posts=reference_posts, structure=chosen_structure)
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
        if "[URL]" not in last:
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
    if chosen_structure:
        data["structure"] = chosen_structure["name"]

    return data
