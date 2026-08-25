#!/usr/bin/env python3
"""
Competitor research scraper for HitPay blog post generator.
Scrapes competitor payment platforms and extracts structured data.
"""

import asyncio
import json
import os
import time
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from config import OPENROUTER_HAIKU_MODEL
from src.llm_client import OpenRouterClient

load_dotenv()

COMPETITORS_DIR = Path("competitors")
COMPETITORS_DIR.mkdir(exist_ok=True)

# ── URL lists per competitor ──────────────────────────────────────────────────

COMPETITOR_URLS = {
    "stripe": {
        "name": "Stripe",
        "base_url": "https://stripe.com",
        "markets": ["Global", "Singapore", "Malaysia", "Philippines"],
        "urls": [
            "https://stripe.com/en-sg",
            "https://stripe.com/en-sg/use-cases",
            "https://stripe.com/en-sg/pricing",
            "https://stripe.com/en-sg/payments",
            "https://stripe.com/en-sg/billing",
            "https://stripe.com/en-sg/checkout",
            "https://stripe.com/en-sg/invoicing",
            "https://stripe.com/en-sg/terminal",
            "https://stripe.com/en-sg/connect",
            "https://stripe.com/en-sg/radar",
            "https://stripe.com/en-sg/sigma",
            "https://stripe.com/en-sg/tax",
            "https://stripe.com/en-sg/identity",
            "https://stripe.com/en-sg/issuing",
            "https://stripe.com/en-sg/payment-links",
            "https://stripe.com/en-sg/elements",
            "https://stripe.com/en-sg/use-cases/saas",
            "https://stripe.com/en-sg/use-cases/ecommerce",
            "https://stripe.com/en-sg/use-cases/platforms",
            "https://stripe.com/en-sg/use-cases/marketplaces",
            "https://stripe.com/en-sg/use-cases/creator-economy",
            "https://stripe.com/en-sg/stripe-apps",
            "https://stripe.com/en-sg/enterprise",
            "https://stripe.com/en-sg/startup",
            "https://stripe.com/en-sg/customers",
            "https://stripe.com/en-my",
            "https://stripe.com/en-my/pricing",
            "https://stripe.com/en-ph",
            "https://stripe.com/en-ph/pricing",
            "https://stripe.com/en-sg/guides/payment-methods-guide",
        ]
    },
    "adyen": {
        "name": "Adyen",
        "base_url": "https://www.adyen.com",
        "markets": ["Global", "Singapore", "Malaysia", "Philippines"],
        "urls": [
            "https://www.adyen.com",
            "https://www.adyen.com/pricing",
            "https://www.adyen.com/payment-methods",
            "https://www.adyen.com/solutions",
            "https://www.adyen.com/industries/retail",
            "https://www.adyen.com/industries/food-and-beverage",
            "https://www.adyen.com/industries/ecommerce",
            "https://www.adyen.com/industries/platforms",
            "https://www.adyen.com/platform",
            "https://www.adyen.com/our-solution/unified-commerce",
            "https://www.adyen.com/our-solution/fraud-and-risk",
            "https://www.adyen.com/our-solution/data-and-analytics",
            "https://www.adyen.com/our-solution/issuing",
            "https://www.adyen.com/our-solution/banking",
            "https://www.adyen.com/our-solution/in-person-payments",
            "https://www.adyen.com/our-solution/online-payments",
            "https://www.adyen.com/about-us",
        ]
    },
    "airwallex": {
        "name": "Airwallex",
        "base_url": "https://www.airwallex.com",
        "markets": ["Global", "Singapore", "Malaysia", "Philippines", "Hong Kong", "Australia"],
        "urls": [
            "https://www.airwallex.com",
            "https://www.airwallex.com/sg",
            "https://www.airwallex.com/sg/pricing",
            "https://www.airwallex.com/sg/solutions/accept-payments",
            "https://www.airwallex.com/sg/solutions/send-payments",
            "https://www.airwallex.com/sg/solutions/manage-spending",
            "https://www.airwallex.com/sg/products/payment-link",
            "https://www.airwallex.com/sg/products/payment-gateway",
            "https://www.airwallex.com/sg/products/global-accounts",
            "https://www.airwallex.com/sg/products/cards",
            "https://www.airwallex.com/sg/products/expense-management",
            "https://www.airwallex.com/sg/solutions/ecommerce",
            "https://www.airwallex.com/sg/solutions/saas",
            "https://www.airwallex.com/sg/solutions/startups",
            "https://www.airwallex.com/my",
            "https://www.airwallex.com/ph",
        ]
    },
    "2c2p": {
        "name": "2C2P",
        "base_url": "https://2c2p.com",
        "markets": ["Southeast Asia", "Singapore", "Malaysia", "Philippines", "Thailand", "Vietnam"],
        "urls": [
            "https://2c2p.com",
            "https://2c2p.com/our-solutions",
            "https://2c2p.com/payment-methods",
            "https://2c2p.com/about-us",
            "https://2c2p.com/our-solutions/online-payment-gateway",
            "https://2c2p.com/our-solutions/in-store",
            "https://2c2p.com/our-solutions/cross-border",
            "https://2c2p.com/our-solutions/smart-payment-page",
            "https://2c2p.com/our-solutions/payment-links",
            "https://2c2p.com/industries",
        ]
    },
    "paypal": {
        "name": "PayPal",
        "base_url": "https://www.paypal.com",
        "markets": ["Global", "Singapore", "Malaysia", "Philippines"],
        "urls": [
            "https://www.paypal.com/sg/home",
            "https://www.paypal.com/sg/business",
            "https://www.paypal.com/sg/merchant/fees",
            "https://www.paypal.com/sg/webapps/mpp/merchant",
            "https://www.paypal.com/sg/webapps/mpp/accept-payments-online",
            "https://www.paypal.com/sg/webapps/mpp/paypal-checkout",
            "https://www.paypal.com/sg/webapps/mpp/send-money-online",
            "https://www.paypal.com/sg/webapps/mpp/invoicing",
            "https://www.paypal.com/my/home",
            "https://www.paypal.com/ph/home",
            "https://www.paypal.com/ph/business",
        ]
    },
    "xendit": {
        "name": "Xendit",
        "base_url": "https://www.xendit.co",
        "markets": ["Philippines", "Indonesia", "Malaysia", "Thailand", "Vietnam"],
        "urls": [
            "https://www.xendit.co",
            "https://www.xendit.co/en-ph/",
            "https://www.xendit.co/en-ph/pricing/",
            "https://www.xendit.co/en-ph/solutions/",
            "https://www.xendit.co/en-ph/payment-methods/",
            "https://www.xendit.co/en-ph/products/payment-gateway/",
            "https://www.xendit.co/en-ph/products/payment-links/",
            "https://www.xendit.co/en-ph/products/invoices/",
            "https://www.xendit.co/en-ph/products/payouts/",
            "https://www.xendit.co/en-ph/products/recurring/",
            "https://www.xendit.co/en-ph/industries/",
            "https://www.xendit.co/en-id/",
            "https://www.xendit.co/en-my/",
        ]
    },
    "maya": {
        "name": "Maya Business",
        "base_url": "https://www.maya.ph",
        "markets": ["Philippines"],
        "urls": [
            "https://www.maya.ph",
            "https://www.maya.ph/for-business",
            "https://www.maya.ph/for-business/accept-payments",
            "https://www.maya.ph/for-business/maya-checkout",
            "https://www.maya.ph/for-business/qr",
            "https://www.maya.ph/for-business/payment-links",
            "https://www.maya.ph/for-business/lending",
            "https://www.maya.ph/for-business/payroll",
        ]
    },
    "paymongo": {
        "name": "PayMongo",
        "base_url": "https://www.paymongo.com",
        "markets": ["Philippines"],
        "urls": [
            "https://www.paymongo.com",
            "https://www.paymongo.com/pricing",
            "https://www.paymongo.com/solutions",
            "https://www.paymongo.com/payment-link",
            "https://www.paymongo.com/about",
            "https://www.paymongo.com/plugins",
        ]
    },
}

# ── Scraper ────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def fetch_page(url: str, timeout: int = 15) -> str | None:
    """Fetch a page and return cleaned text content."""
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                print(f"  ✗ {url} → HTTP {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, "lxml")

            # Remove noise elements
            for tag in soup(["script", "style", "nav", "footer", "header",
                             "aside", "iframe", "noscript", "svg", "img"]):
                tag.decompose()

            # Try to get main content
            main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body
            if not main:
                return None

            text = main.get_text(separator="\n", strip=True)

            # Collapse whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            # Limit to 4000 chars to keep Claude costs low
            return text[:4000] if text else None

    except Exception as e:
        print(f"  ✗ {url} → {e}")
        return None


def extract_facts_with_claude(competitor_name: str, url: str, page_text: str) -> dict:
    """Use Claude Haiku to extract structured facts from page text."""
    client = OpenRouterClient()

    prompt = f"""You are extracting structured facts about a payment platform competitor called "{competitor_name}" from their website page: {url}

Page content:
{page_text}

Extract ONLY facts explicitly stated on this page. Do not invent or infer. If a fact isn't on this page, omit it.

Return a JSON object with any of these fields that are present on this page:
{{
  "pricing": {{
    "monthly_fee": "exact text or null",
    "setup_fee": "exact text or null",
    "transaction_fees": {{"cards": "rate", "local_methods": "rate", "notes": "any caveats"}},
    "pricing_model": "per-transaction / subscription / custom / quote-based"
  }},
  "payment_methods": {{
    "singapore": ["list of methods mentioned"],
    "malaysia": ["list of methods mentioned"],
    "philippines": ["list of methods mentioned"],
    "global": ["list of global methods mentioned"]
  }},
  "markets_served": ["list of countries/regions"],
  "features": ["list of specific features mentioned"],
  "target_segment": ["SME", "enterprise", "startup", "developer", "platform"],
  "integrations": ["list of integrations mentioned"],
  "payout_speed": "exact text",
  "compliance": ["PCI DSS", "MAS", "BSP", "SOC2", etc — only if explicitly stated],
  "unique_selling_points": ["key differentiators mentioned"],
  "positioning": "one sentence positioning from their copy"
}}

Return ONLY the JSON object, no other text."""

    try:
        response = client.messages.create(
            model=OPENROUTER_HAIKU_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"    Claude extraction failed: {e}")
        return {}


def merge_facts(existing: dict, new_facts: dict) -> dict:
    """Merge new extracted facts into existing profile, extending lists."""
    for key, value in new_facts.items():
        if key not in existing:
            existing[key] = value
        elif isinstance(value, dict) and isinstance(existing[key], dict):
            for subkey, subval in value.items():
                if subkey not in existing[key]:
                    existing[key][subkey] = subval
                elif isinstance(subval, list) and isinstance(existing[key].get(subkey), list):
                    combined = existing[key][subkey] + [v for v in subval if v not in existing[key][subkey]]
                    existing[key][subkey] = combined
                elif existing[key][subkey] is None and subval is not None:
                    existing[key][subkey] = subval
        elif isinstance(value, list) and isinstance(existing.get(key), list):
            combined = existing[key] + [v for v in value if v not in existing[key]]
            existing[key] = combined
        elif existing[key] is None and value is not None:
            existing[key] = value
    return existing


def scrape_competitor(key: str, config: dict) -> dict:
    """Scrape all pages for a competitor and build a structured profile."""
    print(f"\n{'='*60}")
    print(f"Scraping: {config['name']}")
    print(f"{'='*60}")

    profile = {
        "id": key,
        "name": config["name"],
        "base_url": config["base_url"],
        "markets": config["markets"],
        "last_updated": date.today().isoformat(),
        "pricing": {},
        "payment_methods": {"singapore": [], "malaysia": [], "philippines": [], "global": []},
        "features": [],
        "target_segment": [],
        "integrations": [],
        "compliance": [],
        "unique_selling_points": [],
        "positioning": None,
        "payout_speed": None,
        "markets_served": config["markets"][:],
        "sources": []
    }

    successful_pages = 0

    for url in config["urls"]:
        print(f"  Fetching: {url}")
        text = fetch_page(url)

        if not text:
            time.sleep(0.5)
            continue

        print(f"  ✓ Got {len(text)} chars — extracting facts...")
        facts = extract_facts_with_claude(config["name"], url, text)

        if facts:
            profile = merge_facts(profile, facts)
            profile["sources"].append({"url": url, "scraped_at": date.today().isoformat()})
            successful_pages += 1

        time.sleep(1.5)  # Polite crawl delay

    print(f"\n  Done: {successful_pages}/{len(config['urls'])} pages successful")
    return profile


def save_profile(key: str, profile: dict):
    """Save competitor profile to JSON file."""
    path = COMPETITORS_DIR / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    print(f"  Saved to {path}")


def build_index():
    """Build a summary index of all competitors for quick reference."""
    index = {}
    for path in COMPETITORS_DIR.glob("*.json"):
        if path.stem == "_index":
            continue
        with open(path, "r") as f:
            profile = json.load(f)
        index[path.stem] = {
            "name": profile.get("name"),
            "markets": profile.get("markets_served", []),
            "positioning": profile.get("positioning"),
            "target_segment": profile.get("target_segment", []),
            "last_updated": profile.get("last_updated"),
        }

    with open(COMPETITORS_DIR / "_index.json", "w") as f:
        json.dump(index, f, indent=2)
    print(f"\nIndex built: {len(index)} competitors")


def main(targets: list[str] = None):
    """Run the scraper. Pass specific competitor keys to scrape only those."""
    competitors_to_scrape = targets if targets else list(COMPETITOR_URLS.keys())

    print(f"Starting competitor research — {len(competitors_to_scrape)} competitor(s)")
    print(f"Output directory: {COMPETITORS_DIR.absolute()}\n")

    for key in competitors_to_scrape:
        if key not in COMPETITOR_URLS:
            print(f"Unknown competitor: {key}. Available: {list(COMPETITOR_URLS.keys())}")
            continue

        config = COMPETITOR_URLS[key]
        profile = scrape_competitor(key, config)
        save_profile(key, profile)

    build_index()
    print("\n✓ Competitor research complete.")


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] if len(sys.argv) > 1 else None
    main(targets)
