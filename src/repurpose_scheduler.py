"""
Reusable repurpose-and-schedule logic.

Generates X, Threads, LinkedIn, and Reddit drafts from a published blog post and
assigns them all to the same next available weekday slot (09:00 SGT = 01:00 UTC).

Usage:
    from src.repurpose_scheduler import repurpose_and_schedule
    result = repurpose_and_schedule(post, user_email="steph@hit-pay.com")
    # {"ok": True/False, "date": datetime, "x_id": int, "threads_id": int, "linkedin_id": int, "reddit_id": int, "errors": dict}
"""
import random
import re
from datetime import datetime, timezone, timedelta

POST_HOUR_UTC = 1   # 09:00 SGT = 01:00 UTC
THREAD_SEP = "\n\n---\n\n"
BLOG_BASE = "https://hitpayapp.com/blog"

# Minimum days required between two scheduled posts whose blog titles share the same theme.
MIN_THEME_GAP_DAYS = 4

_THEME_STOPWORDS = {
    "a", "an", "the", "for", "in", "on", "to", "of", "and", "or", "how",
    "what", "why", "your", "you", "with", "is", "are", "best", "top",
    "guide", "guides", "complete", "practical", "actually", "need", "needs",
    "accept", "accepting", "via", "vs", "2026", "2025", "sme", "smb",
    "smbs", "business", "businesses", "solution", "solutions", "system",
    "systems", "link", "links", "online", "software", "methods", "method",
    "payments", "payment",
    # markets — different market, same theme should still count as similar
    "singapore", "philippines", "philippine", "malaysia", "malaysian",
    "sea", "southeast", "asia", "sg", "ph", "my",
}


def _extract_theme_keywords(title: str) -> set:
    """Reduce a blog title to its core topic keywords (strip filler words, markets, years)."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _THEME_STOPWORDS and len(w) > 2}


def _themes_similar(a: set, b: set) -> bool:
    """True if two keyword sets overlap enough to be considered the same theme."""
    if not a or not b:
        return False
    overlap = a & b
    if not overlap:
        return False
    return len(overlap) / min(len(a), len(b)) >= 0.5


def _get_scheduled_theme_dates() -> list:
    """Return (theme_keywords, scheduled_at) for every post with an existing social draft."""
    from src.database import get_connection

    conn = get_connection()
    rows = conn.run(
        """
        SELECT p.title, s.scheduled_at FROM (
            SELECT source_blog_post_id, scheduled_at FROM x_posts       WHERE scheduled_at IS NOT NULL AND source_blog_post_id IS NOT NULL
            UNION
            SELECT source_blog_post_id, scheduled_at FROM threads_posts  WHERE scheduled_at IS NOT NULL AND source_blog_post_id IS NOT NULL
            UNION
            SELECT source_blog_post_id, scheduled_at FROM linkedin_posts WHERE scheduled_at IS NOT NULL AND source_blog_post_id IS NOT NULL
            UNION
            SELECT source_blog_post_id, scheduled_at FROM reddit_posts    WHERE scheduled_at IS NOT NULL AND source_blog_post_id IS NOT NULL
        ) s
        JOIN posts p ON p.id = s.source_blog_post_id
        """
    )
    return [(_extract_theme_keywords(title), scheduled_at) for title, scheduled_at in rows if title]


def get_next_schedule_date(theme_keywords: set = None) -> datetime:
    """Return the next weekday after the latest scheduled draft across all platforms.

    If theme_keywords is given, the date is pushed forward (skipping weekends)
    until no similarly-themed post is already scheduled within MIN_THEME_GAP_DAYS.
    """
    from src.database import get_connection

    conn = get_connection()
    rows = conn.run(
        "SELECT GREATEST("
        "  (SELECT MAX(scheduled_at) FROM x_posts       WHERE scheduled_at IS NOT NULL),"
        "  (SELECT MAX(scheduled_at) FROM threads_posts  WHERE scheduled_at IS NOT NULL),"
        "  (SELECT MAX(scheduled_at) FROM linkedin_posts WHERE scheduled_at IS NOT NULL),"
        "  (SELECT MAX(scheduled_at) FROM reddit_posts    WHERE scheduled_at IS NOT NULL)"
        ")"
    )
    max_date = rows[0][0] if rows and rows[0][0] else None

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if max_date:
        last_day = max_date.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        base = last_day + timedelta(days=1)
        start = max(base, today + timedelta(days=1))
    else:
        start = today + timedelta(days=1)

    while start.weekday() >= 5:
        start += timedelta(days=1)

    if theme_keywords:
        existing = _get_scheduled_theme_dates()
        while any(
            _themes_similar(theme_keywords, other_kw)
            and abs((start.date() - other_dt.astimezone(timezone.utc).date()).days) < MIN_THEME_GAP_DAYS
            for other_kw, other_dt in existing
        ):
            start += timedelta(days=1)
            while start.weekday() >= 5:
                start += timedelta(days=1)

    return start.replace(hour=POST_HOUR_UTC, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _ensure_url(content: str, blog_url: str) -> str:
    """Append blog_url to content if not already present."""
    if blog_url and blog_url not in content:
        return content.rstrip() + f"\n\n{blog_url}"
    return content


def repurpose_and_schedule(post: dict, user_email: str, override_date: datetime = None) -> dict:
    """
    Generate X, Threads, LinkedIn, and Reddit drafts for *post* and set their
    scheduled_at to the same next available weekday (or override_date if given).

    Only published posts should be passed in; raises ValueError otherwise.

    Returns a dict with keys: ok, date, x_id, threads_id, linkedin_id, reddit_id, errors.
    """
    if post.get("status") not in ("published",):
        raise ValueError(
            f"Post #{post.get('id')} has status '{post.get('status')}' — only 'published' posts can be repurposed."
        )

    from src.x_database import create_x_post
    from src.threads_database import create_threads_post
    from src.linkedin_database import create_linkedin_post
    from src.repurposer import repurpose_post_as_thread, _cap_tweet_post_url
    from src.threads_thought_leadership import generate_threads_story
    from src.linkedin_generator import generate_linkedin_post as _gen_li
    from src.reddit_database import create_reddit_post
    from src.reddit_generator import generate_reddit_post as _gen_reddit

    post_id    = post["id"]
    market     = (post.get("country") or "SG").upper()
    brand      = post.get("brand") or "hitpay"
    topic_hint = (post.get("title") or "")[:150]
    slug       = (post.get("slug") or "").strip()
    blog_url   = f"{BLOG_BASE}/{slug}" if slug else ""

    if override_date is not None:
        slot_date = override_date
    else:
        theme_keywords = _extract_theme_keywords(post.get("title") or "")
        slot_date = get_next_schedule_date(theme_keywords)
    errors: dict = {}

    # --- X ---
    x_id = None
    try:
        thread_size = random.choice([1, 3, 5])
        r = repurpose_post_as_thread(post, thread_size)
        link = r.get("link_url") or blog_url
        tweets = [
            _cap_tweet_post_url(t.replace("[URL]", link))
            for t in (r.get("tweets") or [])
        ]
        x_id = create_x_post(
            content=THREAD_SEP.join(tweets),
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
            scheduled_at=slot_date,
        )
    except Exception as exc:
        errors["x"] = str(exc)

    # --- Threads ---
    threads_id = None
    try:
        r = generate_threads_story(market=market, topic_hint=topic_hint, thread_size=3, brand=brand)
        ps = [p for p in (r.get("posts") or []) if p and p.strip()]
        if not ps:
            raise ValueError("generate_threads_story returned empty posts")
        content = THREAD_SEP.join(ps) if len(ps) > 1 else ps[0]
        content = _ensure_url(content, blog_url)
        threads_id = create_threads_post(
            content=content,
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
            scheduled_at=slot_date,
        )
    except Exception as exc:
        errors["threads"] = str(exc)

    # --- LinkedIn ---
    linkedin_id = None
    try:
        r = _gen_li(market=market, topic_hint=topic_hint, brand=brand)
        content = r.get("content", "")
        content = _ensure_url(content, blog_url)
        linkedin_id = create_linkedin_post(
            content=content,
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
            scheduled_at=slot_date,
        )
    except Exception as exc:
        errors["linkedin"] = str(exc)

    # --- Reddit ---
    reddit_id = None
    try:
        r = _gen_reddit(post, market=market, brand=brand)
        body = (r.get("content") or "").strip()
        if not body:
            raise ValueError("generate_reddit_post returned empty OP body")
        reddit_id = create_reddit_post(
            content=body,
            title=r.get("title"),
            subreddit=r.get("subreddit"),
            reply_comment=r.get("reply_comment"),
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
            scheduled_at=slot_date,
        )
    except Exception as exc:
        errors["reddit"] = str(exc)

    return {
        "ok":          not errors,
        "date":        slot_date,
        "x_id":        x_id,
        "threads_id":  threads_id,
        "linkedin_id": linkedin_id,
        "reddit_id":   reddit_id,
        "errors":      errors,
    }
