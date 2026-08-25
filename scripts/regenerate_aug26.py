#!/usr/bin/env python3
"""One-off script: delete Aug 26–31 X and Threads drafts, regenerate with the [URL] fix.

Usage:
  python scripts/regenerate_aug26.py --dry-run     # preview, no writes
  python scripts/regenerate_aug26.py --confirm     # execute without prompt
  python scripts/regenerate_aug26.py               # execute with confirmation prompt
"""

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.x_database import create_x_post, delete_x_post, get_x_posts_scheduled_from
from src.threads_database import create_threads_post, delete_threads_post, get_threads_posts_scheduled_from
from src.threads_thought_leadership import generate_threads_story
from src.thought_leadership import HITPAY_TOPIC_POOL, generate_random_x_post
from src.repurposer import _cap_tweet_post_url

START_DATE = "2026-08-26"
END_DATE = "2026-09-01"       # exclusive upper bound
MARKET_CYCLE = ["SG", "MY", "PH"]


def dates_in_range() -> list[datetime]:
    """Return one datetime per day Aug 26–31 at 01:00 UTC (09:00 SGT)."""
    base = date.fromisoformat(START_DATE)
    end = date.fromisoformat(END_DATE)
    results = []
    d = base
    while d < end:
        results.append(datetime(d.year, d.month, d.day, 1, 0, 0, tzinfo=timezone.utc))
        d += timedelta(days=1)
    return results


def posts_in_range(posts: list[dict]) -> list[dict]:
    """Filter to posts whose scheduled_at falls within [START_DATE, END_DATE)."""
    return [
        p for p in posts
        if p.get("scheduled_at") and START_DATE <= str(p["scheduled_at"])[:10] < END_DATE
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate Aug 26–31 X and Threads posts.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no DB writes.")
    parser.add_argument("--confirm", action="store_true", help="Skip interactive confirmation prompt.")
    args = parser.parse_args()
    dry_run = args.dry_run

    # ── Step 1: Audit ─────────────────────────────────────────────────────────
    print(f"Auditing scheduled posts in range {START_DATE} → {END_DATE} (exclusive)...")
    all_x = get_x_posts_scheduled_from(START_DATE)
    all_thr = get_threads_posts_scheduled_from(START_DATE)

    x_to_delete = posts_in_range(all_x)
    thr_to_delete = posts_in_range(all_thr)

    print(f"  X posts to delete:       {len(x_to_delete)}")
    if x_to_delete:
        dates_x = sorted(str(p["scheduled_at"])[:10] for p in x_to_delete)
        print(f"  X date range:  {dates_x[0]} → {dates_x[-1]}")

    print(f"  Threads posts to delete: {len(thr_to_delete)}")
    if thr_to_delete:
        dates_t = sorted(str(p["scheduled_at"])[:10] for p in thr_to_delete)
        print(f"  Threads range: {dates_t[0]} → {dates_t[-1]}")

    dates = dates_in_range()
    schedule = [(dt, MARKET_CYCLE[i % len(MARKET_CYCLE)]) for i, dt in enumerate(dates)]

    print(f"\nProposed regeneration: {len(dates)} posts per platform.")
    for dt, mkt in schedule:
        print(f"  {dt.date()} ({dt.strftime('%a')}) — {mkt}")
    print(f"  Post time: 01:00 UTC (09:00 SGT)")

    if dry_run:
        print("\nDRY RUN — no changes made. Run without --dry-run to execute.")
        return

    if not args.confirm:
        resp = input(
            f"\nDelete {len(x_to_delete)} X + {len(thr_to_delete)} Threads posts "
            f"and regenerate {len(dates)} each? [y/N] "
        )
        if resp.strip().lower() != "y":
            print("Aborted.")
            return

    # ── Step 2: Delete ────────────────────────────────────────────────────────
    print(f"\nDeleting {len(x_to_delete)} X posts...")
    for p in x_to_delete:
        delete_x_post(p["id"])
    print(f"  Done.")

    print(f"Deleting {len(thr_to_delete)} Threads posts...")
    for p in thr_to_delete:
        delete_threads_post(p["id"])
    print(f"  Done.")

    # ── Step 3: Regenerate Threads ────────────────────────────────────────────
    print(f"\nRegenerating {len(dates)} Threads posts...")
    threads_errors: list[tuple] = []
    last_structure: str | None = None

    for i, (dt, market) in enumerate(schedule):
        topic = random.choice(HITPAY_TOPIC_POOL)
        label = f"[{i+1}/{len(dates)}] {dt.date()} ({dt.strftime('%a')}) {market}"
        print(f"  Threads {label} — {topic[:55]}…")
        try:
            data = generate_threads_story(
                market=market,
                topic_hint=topic,
                thread_size=3,
                brand="hitpay",
                _avoid_structure=last_structure,
            )
            link = data.get("link_url") or ""
            # Replace [URL] placeholder with the actual blog URL before saving
            content = "\n\n".join(p.replace("[URL]", link) for p in data["posts"])
            last_structure = data.get("structure")
            post_id = create_threads_post(
                content=content,
                market=market,
                scheduled_at=dt,
                brand="hitpay",
                source="regenerate_aug26",
            )
            print(f"    → id={post_id} structure={last_structure or 'n/a'} link={link or '(none)'}")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            threads_errors.append((i + 1, dt, market, str(exc)))

    # ── Step 4: Regenerate X ──────────────────────────────────────────────────
    print(f"\nRegenerating {len(dates)} X posts...")
    x_errors: list[tuple] = []

    for i, (dt, market) in enumerate(schedule):
        topic = random.choice(HITPAY_TOPIC_POOL)
        label = f"[{i+1}/{len(dates)}] {dt.date()} ({dt.strftime('%a')}) {market}"
        print(f"  X {label} — {topic[:55]}…")
        try:
            data = generate_random_x_post(
                market=market,
                topic_hint=topic,
                brand="hitpay",
                content_type="thought_leadership",
            )
            link = data.get("link_url") or ""
            tweets: list[str] = data.get("tweets") or []
            content = "\n\n".join(_cap_tweet_post_url(t.replace("[URL]", link)) for t in tweets)
            post_id = create_x_post(
                content=content,
                market=market,
                scheduled_at=dt,
                brand="hitpay",
                source="regenerate_aug26",
            )
            print(f"    → id={post_id}")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            x_errors.append((i + 1, dt, market, str(exc)))

    # ── Step 5: Summary ───────────────────────────────────────────────────────
    n = len(dates)
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Threads created: {n - len(threads_errors)} / {n}  ({len(threads_errors)} errors)")
    print(f"X posts created: {n - len(x_errors)} / {n}  ({len(x_errors)} errors)")
    print(f"Markets:         {' → '.join(MARKET_CYCLE)} (cycling)")
    print(f"Dates:           {dates[0].date()} → {dates[-1].date()}")
    if threads_errors:
        print("\nThreads errors:")
        for idx, dt, mkt, err in threads_errors:
            print(f"  [{idx}] {dt.date()} {mkt}: {err}")
    if x_errors:
        print("\nX errors:")
        for idx, dt, mkt, err in x_errors:
            print(f"  [{idx}] {dt.date()} {mkt}: {err}")


if __name__ == "__main__":
    main()
