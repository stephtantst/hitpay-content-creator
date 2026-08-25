#!/usr/bin/env python3
"""
External link scraper for HitPay blog post generator.
Scrapes blog/resource listing pages from competitor and partner sites,
classifies articles by topic and market via Claude Haiku, and stores
results in external_links_db.json for use during article generation.
"""

import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import OPENROUTER_HAIKU_MODEL
from src.llm_client import OpenRouterClient

DB_PATH = Path(__file__).parent.parent / "external_links_db.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Source definitions ─────────────────────────────────────────────────────────
# Each source: listing_pages to crawl, URL path patterns that identify article
# links (vs category/tag/pagination links), market scope, competitor flag.

BLOG_SOURCES: dict[str, dict] = {
    "stripe": {
        "name": "Stripe",
        "is_competitor": True,
        "markets": ["SG", "MY", "PH"],
        "listing_pages": [
            "https://stripe.com/blog",
            "https://stripe.com/blog/industry",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/docs/", "/changelog/", "#", "?"],
    },
    "airwallex": {
        "name": "Airwallex",
        "is_competitor": True,
        "markets": ["SG", "MY"],
        "listing_pages": [
            "https://www.airwallex.com/blog",
            "https://www.airwallex.com/blog/page/2",
            "https://www.airwallex.com/blog/page/3",
            "https://www.airwallex.com/sg/blog",
            "https://www.airwallex.com/my/blog",
            "https://www.airwallex.com/my/blog/page/2",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/author/", "/blog/page/", "#"],
    },
    "xendit": {
        "name": "Xendit",
        "is_competitor": True,
        "markets": ["PH", "MY"],
        "listing_pages": [
            "https://www.xendit.co/blog/",
            "https://www.xendit.co/en-ph/blog/",
            "https://www.xendit.co/en-id/blog/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "paymongo": {
        "name": "PayMongo",
        "is_competitor": True,
        "markets": ["PH"],
        "listing_pages": [
            "https://www.paymongo.com/blog",
            "https://www.paymongo.com/blog?page=2",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "adyen": {
        "name": "Adyen",
        "is_competitor": True,
        "markets": ["SG", "MY", "PH"],
        "listing_pages": [
            "https://www.adyen.com/knowledge-hub",
            "https://www.adyen.com/knowledge-hub?page=2",
        ],
        "link_path_patterns": ["/knowledge-hub/"],
        "exclude_path_patterns": ["/knowledge-hub/category/", "/knowledge-hub/tag/", "#"],
    },
    "shopify": {
        "name": "Shopify",
        "is_competitor": False,
        "markets": ["SG", "MY", "PH"],
        "listing_pages": [
            "https://www.shopify.com/blog/topics/payments",
            "https://www.shopify.com/blog/topics/ecommerce",
            "https://www.shopify.com/blog/topics/retail",
            "https://www.shopify.com/blog/topics/marketing",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/topics/", "#", "?"],
    },
    "woocommerce": {
        "name": "WooCommerce",
        "is_competitor": False,
        "markets": ["SG", "MY", "PH"],
        "listing_pages": [
            "https://woocommerce.com/blog/",
            "https://woocommerce.com/blog/page/2/",
            "https://woocommerce.com/blog/page/3/",
            "https://woocommerce.com/blog/page/4/",
            "https://woocommerce.com/posts/",
        ],
        "link_path_patterns": ["/blog/", "/posts/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "xero": {
        "name": "Xero",
        "is_competitor": False,
        "markets": ["SG", "MY"],
        "listing_pages": [
            "https://www.xero.com/blog/",
            "https://www.xero.com/blog/page/2/",
            "https://www.xero.com/sg/resources/",
            "https://www.xero.com/my/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#", "?page="],
    },
    "fiuu": {
        "name": "Fiuu",
        "is_competitor": True,
        "markets": ["MY", "SG"],
        "listing_pages": [
            "https://fiuu.com/blog/",
            "https://fiuu.com/blog/page/2/",
            "https://fiuu.com/blog/page/3/",
            "https://fiuu.com/blog/page/4/",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "ipay88": {
        "name": "iPay88",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://www.ipay88.com/blog/",
            "https://www.ipay88.com/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "#"],
    },
    "storehub": {
        "name": "StoreHub",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://www.storehub.com/blog/",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "paymaya": {
        "name": "Maya (PayMaya)",
        "is_competitor": True,
        "markets": ["PH"],
        "listing_pages": [
            "https://www.maya.ph/blog",
            "https://www.maya.ph/for-business/resources",
            "https://www.maya.ph/for-business/blog",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "billplz": {
        "name": "Billplz",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://www.billplz.com/blog",
            "https://www.billplz.com/blog?page=2",
            "https://www.billplz.com/blog?page=3",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "senangpay": {
        "name": "SenangPay",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://senangpay.com/blog/",
            "https://senangpay.com/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "curlec": {
        "name": "Curlec",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://curlec.com/blog/",
            "https://curlec.com/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "revolut": {
        "name": "Revolut",
        "is_competitor": True,
        "markets": ["SG"],
        "listing_pages": [
            "https://www.revolut.com/blog/",
            "https://www.revolut.com/en-SG/blog/",
        ],
        "link_path_patterns": ["/blog/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "/blog/author/", "#"],
    },
    "qashier": {
        "name": "Qashier",
        "is_competitor": True,
        "markets": ["SG"],
        "listing_pages": [
            "https://qashier.com/blog/",
            "https://qashier.com/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "2c2p": {
        "name": "2C2P",
        "is_competitor": True,
        "markets": ["SG", "MY", "PH"],
        "listing_pages": [
            "https://www.2c2p.com/insights",
            "https://www.2c2p.com/resources",
        ],
        "link_path_patterns": ["/insights/", "/resources/"],
        "exclude_path_patterns": ["/insights/category/", "/insights/tag/", "#"],
    },
    "dragonpay": {
        "name": "DragonPay",
        "is_competitor": True,
        "markets": ["PH"],
        "listing_pages": [
            "https://www.dragonpay.ph/blog/",
            "https://www.dragonpay.ph/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "paynamics": {
        "name": "Paynamics",
        "is_competitor": True,
        "markets": ["PH"],
        "listing_pages": [
            "https://paynamics.com/blog/",
            "https://paynamics.com/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
    "eghl": {
        "name": "eGHL",
        "is_competitor": True,
        "markets": ["MY"],
        "listing_pages": [
            "https://www.eghl.my/blog/",
            "https://www.eghl.my/resources/",
        ],
        "link_path_patterns": ["/blog/", "/resources/"],
        "exclude_path_patterns": ["/blog/category/", "/blog/tag/", "/blog/page/", "#"],
    },
}

# ── Utilities ──────────────────────────────────────────────────────────────────

def _fetch_html(url: str, timeout: int = 15) -> str | None:
    """Fetch a page and return raw HTML."""
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"  ✗ {url} → HTTP {resp.status_code}")
                return None
            return resp.text
    except Exception as e:
        print(f"  ✗ {url} → {e}")
        return None


def _clean_scraped_title(raw: str) -> str:
    """First-pass noise removal from raw blog-card link text.

    Only strips the most reliable patterns (CTA text and dates).
    Source/category prefixes are left for Claude to handle during classification,
    since they vary too much across sites for safe regex stripping.
    """
    # Drop everything from "Continue Reading" / "Read More" onwards
    raw = re.sub(r'\s*(continue reading|read more|read article|learn more)\b.*$', '', raw, flags=re.IGNORECASE)
    # Drop date patterns: "Apr 17, 2026" / "April 07, 2026" / "2026-04-17"
    raw = re.sub(
        r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        r'\.?\s+\d{1,2},?\s+\d{4}\b',
        '', raw, flags=re.IGNORECASE,
    )
    raw = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', raw)
    return ' '.join(raw.split()).strip()


def _extract_article_links(html: str, base_url: str, config: dict) -> list[dict]:
    """Extract article links from a listing page that match the source's path patterns."""
    soup = BeautifulSoup(html, "lxml")
    domain = urlparse(base_url).netloc
    include_patterns = config["link_path_patterns"]
    exclude_patterns = config.get("exclude_path_patterns", [])

    seen: set[str] = set()
    articles: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Resolve relative URLs
        if href.startswith("/"):
            href = urljoin(base_url, href)
        elif not href.startswith("http"):
            continue

        # Must be on the same domain
        if urlparse(href).netloc != domain:
            continue

        path = urlparse(href).path

        # Must match at least one include pattern
        if not any(pat in path for pat in include_patterns):
            continue

        # Must not match any exclude pattern
        if any(pat in href for pat in exclude_patterns):
            continue

        # Must have a meaningful slug (at least 10 chars after the pattern)
        base_path = next((path.split(pat)[-1] for pat in include_patterns if pat in path), "")
        if len(base_path.strip("/")) < 5:
            continue

        # Deduplicate
        href_clean = href.rstrip("/")
        if href_clean in seen:
            continue
        seen.add(href_clean)

        # Extract title — prefer heading element inside the <a>, then clean full text, then slug
        title = ""

        # 1. Heading inside the link card (most reliable — avoids dates/categories)
        inner_heading = a.find(["h1", "h2", "h3", "h4"])
        if inner_heading:
            title = inner_heading.get_text(strip=True)

        # 2. Clean the full link text (strip noise like dates, "Continue Reading", category labels)
        if not title or len(title) < 5:
            raw = a.get_text(separator=" ", strip=True)
            title = _clean_scraped_title(raw)

        # 3. Try heading in parent elements
        if not title or len(title) < 5:
            parent = a.parent
            for _ in range(3):
                if parent is None:
                    break
                heading = parent.find(["h2", "h3", "h4"])
                if heading:
                    title = _clean_scraped_title(heading.get_text(strip=True))
                    break
                parent = parent.parent

        # 4. Derive from URL slug
        if not title or len(title) < 5:
            title = path.rstrip("/").split("/")[-1].replace("-", " ").title()

        if title and len(title) >= 5:
            articles.append({"url": href_clean, "title": title[:200]})

    return articles


def _classify_articles_with_claude(
    source_name: str,
    source_markets: list[str],
    raw_articles: list[dict],
) -> list[dict]:
    """Use Claude Haiku to classify a batch of articles by topics and markets."""
    if not raw_articles:
        return []

    client = OpenRouterClient()

    # Process in batches of 30 to stay within token limits
    classified: list[dict] = []
    batch_size = 30

    for i in range(0, len(raw_articles), batch_size):
        batch = raw_articles[i : i + batch_size]
        article_list = "\n".join(
            f'{j+1}. URL: {a["url"]}\n   Title: {a["title"]}'
            for j, a in enumerate(batch)
        )

        prompt = f"""You are classifying blog articles from {source_name} (a payment/fintech company serving markets: {', '.join(source_markets)}).

For each article below, do three things:
1. title: extract the clean article headline — strip any leading source/category labels (e.g. "Fiuu — Business", "PayMongo — In the Spotlight"), leftover dates, and trailing noise. Return only the actual article title.
2. topics: 3-6 lowercase keyword phrases describing what the article is about (e.g. "payment gateway", "online payments", "pos system", "woocommerce", "shopify", "invoicing", "qr code", "bnpl")
3. markets: which of SG/MY/PH this article is most relevant to. If clearly one market, list just that one. If general, list all that apply from: {source_markets}

Articles to classify:
{article_list}

Return a JSON array with one object per article, in order:
[
  {{"index": 1, "title": "Clean Article Title Here", "topics": ["topic1", "topic2"], "markets": ["SG"]}},
  ...
]

Only return the JSON array. Omit articles that are clearly not about payments, SME business, or ecommerce (e.g. company news, job listings, press releases)."""

        try:
            resp = client.messages.create(
                model=OPENROUTER_HAIKU_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            results = json.loads(raw)

            for item in results:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(batch):
                    article = batch[idx].copy()
                    # Use Claude's cleaned title if provided and non-empty
                    clean_title = (item.get("title") or "").strip()
                    if clean_title and len(clean_title) >= 5:
                        article["title"] = clean_title
                    article["topics"] = item.get("topics", [])
                    article["markets"] = item.get("markets", source_markets)
                    classified.append(article)

        except Exception as e:
            print(f"    Claude classification failed for batch: {e}")
            # Fallback: include with no topics
            for a in batch:
                classified.append({**a, "topics": [], "markets": source_markets})

        time.sleep(0.5)

    return classified


# ── Main scraper ───────────────────────────────────────────────────────────────

def scrape_source(key: str, config: dict, verbose: bool = True) -> list[dict]:
    """Scrape all listing pages for a source and return classified articles."""
    if verbose:
        print(f"\n{'='*60}")
        print(f"Scraping: {config['name']}")
        print(f"{'='*60}")

    all_raw: list[dict] = []

    for listing_url in config["listing_pages"]:
        if verbose:
            print(f"  Fetching listing: {listing_url}")
        html = _fetch_html(listing_url)
        if not html:
            time.sleep(1)
            continue

        links = _extract_article_links(html, listing_url, config)
        if verbose:
            print(f"  Found {len(links)} article links")
        all_raw.extend(links)
        time.sleep(1.5)

    # Deduplicate across listing pages
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in all_raw:
        if a["url"] not in seen:
            seen.add(a["url"])
            deduped.append(a)

    if not deduped:
        if verbose:
            print(f"  No articles found for {config['name']} (site may be JS-rendered)")
        return []

    if verbose:
        print(f"  Classifying {len(deduped)} unique articles with Claude Haiku...")

    classified = _classify_articles_with_claude(config["name"], config["markets"], deduped)

    # Attach source metadata
    today = date.today().isoformat()
    for article in classified:
        article["source_key"] = key
        article["source_name"] = config["name"]
        article["is_competitor"] = config["is_competitor"]
        article["scraped_at"] = today

    if verbose:
        print(f"  Done: {len(classified)} articles classified")

    return classified


def load_db() -> dict:
    """Load the external links DB, or return empty structure."""
    if DB_PATH.exists():
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": None, "sources_scraped": [], "articles": []}


def save_db(db: dict):
    """Persist the external links DB."""
    db["last_updated"] = date.today().isoformat()
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def run_scraper(targets: list[str] | None = None, verbose: bool = True) -> dict:
    """Scrape sources and update the DB. Returns summary stats."""
    sources_to_scrape = targets if targets else list(BLOG_SOURCES.keys())
    db = load_db()

    # Remove old entries for sources being re-scraped
    db["articles"] = [
        a for a in db.get("articles", [])
        if a.get("source_key") not in sources_to_scrape
    ]

    stats = {"scraped": 0, "articles_added": 0, "failed": []}

    for key in sources_to_scrape:
        if key not in BLOG_SOURCES:
            print(f"Unknown source: {key}. Available: {list(BLOG_SOURCES.keys())}")
            continue

        articles = scrape_source(key, BLOG_SOURCES[key], verbose=verbose)

        if articles:
            db["articles"].extend(articles)
            stats["articles_added"] += len(articles)
            if key not in db.get("sources_scraped", []):
                db.setdefault("sources_scraped", []).append(key)
            stats["scraped"] += 1
        else:
            stats["failed"].append(key)

    save_db(db)
    return stats


# ── Search ─────────────────────────────────────────────────────────────────────

def search_articles(
    keyword: str,
    markets: list[str] | None = None,
    is_comparison: bool = False,
    limit: int = 10,
    max_per_source: int = 2,
) -> list[dict]:
    """Search the DB for articles relevant to the keyword.

    Scores: topic token match + title token match. Competitor articles are only
    returned when is_comparison=True. max_per_source prevents any single site
    from dominating the results.
    """
    db = load_db()
    articles = db.get("articles", [])
    if not articles:
        return []

    kw_tokens = set(t.lower() for t in re.split(r"\W+", keyword) if len(t) > 2)
    scored: list[tuple[int, dict]] = []

    for article in articles:
        # Skip competitors unless comparison article
        if article.get("is_competitor") and not is_comparison:
            continue

        # Market filter
        if markets:
            article_markets = set(article.get("markets", []))
            if not article_markets.intersection(markets):
                continue

        # Score: title token matches
        title_tokens = set(t.lower() for t in re.split(r"\W+", article.get("title", "")) if len(t) > 2)
        title_score = len(kw_tokens & title_tokens)

        # Score: topic matches
        topic_tokens = set(
            t.lower()
            for topic in article.get("topics", [])
            for t in re.split(r"\W+", topic)
            if len(t) > 2
        )
        topic_score = len(kw_tokens & topic_tokens)

        total = title_score * 2 + topic_score
        if total > 0:
            scored.append((total, article))

    scored.sort(key=lambda x: -x[0])

    # Apply per-source cap to prevent one site from dominating
    results: list[dict] = []
    source_counts: dict[str, int] = {}
    for article in (a for _, a in scored):
        src = article.get("source_key", "")
        if source_counts.get(src, 0) >= max_per_source:
            continue
        source_counts[src] = source_counts.get(src, 0) + 1
        results.append(article)
        if len(results) >= limit:
            break

    return results
