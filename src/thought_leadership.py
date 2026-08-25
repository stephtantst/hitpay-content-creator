import json
import random
import re
import time
from datetime import datetime as _datetime

import json
import requests

from config import OPENROUTER_MODEL
from src.generator import COUNTRY_CONTEXT, _load_relevant_docs, _messages_create_with_retry
from src.llm_client import OpenRouterClient

_SITEMAP_URL = "https://hitpayapp.com/sitemap_en.xml"
_blog_slugs_cache: list[str] | None = None
_blog_slugs_cache_ts: float = 0
_SLUG_CACHE_TTL = 3600  # 1 hour

_WRITING_STYLE_RULES = """PROSE ANTI-PATTERNS — avoid all of these:
- Marketing language, startup jargon, corporate phrasing, inspirational tone
- Performative vulnerability, forced relatability, fake conversational fillers
- Overwritten hooks, one-line paragraph spam, overuse of em dashes, constant rhetorical contrast
- Banned openers and structures: "Honestly…", "The truth is…", "Let that sink in.", "Here's the thing.", "It turns out…", "You're not alone.", "In a world where…", "Everything changed when…", "I used to think… now I…", "This isn't just about X. It's about Y.", "Most people don't realize…", "Read that again."
- Do not manufacture emotion or tension where none exists

PROSE STYLE:
- Use normal sentence structure. Full paragraphs are fine.
- Vary rhythm naturally, not intentionally.
- Prefer clarity over punchiness.
- Let observations stand without over-explaining them.
- Use concrete language and real examples. Keep transitions subtle.
- Allow mild imperfections in flow — they sound human.
- The writing can have personality, but should not constantly signal personality."""


def _fetch_live_blog_slugs() -> list[str]:
    """Return all live blog post slugs from the sitemap. Cached for 1 hour."""
    global _blog_slugs_cache, _blog_slugs_cache_ts
    if _blog_slugs_cache and (time.time() - _blog_slugs_cache_ts) < _SLUG_CACHE_TTL:
        return _blog_slugs_cache
    try:
        resp = requests.get(_SITEMAP_URL, timeout=8)
        resp.raise_for_status()
        all_urls = re.findall(r"<loc>(https://hitpayapp\.com/blog/[^<]+)</loc>", resp.text)
        slugs = [
            u.replace("https://hitpayapp.com/blog/", "")
            for u in all_urls
            if "/categories/" not in u and "/tags/" not in u and u != "https://hitpayapp.com/blog"
        ]
        if slugs:
            _blog_slugs_cache = slugs
            _blog_slugs_cache_ts = time.time()
            return slugs
    except Exception:
        pass
    # Fall back to hardcoded list
    return [url.replace("https://hitpayapp.com/blog/", "") for _, url in VALID_BLOG_URLS]


def _cap_tweet(text: str, limit: int = 280) -> str:
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 1)
    if cutoff <= 0:
        cutoff = limit - 1
    return text[:cutoff] + "…"


# Verified HitPay blog post URLs — used as fallback when sitemap is unavailable
VALID_BLOG_URLS: list[tuple[str, str]] = [
    # General / SEA
    ("What is HitPay", "https://hitpayapp.com/blog/what-is-hitpay"),
    ("HitPay Rates & Pricing (fees, MDR, transaction costs)", "https://hitpayapp.com/blog/hitpay-rates"),
    ("HitPay Transaction Fee Breakdown", "https://hitpayapp.com/blog/hitpay-transaction-fee"),
    ("HitPay Payment Solutions Overview", "https://hitpayapp.com/blog/hitpay-payment-solutions"),
    ("Payment Gateway Guide SEA", "https://hitpayapp.com/blog/payment-gateway"),
    ("Payment Processing for Online Stores SEA", "https://hitpayapp.com/blog/payment-processing-online-store"),
    ("Payment Link — send & get paid instantly", "https://hitpayapp.com/blog/payment-link"),
    ("QR Code Payments Explained", "https://hitpayapp.com/blog/qr-code-payments"),
    ("Alternative Payment Methods in Southeast Asia", "https://hitpayapp.com/blog/alternative-payment-methods-southeast-asia"),
    ("Ecommerce Payment Solutions SEA", "https://hitpayapp.com/blog/ecommerce-payment-solutions-southeast-asia"),
    ("Credit Card Payment for Businesses SEA", "https://hitpayapp.com/blog/credit-card-payment"),
    ("Invoice Payment for SMEs SEA", "https://hitpayapp.com/blog/invoice-payment"),
    ("Tap to Pay on iPhone", "https://hitpayapp.com/blog/hitpay-now-offers-tap-to-pay-on-iphone-for-merchants-to-accept-contactless-payments"),
    ("Tap to Pay Singapore", "https://hitpayapp.com/blog/tap-to-pay-singapore"),
    ("HitPay Payout Guide", "https://hitpayapp.com/blog/hitpay-payout-guide"),
    ("HitPay Scan to Pay", "https://hitpayapp.com/blog/hitpay-scan-to-pay"),
    ("POS System Guide SEA", "https://hitpayapp.com/blog/pos-system"),
    ("Point of Sale Southeast Asia", "https://hitpayapp.com/blog/point-of-sale-southeast-asia"),
    ("How to Accept Payments Online", "https://hitpayapp.com/blog/how-to-accept-payments-online"),
    ("Recurring Billing (How to Use)", "https://hitpayapp.com/blog/how-to-use-recurring-billing"),
    ("Recurring Payment Link", "https://hitpayapp.com/blog/recurring-payment-link"),
    ("Payment Link Explained (SEA)", "https://hitpayapp.com/blog/payment-links"),
    ("Pass Transaction Fees to Customers", "https://hitpayapp.com/blog/pass-transaction-fees-to-customers"),
    ("Credit Card Chargebacks Guide", "https://hitpayapp.com/blog/credit-card-chargebacks"),
    ("B2B Payment Solutions SEA", "https://hitpayapp.com/blog/best-b2b-payment-solutions-southeast-asia"),
    ("Multi-currency Payment Gateway SEA", "https://hitpayapp.com/blog/best-multi-currency-payment-gateway-sea-smbs"),
    ("BNPL Singapore Buy Now Pay Later", "https://hitpayapp.com/blog/bnpl-singapore-buy-now-pay-later"),
    ("HitPay App Features", "https://hitpayapp.com/blog/hitpay-app"),
    ("HitPay POS Software", "https://hitpayapp.com/blog/hitpay-pos-software"),
    ("Digital Wallet vs Payment Gateways", "https://hitpayapp.com/blog/digital-wallet-vs-payment-gateways"),
    ("Cross-Border QR Acceptance", "https://hitpayapp.com/blog/cross-border-qr-acceptance-on-hitpay-terminals-serving-tourists-from-thailand-indonesia-and-beyond"),
    ("Payment API Southeast Asia", "https://hitpayapp.com/blog/payment-api-southeast-asia"),
    # SG
    ("Payment Gateway Singapore", "https://hitpayapp.com/blog/payment-gateway-singapore"),
    ("Best Payment Gateway Singapore", "https://hitpayapp.com/blog/best-payment-gateway-singapore"),
    ("HitPay Singapore Overview", "https://hitpayapp.com/blog/hitpay-singapore"),
    ("Recurring Billing Singapore", "https://hitpayapp.com/blog/recurring-billing-sg"),
    ("Online Payment Singapore", "https://hitpayapp.com/blog/online-payment-singapore"),
    ("Accept Online Payments Singapore", "https://hitpayapp.com/blog/accept-online-payments-singapore"),
    ("PayNow Payment Gateway Singapore", "https://hitpayapp.com/blog/paynow-payment-gateway-singapore"),
    ("PayNow QR Code Singapore", "https://hitpayapp.com/blog/paynow-qr-code-setup-singapore"),
    ("Generate PayNow QR Code Singapore", "https://hitpayapp.com/blog/generate-paynow-qr-code-singapore"),
    ("PayNow Shopify Gateway Singapore", "https://hitpayapp.com/blog/paynow-shopify-payment-gateway-singapore"),
    ("Card Reader Singapore", "https://hitpayapp.com/blog/card-reader-singapore"),
    ("Card Payment Singapore", "https://hitpayapp.com/blog/card-payment-singapore"),
    ("Best Card Machine Singapore", "https://hitpayapp.com/blog/best-card-machine-singapore"),
    ("POS System Singapore", "https://hitpayapp.com/blog/pos-system-singapore"),
    ("Best POS System Singapore SME", "https://hitpayapp.com/blog/best-pos-system-singapore-sme"),
    ("Cashless Payments Singapore", "https://hitpayapp.com/blog/cashless-payments-singapore-what-methods-to-accept"),
    ("Popular Payment Methods Singapore", "https://hitpayapp.com/blog/popular-payment-methods-in-singapore"),
    ("Invoicing Singapore", "https://hitpayapp.com/blog/invoicing-for-businesses-singapore"),
    ("How to Create & Send Payment Link Singapore", "https://hitpayapp.com/blog/how-to-create-send-payment-link-singapore"),
    ("Payment Links Singapore PayNow GrabPay", "https://hitpayapp.com/blog/payment-links-singapore-paynow-grabpay"),
    ("Contactless Payments Singapore", "https://hitpayapp.com/blog/contactless-payments-singapore-setup-guide"),
    ("Accept GrabPay Singapore", "https://hitpayapp.com/blog/how-to-accept-grabpay-payments-singapore"),
    ("Shopify Payment Gateway Singapore", "https://hitpayapp.com/blog/shopify-payment-gateway-singapore"),
    ("Stripe Alternatives Singapore", "https://hitpayapp.com/blog/stripe-alternatives-singapore"),
    ("HitPay vs Stripe Singapore", "https://hitpayapp.com/blog/hitpay-vs-stripe-singapore"),
    ("HitPay vs PayPal Singapore", "https://hitpayapp.com/blog/hitpay-vs-paypal-singapore"),
    ("B2B Payment Solutions Singapore", "https://hitpayapp.com/blog/b2b-payment-solutions-singapore"),
    ("B2B Multi-Currency Payments Singapore", "https://hitpayapp.com/blog/b2b-multi-currency-payments-singapore"),
    ("Cross-Border Payouts Singapore", "https://hitpayapp.com/blog/cross-border-payouts-singapore-businesses"),
    ("NFC Payment Singapore", "https://hitpayapp.com/blog/nfc-payment-singapore"),
    ("SGQR Singapore Setup Guide", "https://hitpayapp.com/blog/sgqr-singapore-setup-guide"),
    ("ShopeePay BNPL Singapore", "https://hitpayapp.com/blog/shopeepay-bnpl-singapore"),
    ("Accept ShopeePay Singapore", "https://hitpayapp.com/blog/accept-shopeepay-singapore-business"),
    ("GrabPay PayLater Singapore", "https://hitpayapp.com/blog/grab-paylater-merchant-singapore"),
    ("Atome Singapore BNPL", "https://hitpayapp.com/blog/hitpay-atome-singapore"),
    ("PSG Grant POS Singapore", "https://hitpayapp.com/blog/most-affordable-pos-system-psg-grant-for-singapore-businesses"),
    ("Generate Free Payment Links Singapore", "https://hitpayapp.com/blog/generate-free-payment-links-singapore"),
    ("Lower Payment Processing Fees Singapore", "https://hitpayapp.com/blog/lower-payment-processing-fees-singapore"),
    ("Payment Service Provider Singapore", "https://hitpayapp.com/blog/payment-service-provider-singapore"),
    ("Payment Gateway API Singapore", "https://hitpayapp.com/blog/payment-gateway-api-singapore"),
    ("PayNow API Integration Singapore", "https://hitpayapp.com/blog/paynow-api-integration-singapore"),
    # MY
    ("Best Payment Gateway Malaysia", "https://hitpayapp.com/blog/best-payment-gateway-malaysia"),
    ("Payment Gateway Malaysia", "https://hitpayapp.com/blog/payment-gateway-malaysia"),
    ("Best Online Payment Solution Malaysia", "https://hitpayapp.com/blog/best-online-payment-solution-malaysia"),
    ("Payment Link Malaysia", "https://hitpayapp.com/blog/payment-link-malaysia"),
    ("Recurring Billing Malaysia", "https://hitpayapp.com/blog/recurring-billing-my"),
    ("FPX Payments Malaysia", "https://hitpayapp.com/blog/fpx-payments"),
    ("Best FPX Payment Gateway Malaysia", "https://hitpayapp.com/blog/best-fpx-payment-gateway-malaysia"),
    ("DuitNow API Integration Malaysia", "https://hitpayapp.com/blog/duitnow-api-integration-malaysia"),
    ("DuitNow QR Gateway Shopify Xero", "https://hitpayapp.com/blog/duitnow-qr-payment-gateway-shopify-xero"),
    ("Set Up DuitNow QR Malaysia", "https://hitpayapp.com/blog/how-to-set-up-duitnow-qr-malaysia-business"),
    ("Accept GrabPay Malaysia", "https://hitpayapp.com/blog/how-to-accept-grabpay-malaysia"),
    ("Touch n Go eWallet Malaysia", "https://hitpayapp.com/blog/touch-n-go-ewallet-merchant-malaysia"),
    ("Accept ShopeePay Malaysia", "https://hitpayapp.com/blog/accept-shopeepay-payments-malaysia"),
    ("Card Reader Malaysia", "https://hitpayapp.com/blog/card-reader-malaysia-business"),
    ("Card Payments Malaysia", "https://hitpayapp.com/blog/card-payments-malaysia"),
    ("Cashless Payment Methods Malaysia", "https://hitpayapp.com/blog/cashless-payment-methods-malaysia"),
    ("Popular Payment Methods Malaysia", "https://hitpayapp.com/blog/popular-payment-methods-malaysia"),
    ("POS System Malaysia", "https://hitpayapp.com/blog/best-pos-system-small-businesses-malaysia"),
    ("Tap to Pay Malaysia", "https://hitpayapp.com/blog/tap-to-pay-malaysia"),
    ("Contactless Payments Malaysia", "https://hitpayapp.com/blog/contactless-payments-malaysia"),
    ("NFC Payments Malaysia", "https://hitpayapp.com/blog/nfc-payments-malaysia"),
    ("Invoicing Malaysia", "https://hitpayapp.com/blog/invoicing-malaysia-business"),
    ("Create & Send Payment Link Malaysia", "https://hitpayapp.com/blog/how-to-create-send-payment-link-malaysia"),
    ("Stripe Alternatives Malaysia", "https://hitpayapp.com/blog/stripe-alternatives-malaysia"),
    ("HitPay vs Stripe Malaysia", "https://hitpayapp.com/blog/hitpay-vs-stripe-malaysia"),
    ("HitPay vs PayPal Malaysia", "https://hitpayapp.com/blog/hitpay-vs-paypal-malaysia"),
    ("B2B Payment Solutions Malaysia", "https://hitpayapp.com/blog/b2b-payment-solutions-malaysia"),
    ("Malaysia Payment Gateway Comparison", "https://hitpayapp.com/blog/malaysia-payment-gateway-comparison"),
    ("Boost Wallet Malaysia", "https://hitpayapp.com/blog/boost-wallet-merchant-malaysia"),
    ("Cross-Border Payments Malaysia", "https://hitpayapp.com/blog/cross-border-payments-malaysia"),
    ("Payment Gateway API Malaysia", "https://hitpayapp.com/blog/payment-gateway-api-malaysia"),
    ("BNPL Malaysia Businesses", "https://hitpayapp.com/blog/best-bnpl-options-malaysia-businesses"),
    ("ShopeePay BNPL Malaysia", "https://hitpayapp.com/blog/shopeepay-bnpl-malaysia"),
    # PH
    ("Best Payment Gateway Philippines", "https://hitpayapp.com/blog/best-payment-gateway-philippines"),
    ("Payment Gateway Philippines", "https://hitpayapp.com/blog/payment-gateway-philippines"),
    ("QR Code Payments Philippines", "https://hitpayapp.com/blog/how-to-accept-qr-code-payments-philippines"),
    ("QR Ph No Monthly Fee Comparison", "https://hitpayapp.com/blog/accept-qrph-with-no-monthly-fees-gateway-comparison-2025"),
    ("How to Generate QR Ph", "https://hitpayapp.com/blog/how-to-generate-qrph"),
    ("Ecommerce Payment Gateways Philippines", "https://hitpayapp.com/blog/ecommerce-payment-gateways-philippines"),
    ("Recurring Billing Philippines", "https://hitpayapp.com/blog/recurring-billing-ph"),
    ("Payment Link Philippines", "https://hitpayapp.com/blog/how-to-create-payment-link-philippines"),
    ("Accept GCash Philippines", "https://hitpayapp.com/blog/how-to-accept-gcash-payments-philippines"),
    ("GCash Payment Gateway Philippines", "https://hitpayapp.com/blog/gcash-payment-gateway-philippines"),
    ("GCash HitPay", "https://hitpayapp.com/blog/gcash-hitpay"),
    ("GCash API Integration Philippines", "https://hitpayapp.com/blog/gcash-api-integration-philippines"),
    ("HitPay Philippines Scan to Pay", "https://hitpayapp.com/blog/hitpay-philippines-scan-to-pay"),
    ("QR Ph GCash Maya Payment Philippines", "https://hitpayapp.com/blog/qr-ph-gcash-maya-payment-methods-philippines"),
    ("Cashless Payment Methods Philippines", "https://hitpayapp.com/blog/cashless-payment-methods-philippines"),
    ("POS System Philippines", "https://hitpayapp.com/blog/best-pos-system-small-businesses-philippines"),
    ("Best Card Terminal Philippines", "https://hitpayapp.com/blog/best-card-terminal-philippines-smes"),
    ("Accept InstaPay Philippines", "https://hitpayapp.com/blog/accept-instapay-payments-philippines"),
    ("Accept GrabPay Philippines", "https://hitpayapp.com/blog/accept-grabpay-philippines"),
    ("HitPay vs PayMongo Philippines", "https://hitpayapp.com/blog/hitpay-vs-paymongo-fees-in-2025-which-payment-gateway-saves-philippine-smes-more"),
    ("Philippines Payment Gateway Comparison", "https://hitpayapp.com/blog/philippines-payment-gateway-comparison"),
    ("Recurring Billing Philippines Subscriptions", "https://hitpayapp.com/blog/recurring-billing-philippines-subscription-payments"),
    ("QR Payment Soundbox Philippines", "https://hitpayapp.com/blog/qr-payment-soundbox-philippines"),
    ("Create Invoice Philippines", "https://hitpayapp.com/blog/create-invoice-philippines"),
    ("QRPH Payment Guide", "https://hitpayapp.com/blog/qrph-payment"),
]

_FALLBACK_URL = "https://hitpayapp.com/blog/hitpay-rates"
_SME_FALLBACK_URL = "https://smegrowthhub.com/blog"


def _is_valid_blog_url(url: str) -> bool:
    """Accept hitpayapp.com/blog/<slug> URLs — validates against the live sitemap cache."""
    if not re.match(r"^https://hitpayapp\.com/blog/[^\s]+$", url):
        return False
    slug = url.replace("https://hitpayapp.com/blog/", "")
    live_slugs = _fetch_live_blog_slugs()
    if live_slugs:
        return slug in live_slugs
    # Fallback: accept any well-formed slug when sitemap unavailable
    return bool(re.match(r"^[a-zA-Z0-9_\-()/%.]+$", slug))


_OVERUSED_STORY_EXAMPLES_TWEET = (
    "a guesthouse owner in El Nido, a textile shop in Chinatown, and a merchant named "
    "\"Maribel\" — these have been used so often they now read as templates, not stories"
)

_MERCHANT_ARCHETYPE_POOL = [
    "cafe owner", "restaurant owner", "hawker stall owner", "catering business owner",
    "bubble tea shop owner", "boutique fashion store owner", "electronics shop owner",
    "pharmacy owner", "bookstore owner", "freelance designer", "tuition centre owner",
    "cleaning company owner", "personal trainer", "events company owner",
    "Shopify seller", "WooCommerce seller", "social commerce seller", "Instagram boutique owner",
    "florist", "pet groomer", "dental clinic owner", "driving school owner", "gym owner",
]

_MERCHANT_NAME_POOL = [
    "Amirah", "Wei Ling", "Grace", "Farah", "Hui Min", "Dhevi", "Sarah", "Aisyah",
    "Kevin", "Faizal", "Marco", "Joel", "Ravi", "Daniel", "Nadia", "Jasmine",
    "Elena", "Rina", "Michelle", "Adrian", "Bea", "Jomar", "Cathy", "Ferdie",
]


def _build_merchant_story_prompt(thread_size: int) -> str:
    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in slugs)

    archetype = random.choice(_MERCHANT_ARCHETYPE_POOL)
    suggested_name = random.choice(_MERCHANT_NAME_POOL)

    slugs_str = urls_list
    example = json.dumps({
        "topic": "late payment cash flow",
        "tweets": [
            "She runs a small events company in KL. Her biggest client pays on 60-day terms. "
            "Her caterer wants payment in 14. She's been bridging that gap with her own savings for two years.",
            "She switched to HitPay invoicing — payment links, auto-reminders, one-tap pay. "
            "First month: three clients paid on time without a single follow-up call. "
            "The gap closed itself: [URL]",
        ],
        "link_url": "https://hitpayapp.com/blog/online-invoicing",
        "visual_note": None,
    }, ensure_ascii=False)

    example_1 = json.dumps({
        "topic": "late payment cash flow",
        "tweets": [
            "She runs an events company in KL. Her biggest client pays on 60-day terms. Her caterer wants 14. "
            "She switched to HitPay invoicing — auto-reminders, one-tap pay. Three clients paid on time that first month, no calls: [URL]",
        ],
        "link_url": "https://hitpayapp.com/blog/online-invoicing",
        "visual_note": None,
    }, ensure_ascii=False)

    return f"""You are the @hitpay_app content writer for X (Twitter) — Wednesday slot: Merchant Story.
Write a single tweet that tells a real-feeling story about a Southeast Asian merchant solving a problem with HitPay.

BRAND: HitPay — MAS-licensed (SG), BNM-approved (MY), BSP OPS-licensed (PH). No monthly fees. 50+ payment methods.
TONE: Narrative, specific, human. Like a journalist compressing a business story into one sentence — not a brand writing a press release.
AUDIENCE: SME founders and merchants in Singapore, Malaysia, or Philippines.

CONTENT FORMAT: Single tweet — no numbers, no thread emoji
- Exactly 1 tweet
- Do NOT use 🧵 or any thread emoji
- The full arc in one tweet: person + problem → what changed → concrete outcome
- 200–270 chars. End with [URL] as a literal placeholder.

MERCHANT ARCHETYPE FOR THIS STORY: {archetype}
Suggested name (use this one, or another equally specific and varied name — never reuse a name from a previous story): {suggested_name}

PAIN POINTS — pick the most compelling:
- Manual reconciliation eating hours every week
- Late invoices hurting cash flow
- Customers unable to pay due to missing payment methods
- High card fees on every transaction
- Checkout friction causing abandoned sales
- Cash flow gap between sale date and payout date

DO NOT default to overused examples: {_OVERUSED_STORY_EXAMPLES_TWEET}. Every story must feel freshly invented.

STYLE RULES:
- Open with "She" or "He" or "They" — name a real-feeling archetype, not "a business owner"
- Include one specific detail that makes it feel true (a number, a day, a place)
- End with a concrete result + a short reader-involving CTA with [URL] — connect the outcome back to the reader, e.g. "If you're still chasing the same invoices, this is how she fixed it: [URL]"
- No hashtags, no @ mentions, no 🧵
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage

LINK URL RULE:
Set link_url to https://hitpayapp.com/blog/{{slug}} using the most topically relevant slug.
If no clear match, default to: hitpay-rates

LIVE BLOG SLUGS:
{slugs_str}

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{example_1}

IMPORTANT: [URL] is a literal placeholder — never substitute the real URL."""


def _build_thought_leadership_prompt(thread_size: int) -> str:
    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in slugs)

    example = json.dumps({
        "topic": "manual bank reconciliation",
        "tweets": [
            "Every Monday morning reconciling last week's sales. "
            "Matching bank statements to payment records to a spreadsheet that's already out of date. "
            "HitPay reconciles automatically — if this is still how your Mondays start, it's worth a look: [URL]"
        ],
        "link_url": "https://hitpayapp.com/blog/hitpay-rates",
        "visual_note": None,
    }, ensure_ascii=False)

    return f"""You are the @hitpay_app content writer for X (Twitter) — Thought Leadership slot (Tue/Fri/Sat).
Write a single tweet. It can be an opinion, an observation, a question, or something that makes a merchant pause mid-scroll.

BRAND: HitPay — voice is authoritative, honest, occasionally contrarian. Not a brand account. A founder talking.
TONE: Direct, confident, occasionally opinionated. Never preachy. Never trying too hard.
AUDIENCE: SME founders, merchants, and finance managers in Southeast Asia who live the payment problems you name.

CONTENT FORMAT: Single standalone tweet — no thread, no emoji
- Exactly 1 tweet
- Do NOT use 🧵 or any thread emoji
- Do NOT number the tweet
- 200–270 chars
- Introduce both the problem and the HitPay solution. Structure: name the problem clearly → briefly show how HitPay addresses it → end with a reader-involving CTA and [URL].
- Keep the problem observation sharp and specific — that's still what earns the read. The solution should be one concise sentence, not a feature list.
- HitPay must appear as the practical answer to the problem named.

ANGLES — pick the sharpest for the topic:
- Opinion: a direct stance on a broken practice ("Cash is not simpler. It just shifts the cost somewhere invisible.")
- Observation: something a merchant feels but hasn't articulated ("The card machine fee is visible. The queue it creates is not.")
- Question: one that lands ("Why do we accept that B2B invoices take 60 days to collect but B2C takes 2 seconds?")
- Emotion: name the frustration without manufacturing it ("Every Monday morning reconciling last week's sales. Every week.")

TOPIC POOL — pick the sharpest if no hint is given:
- Why some people prefer cash (reliability, not habit)
- The real cost of a slow checkout page (it's not conversion rate — it's trust)
- Late payment culture and what it actually costs a small business
- The myth that cash is simpler for a growing business
- Why QR payments feel different to customers than swiping a card
- The labour cost of manual bank reconciliation
- Why merchants don't fight chargebacks (and what happens when they do)
- What "next business day payouts" means when rent is due Thursday
- The checkout page with one payment method that silently turns people away

CRITICAL — NEVER ATTACK HITPAY'S OWN PRODUCTS:
Never frame card terminals, POS hardware, or in-person card acceptance as outdated or inferior.
Attack PROCESSES or BEHAVIOURS only.

STYLE RULES:
- No hashtags, no @ mentions, no 🧵
- Banned structures: "Honest truth:", "Hot take:", "Unpopular opinion:", "This is your reminder", "Read that again."
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{example}"""


SME_TOPIC_POOL = [
    "How to manage cash flow when customers pay late",
    "When to hire your first employee — and what it actually costs",
    "Business registration in Singapore: sole proprietor vs Pte Ltd",
    "How to reduce payment processing fees without switching everything",
    "E-commerce in SEA: marketplace vs own store — the real trade-off",
    "Why your payment gateway choice affects your cash position",
    "What SMBs get wrong about B2B invoicing",
    "GST/SST registration: when you need it and when you don't",
    "Digital marketing on a tight budget for SEA SMBs",
    "How to pick accounting software you'll actually use",
    "Cross-border payments for small businesses — simpler than you think",
    "The hidden cost of cash for small businesses",
]

SME_STORYTELLING_TOPIC_POOL = [
    "The moment you realised cash was silently costing you",
    "Hiring your first employee — what nobody tells you",
    "The accounting tool switch that changed everything",
    "Late payments and what they actually cost",
    "When a sale nearly didn't happen because of payment friction",
    "The invoice that never got paid — and what you learned",
    "Why moving from marketplace to own store felt impossible at first",
    "The week you realised your pricing was wrong",
    "What changed when you started separating business and personal finances",
    "The supplier payment that nearly broke the relationship",
]


def _build_sme_educational_prompt(thread_size: int) -> str:
    from src.brand_config import get_brand_config
    bc = get_brand_config("smegrowthhub")

    if thread_size == 1:
        format_section = """CONTENT FORMAT: Single standalone tweet
- Exactly 1 tweet, no numbering
- 200–280 chars, fully self-contained
- Ends with a reference to the article + [URL] as a literal placeholder"""
        output_example = (
            '{"topic": "cash flow management", '
            '"tweets": ["Most Singapore SMBs invoice at the end of the month. '
            'That habit alone adds 14 days to your average collection time. Invoice immediately after delivery: [URL]"], '
            f'"link_url": "{bc.blog_base_url}/cash-flow-invoicing", "visual_note": null}}'
        )
    elif thread_size == 2:
        format_section = (
            "CONTENT FORMAT: Thread of exactly 2 tweets\n"
            "- Tweet 1/2: introduce the concept clearly with a key fact — end with 🧵\n"
            "- Tweet 2/2: concrete example or actionable takeaway + article reference + [URL]\n"
            "- Each tweet: 200–280 chars"
        )
        output_example = (
            '{"topic": "...", "tweets": ["1/2 ... 🧵", "2/2 ... [URL]"], '
            f'"link_url": "{bc.blog_base_url}/article-slug", "visual_note": null}}'
        )
    else:
        format_section = (
            f"CONTENT FORMAT: Thread of exactly {thread_size} tweets\n"
            f"- {thread_size} tweets numbered \"1/{thread_size} ...\", etc.\n"
            "- Tweet 1: introduce the concept clearly — definition or hook — end with 🧵\n"
            "- Middle tweets: specific facts, numbers, concrete examples from the SEA context\n"
            f"- Final tweet: actionable takeaway + article reference + [URL] as literal placeholder\n"
            "- Each tweet: 200–280 chars, self-contained"
        )
        example_tweets = [f'"{i}/{thread_size} ..."' for i in range(1, thread_size + 1)]
        example_tweets[-1] = f'"{thread_size}/{thread_size} ... [URL]"'
        output_example = (
            '{"topic": "...", "tweets": [' + ", ".join(example_tweets) + '], '
            f'"link_url": "{bc.blog_base_url}/article-slug", "visual_note": null}}'
        )

    return f"""You are the X content writer for SME Growth Hub, an independent editorial resource for small business operators across Southeast Asia.
Write practical, peer-to-peer content that teaches SEA SME owners something genuinely useful — the kind a trusted peer would share after doing the research.

VOICE: Independent advisor, not a brand account. Direct, warm, specific.
AUDIENCE: SME owners and operators in SG/MY/PH across F&B, retail, services, e-commerce, freelancing.

{format_section}

STYLE RULES:
- Use specific numbers and concrete examples — "a 2-week delay on RM3,000 invoice" beats "late payments are costly"
- Em-dashes (—) are fine, sparingly
- No hashtags, no @ mentions
- No URLs in any tweet except the final one — use [URL] as a literal placeholder there only
- No promotional language
- HitPay may be mentioned in payment-related content as a peer recommendation — e.g. "For Singapore SMBs, HitPay is the simplest starting point". Never as the main subject.
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust

TOPIC POOL — pick the most relevant if no hint is given:
{chr(10).join(f'- {t}' for t in SME_TOPIC_POOL)}

LINK URL RULE:
Set link_url to {bc.blog_base_url}/{{slug}} using a descriptive slug matching the topic.
If no specific slug is available, default to: {bc.blog_base_url}

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{output_example}

IMPORTANT: [URL] in the tweet(s) is a literal placeholder — never substitute the real URL into the tweet text itself."""


def _build_sme_storytelling_prompt(thread_size: int) -> str:
    from src.brand_config import get_brand_config
    bc = get_brand_config("smegrowthhub")

    if thread_size == 1:
        format_section = """CONTENT FORMAT: Single standalone tweet
- Exactly 1 tweet, no numbering
- 200–280 chars
- A compact narrative arc: observation → insight or tension → resolution
- Ends with a reference to the article + [URL] as literal placeholder"""
        output_example = (
            '{"topic": "late payments", '
            '"tweets": ["Most SMBs track late invoices. Few track what they cost in working capital. '
            'A 30-day delay on a S$5,000 invoice is roughly S$25 of lost cash value at the bank. '
            'The fix is simpler than chasing: [URL]"], '
            f'"link_url": "{bc.blog_base_url}/late-payments", "visual_note": null}}'
        )
    else:
        format_section = (
            f"CONTENT FORMAT: Storytelling thread of exactly {thread_size} tweets\n"
            f"- {thread_size} tweets numbered \"1/{thread_size} ...\", etc.\n"
            "- Tweet 1: open with an observation or tension — something a business owner feels but rarely names. End with 🧵\n"
            "- Middle tweets: deepen with a concrete scenario. Still no pitch.\n"
            f"- Final tweet: resolution or reframe. Where the article's insight fits naturally. End with [URL].\n"
            "- Each tweet: 180–280 chars, short punchy sentences"
        )
        example_tweets = [f'"{i}/{thread_size} ..."' for i in range(1, thread_size + 1)]
        example_tweets[-1] = f'"{thread_size}/{thread_size} ... [URL]"'
        output_example = (
            '{"topic": "...", "tweets": [' + ", ".join(example_tweets) + '], '
            f'"link_url": "{bc.blog_base_url}/article-slug", "visual_note": null}}'
        )

    return f"""You are the X storytelling writer for SME Growth Hub, an independent resource for Southeast Asian business operators.
Write posts that make SMB owners pause and recognise something they already feel but couldn't quite name.

VOICE: Peer who has spent time around these businesses. Reflective, honest, slightly philosophical. Not a brand.
AUDIENCE: SME founders and operators in Southeast Asia (SG/MY/PH).

{format_section}

STYLE RULES:
- No statistics in middle tweets (sparingly in final only)
- Short declarative sentences. Fragments are fine.
- No hashtags, no @ mentions
- No URLs except final tweet — use [URL] as literal placeholder
- No promotional language until the final tweet
- HitPay may appear in the final tweet only if the topic is payment-related — as a natural peer recommendation, never as a pitch
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust

NARRATIVE THEMES — pick the most resonant if no hint is given:
{chr(10).join(f'- {t}' for t in SME_STORYTELLING_TOPIC_POOL)}

LINK URL RULE:
Set link_url to {bc.blog_base_url}/{{slug}} using a descriptive slug matching the topic.
If no specific slug is available, default to: {bc.blog_base_url}

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{output_example}

IMPORTANT: [URL] in the tweet(s) is a literal placeholder — never substitute the real URL."""



def _build_product_focus_prompt(thread_size: int) -> str:
    slugs = _fetch_live_blog_slugs()
    urls_list = "\n".join(f"  {s}" for s in slugs)
    example = json.dumps({
        "topic": "Tap to Pay on iPhone",
        "tweets": [
            "HitPay Tap to Pay on iPhone is live. "
            "Accept card and e-wallet payments in person using your iPhone — no card reader, no terminal rental. "
            "Set up in minutes: [URL]"
        ],
        "link_url": "https://hitpayapp.com/blog/tap-to-pay-on-iphone",
        "visual_note": "Short screen recording: phone tap, payment confirmed",
    }, ensure_ascii=False)

    return f"""You are the @hitpay_app content writer for X (Twitter) — Product post (Mon/Thu/Sun).
Write a single tweet about a specific HitPay feature or product update.
Lead with the merchant outcome or problem solved — not the feature name.

BRAND: HitPay — MAS-licensed (SG), BNM-approved (MY), BSP OPS-licensed (PH). No monthly fees. 50+ payment methods.
TONE: Direct, practical, founder-voice. Like someone who built this and is quietly proud of it.
AUDIENCE: SME founders and merchants in Southeast Asia.

CONTENT FORMAT: Single tweet — no thread, no emoji marker
- Exactly 1 tweet
- Do NOT use 🧵 or any thread emoji
- Do NOT number the tweet
- 200–270 chars
- End with [URL] as a literal placeholder

HITPAY FEATURES TO DRAW FROM — use these verified facts, not generic descriptions:
- Tap to Pay on iPhone: accept in-person card and e-wallet payments using your iPhone — no hardware needed
- Recurring Bulk Subscriptions: create subscriptions for multiple customers at once from one dashboard upload
- Shareable Carts: generate a cart link pre-loaded with items that customers open and pay instantly
- Touch n Go Offline (MY): accept TnG e-wallet in-person via POS terminal
- GrabPay and PayLater by Grab Offline (MY): accept Grab wallets in-person at checkout
- GCash Offline (PH): accept GCash in-person via HitPay POS terminal
- ShopeePay for Subscriptions (SG and PH): charge ShopeePay for recurring/subscription payments
- PayLater by Grab Online (MY): merchants can offer Grab BNPL at online checkout
- HitPay Payout Rails: own settlement infrastructure for SG, MY, PH — next business day payouts
- Save Payment Details: returning customers pay in one tap — card details saved via tokenisation
- Online Store Templates: launch a branded online store without a developer
- Payment links: shareable, one-click payment for any amount or invoice
- PayNow / DuitNow / QR Ph: QR code generation for in-person or remote collection
- WooCommerce, Shopify, Wix, Magento integrations
- Multi-currency checkout for cross-border sales
- Automatic payment reconciliation and export
- Invoice generation with payment tracking and auto-reminders

STYLE RULES:
- Lead with what the merchant can now DO or STOP DOING — not what the feature is called
- Lead with the problem/outcome, then what HitPay does. End with a short reader-involving CTA that includes [URL] in the sentence — e.g. "If you're still turning away card customers, this takes 5 minutes to set up: [URL]" rather than just "Set up in minutes: [URL]"
- No hashtags, no @ mentions
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust
- NEVER disparage card terminals, POS hardware, or any payment method HitPay offers

LINK URL RULE:
Set link_url to https://hitpayapp.com/blog/<slug> using the most topically relevant slug from the list below.
If no clear match, default to: hitpay-rates

LIVE BLOG SLUGS:
{urls_list}

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{json.dumps(example, ensure_ascii=False)}

IMPORTANT: [URL] is a literal placeholder — never substitute the real URL."""


TOPIC_POOL = [
    "MDR (Merchant Discount Rate) and interchange fees explained",
    "Payment settlement timelines: T+0, T+1, T+2 and why they matter",
    "QR vs card payments: cost and speed comparison for SMEs",
    "How chargebacks work and how merchants can fight them",
    "Cross-border payment fees in SEA and how to reduce them",
    "The hidden cost of free payment terminals",
    "Buy Now Pay Later economics from the merchant's perspective",
    "Cash flow impact of choosing the wrong payment method",
    "POS vs online vs invoice payments — when to use which",
    "Real-time payments in SEA: PayNow, DuitNow, QR Ph adoption",
    "Payment reconciliation: how to stop losing hours to manual matching",
    "Recurring billing and subscription payment mechanics",
]

STORYTELLING_TOPIC_POOL = [
    "Payment systems built for where you are, not who you serve",
    "The silent cost of cash that nobody talks about",
    "Why your invoice tool doesn't feel like yours",
    "The gap between 'accepted' and 'paid'",
    "Cross-border payments from a customer's perspective",
    "What a tourist feels when their card gets declined",
    "The moment a business realises their checkout is losing them customers",
    "Reconciliation as a symptom, not a problem",
    "Why 'no monthly fee' changes how merchants take risks",
    "The difference between a payment tool and payment infrastructure",
    "What getting paid really means for a small business",
    "How payment friction becomes a business culture problem",
]

# Shared topic pool used for automated X and Threads generation when no topic_hint is supplied
HITPAY_TOPIC_POOL: list[str] = [
    # How payments work
    "MDR explained: who keeps your 2.8% card fee and why",
    "Payment settlement timelines: T+0, T+1, T+2 and what they mean for cash flow",
    "How a payment gateway differs from a payment processor",
    "How chargebacks work and what merchants can do to fight them",
    "Buy Now Pay Later economics from the merchant's perspective",
    "How recurring billing and subscription payments actually work",
    "What interchange fees are and why they vary by card type",
    "How QR code payments work end-to-end for a merchant",
    # Costs & fees
    "The real cost of accepting cash: errors, time, and operational risk",
    "Why free payment terminals are rarely actually free",
    "How to reduce card processing fees without rebuilding your stack",
    "The hidden cost of a slow or broken checkout page",
    "What 'no monthly fee' really means for a growing business",
    "Fee transparency: understanding every line item on your payment provider invoice",
    "Paying 3% MDR when real-time alternatives cost under 1.5%: the maths",
    # Operations
    "Payment reconciliation: why it eats hours and how to automate it",
    "POS vs payment link vs invoice: when to use which",
    "How to handle a payment dispute and protect your revenue",
    "How to accept cross-border payments from tourists in Southeast Asia",
    "Multi-currency checkout: when it makes sense for a small business",
    "How to create and share a payment link for instant collections",
    "Setting up PayNow, DuitNow, or QR Ph for your business: what to expect",
    # Cash flow & business impact
    "How late invoices damage SME cash flow more than transaction fees do",
    "Why next business day payouts matter for small business working capital",
    "The checkout abandonment problem: how missing payment methods cost you sales",
    "How accepting e-wallets changes customer spending behaviour in SEA",
    "How payment method choice affects customer trust at checkout",
    "Cash flow impact of T+1 vs T+2 settlement: what the difference adds up to",
    "The gap between a sale and the money in your account — and how to close it",
    # Market & adoption
    "Real-time payments in Southeast Asia: PayNow, DuitNow, and QR Ph compared",
    "E-wallet landscape in Malaysia: GrabPay, TnG eWallet, ShopeePay for merchants",
    "QR code payments replacing card terminals in SG, MY, and PH",
    "Cross-border QR payments: accepting tourists from Thailand, Indonesia, and China",
    "How digital payments are changing F&B businesses across Southeast Asia",
    "The shift from cash to QR in Philippine sari-sari stores and wet markets",
    # Merchant-specific
    "Payment strategy for Shopify and WooCommerce stores in SEA",
    "How subscription businesses should think about their payment method mix",
    "Invoice payment best practices for service businesses in SEA",
    "Why B2B merchants lose more to late payment than to high fees",
    "How to optimise checkout for mobile-first customers in Southeast Asia",
    # Real HitPay product features (from changelog)
    "Tap to Pay on iPhone: accepting card payments in-person without a card terminal",
    "How recurring bulk subscriptions save time for businesses with many subscribers",
    "Shareable cart links: a faster way to sell without a full online store",
    "Touch n Go, GrabPay, and PayLater by Grab now available offline for Malaysian merchants",
    "GCash offline payments: what it means for Philippine merchants at the counter",
    "ShopeePay for subscriptions: accepting recurring payments via e-wallet in SG and PH",
    "PayLater by Grab for online merchants in Malaysia: offering BNPL without building anything",
    "How HitPay Payout Rails give SG, MY, and PH merchants faster, more reliable settlements",
    "Save payment details: how one-tap checkout for returning customers increases conversion",
    "Launching an online store without a developer using HitPay's built-in templates",
]

# All valid (style, thread_size) combinations — used for randomized automation
_AUTOMATION_VARIANTS: list[tuple[str, int]] = [
    ("educational", 1),
    ("educational", 3),
    ("educational", 5),
    ("educational", 7),
    ("storytelling", 1),
    ("storytelling", 2),
    ("storytelling", 3),
    ("storytelling", 5),
]

_AUTOMATION_MARKETS = ["SG", "MY", "PH", None]  # None = SEA broadly

CONTENT_TYPE_BY_WEEKDAY: dict[int, str] = {
    0: "product_focus",      # Monday
    1: "thought_leadership", # Tuesday
    2: "merchant_story",     # Wednesday — 1x/week storytelling
    3: "product_focus",      # Thursday
    4: "thought_leadership", # Friday
    5: "thought_leadership", # Saturday
    6: "product_focus",      # Sunday
}

CONTENT_TYPE_CONFIGS: dict[str, dict] = {
    "product_focus":      {"thread_size": 1, "style": "product"},
    "merchant_story":     {"thread_size": 1, "style": "storytelling"},
    "thought_leadership": {"thread_size": 1, "style": "opinion"},
}

# product_focus uses a published blog post as source context when available.
_BLOG_REPURPOSE_CONTENT_TYPES = {"product_focus"}


def generate_random_x_post(
    market: str = None,
    topic_hint: str = None,
    brand: str = "hitpay",
    content_type: str | None = None,
) -> dict:
    """Generate an X post using day-of-week content type (hitpay) or random variant (other brands).

    Monday/Thursday/Sunday → product_focus (features, announcements, 1 tweet)
    Tuesday/Friday/Saturday → thought_leadership (opinion/observation, 1 tweet)
    Wednesday → merchant_story (2-tweet narrative, 1x/week storytelling)

    product_focus repurposes a published blog post when one is available.
    Pass content_type to override day-of-week detection (useful for testing).
    Returns dict with keys: topic, tweets, link_url, visual_note, style, thread_size, market, content_type
    """
    if brand == "hitpay":
        if topic_hint is None:
            topic_hint = random.choice(HITPAY_TOPIC_POOL)
        if content_type is None:
            weekday = _datetime.utcnow().weekday()
            content_type = CONTENT_TYPE_BY_WEEKDAY.get(weekday, "product_focus")
        config = CONTENT_TYPE_CONFIGS[content_type]
        thread_size = config["thread_size"]
        style = config["style"]
    else:
        style, thread_size = random.choice(_AUTOMATION_VARIANTS)
        content_type = None

    chosen_market = market if market is not None else random.choice(_AUTOMATION_MARKETS)

    if brand == "hitpay" and content_type in _BLOG_REPURPOSE_CONTENT_TYPES:
        from src.database import get_unrepurposed_published_post, mark_post_x_repurposed
        from src.repurposer import repurpose_post_as_thread
        post = get_unrepurposed_published_post(brand=brand)
        if post:
            result = repurpose_post_as_thread(post, thread_size)
            mark_post_x_repurposed(post["id"])
            result["style"] = style
            result["thread_size"] = thread_size
            result["market"] = post.get("country") or chosen_market
            result["content_type"] = content_type
            result["source_post_id"] = post["id"]
            return result
        # No published posts left — fall through to topic-pool generation

    result = generate_thought_leadership_thread(
        market=chosen_market,
        topic_hint=topic_hint,
        thread_size=thread_size,
        style=style,
        content_type=content_type,
        brand=brand,
    )
    result["style"] = style
    result["thread_size"] = thread_size
    result["market"] = chosen_market
    result["content_type"] = content_type
    return result


def generate_thought_leadership_thread(
    market: str = None,
    topic_hint: str = None,
    thread_size: int = 7,
    style: str = "educational",
    content_type: str | None = None,
    brand: str = "hitpay",
) -> dict:
    """Generate a standalone thought leadership X post or thread on a payments topic.

    Args:
        market: Optional market code (SG, MY, PH)
        topic_hint: Optional topic to focus on
        thread_size: Number of tweets — 1, 2 (storytelling), 3, 5, or 7
        style: "educational" (data-driven) or "storytelling" (narrative arc)

    Returns: {"topic": str, "tweets": list[str], "link_url": str, "visual_note": str | None}
    """
    if style not in ("educational", "storytelling", "opinion", "product"):
        raise ValueError(f"style must be 'educational', 'storytelling', 'opinion', or 'product' — got {style!r}")

    if thread_size not in (1, 2, 3, 5, 7):
        raise ValueError(f"thread_size must be one of (1, 2, 3, 5, 7) — got {thread_size}")

    client = OpenRouterClient()
    context_parts = []

    from src.brand_config import get_brand_config
    bc = get_brand_config(brand)

    if market and market in COUNTRY_CONTEXT:
        ctx = COUNTRY_CONTEXT[market]
        context_parts.append(
            f"TARGET MARKET: {ctx['name']} ({market})\n"
            f"Local payment methods: {ctx['local_methods']}\n"
            f"Cross-border: {ctx['cross_border']}\n"
            f"Payout timing: {ctx['payout']}"
        )
    else:
        context_parts.append("TARGET MARKET: Southeast Asia broadly (SG, MY, PH)")

    if topic_hint:
        product_docs = _load_relevant_docs(topic_hint, docs_file=bc.docs_file, max_chars=8000)
        if product_docs:
            context_parts.append(f"KNOWLEDGE BASE CONTEXT:\n{product_docs}")
        context_parts.append(f"TOPIC: Write the content about: {topic_hint}")
    else:
        default_pool = "topic pool for a general SEA SMB audience" if brand == "smegrowthhub" else "topic pool for a general SEA payments audience"
        context_parts.append(f"Pick the best topic from the {default_pool}.")

    context_parts.append(
        f"FORMAT: Generate exactly {thread_size} tweet(s) as specified."
    )

    user_message = "\n\n".join(context_parts)

    if brand == "smegrowthhub":
        prompt_builder = _build_sme_storytelling_prompt if style == "storytelling" else _build_sme_educational_prompt
    elif content_type:
        _ct_map = {
            "product_focus":      _build_product_focus_prompt,
            "merchant_story":     _build_merchant_story_prompt,
            "thought_leadership": _build_thought_leadership_prompt,
        }
        prompt_builder = _ct_map.get(content_type, _build_thought_leadership_prompt)
    else:
        prompt_builder = _build_storytelling_prompt if style == "storytelling" else _build_thought_leadership_prompt

    msg = _messages_create_with_retry(
        client,
        model=OPENROUTER_MODEL,
        max_tokens=2500,
        system=prompt_builder(thread_size) + "\n\n" + _WRITING_STYLE_RULES,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = msg.content[0].text.strip()

    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)
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
            raise ValueError(f"Could not parse thought leadership response: {e}")

    tweets = data.get("tweets")
    if not isinstance(tweets, list) or len(tweets) < 1:
        raise ValueError(f"Expected tweets array, got: {tweets!r}")

    # Trim if model over-generates
    tweets = tweets[:thread_size]

    if len(tweets) < thread_size:
        raise ValueError(f"Expected {thread_size} tweet(s), got {len(tweets)}")

    tweets = [_cap_tweet(t) for t in tweets]

    # hot_take posts are intentionally text-only — no URL attached
    if content_type == "thought_leadership":
        link_url = None
        # thought_leadership is text-only — strip any stray [URL] Claude may have added.
        # Only touch the tweet (and re-close the sentence) if [URL] was actually present —
        # otherwise this was clobbering the tweet's own genuine closing full stop.
        last = tweets[-1]
        if "[URL]" in last:
            last = last.replace("[URL]", "").rstrip().rstrip("…").rstrip()
            if last and not last.endswith((".", "!", "?", "…")):
                last += "."
        tweets[-1] = last
    else:
        fallback = _SME_FALLBACK_URL if brand == "smegrowthhub" else _FALLBACK_URL
        link_url = data.get("link_url") or fallback
        if brand != "smegrowthhub" and not _is_valid_blog_url(link_url):
            link_url = fallback

        # Ensure the last tweet carries the [URL] placeholder.
        # Claude sometimes outputs … (U+2026) or ... instead of [URL].
        last = tweets[-1]
        if "[URL]" not in last:
            for ellipsis in ("…", "..."):
                if last.endswith(ellipsis):
                    tweets[-1] = last[: -len(ellipsis)].rstrip() + " [URL]"
                    break
            else:
                # No ellipsis either — append [URL] after a space
                tweets[-1] = _cap_tweet(last.rstrip() + " [URL]")

    return {
        "topic": data.get("topic", ""),
        "tweets": tweets,
        "link_url": link_url,
        "visual_note": data.get("visual_note"),
    }
