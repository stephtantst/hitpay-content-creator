#!/usr/bin/env python3
"""One-off script: delete Aug 26–31 X and Threads drafts, regenerate with the [URL] fix.

Usage:
  python scripts/regenerate_aug26.py --preview    # generate & print to terminal, no DB writes
  python scripts/regenerate_aug26.py --dry-run    # show schedule only, no generation, no DB writes
  python scripts/regenerate_aug26.py --confirm    # delete old + save new without prompt
  python scripts/regenerate_aug26.py              # delete old + save new with confirmation prompt
"""

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.threads_thought_leadership import generate_threads_story
from src.thought_leadership import (
    HITPAY_TOPIC_POOL, CONTENT_TYPE_BY_WEEKDAY, CONTENT_TYPE_CONFIGS,
    _BLOG_REPURPOSE_CONTENT_TYPES,
    generate_random_x_post, generate_thought_leadership_thread,
)
from src.repurposer import _cap_tweet_post_url
from src.fact_checker import fact_check_social_post

START_DATE = "2026-08-26"
END_DATE = "2026-09-01"       # exclusive upper bound
MARKET_CYCLE = ["SG", "MY", "PH"]

SEP = "\n" + "─" * 60 + "\n"


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


def print_divider(label: str = ""):
    print(SEP + (f"  {label}\n" if label else ""))


def print_fact_check(result: dict) -> None:
    """Print a compact fact-check result inline."""
    verdict = result.get("verdict", "unknown")
    summary = result.get("summary", "")
    issues = result.get("issues", [])
    icon = {"pass": "PASS", "flag": "FLAG", "fail": "FAIL"}.get(verdict, verdict.upper())
    print(f"\n[FACT CHECK: {icon}] {summary}")
    for iss in issues:
        sev = iss.get("severity", "").upper()
        print(f"  [{sev}] \"{iss.get('claim', '')}\"")
        print(f"         Issue: {iss.get('issue', '')}")
        print(f"         Fix:   {iss.get('fix', '')}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate Aug 26–31 X and Threads posts.")
    parser.add_argument("--preview", action="store_true", help="Generate and print content — no DB reads or writes.")
    parser.add_argument("--dry-run", action="store_true", help="Show schedule only — no generation, no DB writes.")
    parser.add_argument("--confirm", action="store_true", help="Skip interactive confirmation prompt.")
    args = parser.parse_args()

    dates = dates_in_range()
    schedule = [(dt, MARKET_CYCLE[i % len(MARKET_CYCLE)]) for i, dt in enumerate(dates)]

    # ── Preview mode: generate & print, no DB ─────────────────────────────────
    if args.preview:
        print(f"\nPREVIEW MODE — generating {len(dates)} days of content (no DB writes)\n")
        last_structure: str | None = None

        for i, (dt, market) in enumerate(schedule):
            topic = random.choice(HITPAY_TOPIC_POOL)
            label = f"{dt.date()} ({dt.strftime('%A')}) — {market}"

            print_divider(label)
            print(f"TOPIC: {topic}\n")

            # Threads
            print("THREADS\n")
            try:
                thr = generate_threads_story(
                    market=market,
                    topic_hint=topic,
                    thread_size=3,
                    brand="hitpay",
                    _avoid_structure=last_structure,
                )
                link = thr.get("link_url") or ""
                last_structure = thr.get("structure")
                resolved_posts = [p.replace("[URL]", link) for p in thr["posts"]]
                for j, post in enumerate(resolved_posts, 1):
                    print(f"Post {j}:\n{post}\n")
                try:
                    fc = fact_check_social_post("\n\n".join(resolved_posts), market)
                    print_fact_check(fc)
                except Exception as fc_err:
                    print(f"[FACT CHECK ERROR: {fc_err}]\n")
            except Exception as exc:
                print(f"ERROR: {exc}")

            print("\nX\n")
            content_type = CONTENT_TYPE_BY_WEEKDAY.get(dt.weekday(), "thought_leadership")
            print(f"[content_type: {content_type}]\n")
            x = None
            try:
                x = generate_random_x_post(
                    market=market,
                    topic_hint=topic,
                    brand="hitpay",
                    content_type=content_type,
                )
            except Exception as exc:
                if content_type in _BLOG_REPURPOSE_CONTENT_TYPES:
                    # DB unavailable locally — generate product style without blog source
                    print(f"[No DB available — generating {content_type} style without blog source]\n")
                    try:
                        cfg = CONTENT_TYPE_CONFIGS[content_type]
                        x = generate_thought_leadership_thread(
                            market=market, topic_hint=topic,
                            thread_size=cfg["thread_size"], style=cfg["style"],
                            content_type=content_type, brand="hitpay",
                        )
                        x["content_type"] = content_type
                    except Exception as exc2:
                        print(f"ERROR: {exc2}")
                else:
                    print(f"ERROR: {exc}")
            if x:
                x_link = x.get("link_url") or ""
                tweets = x.get("tweets") or []
                resolved_tweets = [_cap_tweet_post_url(t.replace("[URL]", x_link)) for t in tweets]
                for j, tweet in enumerate(resolved_tweets, 1):
                    print(f"Tweet {j}:\n{tweet}\n")
                try:
                    fc = fact_check_social_post("\n\n".join(resolved_tweets), market)
                    print_fact_check(fc)
                except Exception as fc_err:
                    print(f"[FACT CHECK ERROR: {fc_err}]\n")

        print("\n" + "=" * 60)
        print("PREVIEW COMPLETE — run without --preview to save to DB.")
        print("=" * 60)
        return

    # ── Dry-run mode: show schedule only ──────────────────────────────────────
    if args.dry_run:
        print(f"\nDRY RUN — proposed schedule ({len(dates)} days):")
        for dt, mkt in schedule:
            print(f"  {dt.date()} ({dt.strftime('%A')}) — {mkt}")
        print("\nNo changes made.")
        return

    # ── Full run: delete old posts, save new ones ──────────────────────────────
    from src.x_database import create_x_post, delete_x_post, get_x_posts_scheduled_from
    from src.threads_database import create_threads_post, delete_threads_post, get_threads_posts_scheduled_from

    print(f"Auditing scheduled posts in range {START_DATE} → {END_DATE} (exclusive)...")
    x_to_delete = posts_in_range(get_x_posts_scheduled_from(START_DATE))
    thr_to_delete = posts_in_range(get_threads_posts_scheduled_from(START_DATE))

    print(f"  X posts to delete:       {len(x_to_delete)}")
    print(f"  Threads posts to delete: {len(thr_to_delete)}")

    if not args.confirm:
        resp = input(
            f"\nDelete {len(x_to_delete)} X + {len(thr_to_delete)} Threads posts "
            f"and regenerate {len(dates)} each? [y/N] "
        )
        if resp.strip().lower() != "y":
            print("Aborted.")
            return

    print(f"\nDeleting {len(x_to_delete)} X posts...")
    for p in x_to_delete:
        delete_x_post(p["id"])
    print(f"Deleting {len(thr_to_delete)} Threads posts...")
    for p in thr_to_delete:
        delete_threads_post(p["id"])

    threads_errors: list[tuple] = []
    x_errors: list[tuple] = []
    last_structure = None

    print(f"\nRegenerating {len(dates)} Threads posts...")
    for i, (dt, market) in enumerate(schedule):
        topic = random.choice(HITPAY_TOPIC_POOL)
        print(f"  [{i+1}/{len(dates)}] {dt.date()} {market} — {topic[:55]}…")
        try:
            data = generate_threads_story(
                market=market, topic_hint=topic, thread_size=3,
                brand="hitpay", _avoid_structure=last_structure,
            )
            link = data.get("link_url") or ""
            resolved = [p.replace("[URL]", link) for p in data["posts"]]
            content = "\n\n".join(resolved)
            last_structure = data.get("structure")
            # Fact check before saving
            try:
                fc = fact_check_social_post(content, market)
                fc_verdict = fc.get("verdict", "pass")
                if fc_verdict == "fail":
                    issues_str = "; ".join(i["issue"] for i in fc.get("issues", []))
                    print(f"    FACT CHECK FAIL — skipped. Issues: {issues_str}")
                    threads_errors.append((i + 1, dt, market, f"fact_check_fail: {issues_str}"))
                    continue
                elif fc_verdict == "flag":
                    issues_str = "; ".join(i["issue"] for i in fc.get("issues", []))
                    print(f"    FACT CHECK FLAG — saving with warning. {issues_str}")
            except Exception as fc_err:
                print(f"    FACT CHECK ERROR (saving anyway): {fc_err}")
            post_id = create_threads_post(
                content=content, market=market, scheduled_at=dt,
                brand="hitpay", source="regenerate_aug26",
            )
            print(f"    → id={post_id} structure={last_structure or 'n/a'} link={link or '(none)'}")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            threads_errors.append((i + 1, dt, market, str(exc)))

    print(f"\nRegenerating {len(dates)} X posts...")
    for i, (dt, market) in enumerate(schedule):
        topic = random.choice(HITPAY_TOPIC_POOL)
        content_type = CONTENT_TYPE_BY_WEEKDAY.get(dt.weekday(), "thought_leadership")
        print(f"  [{i+1}/{len(dates)}] {dt.date()} {market} [{content_type}] — {topic[:55]}…")
        try:
            data = generate_random_x_post(
                market=market, topic_hint=topic, brand="hitpay",
                content_type=content_type,
            )
            link = data.get("link_url") or ""
            tweets = data.get("tweets") or []
            resolved = [_cap_tweet_post_url(t.replace("[URL]", link)) for t in tweets]
            content = "\n\n".join(resolved)
            # Fact check before saving
            try:
                fc = fact_check_social_post(content, market)
                fc_verdict = fc.get("verdict", "pass")
                if fc_verdict == "fail":
                    issues_str = "; ".join(iss["issue"] for iss in fc.get("issues", []))
                    print(f"    FACT CHECK FAIL — skipped. Issues: {issues_str}")
                    x_errors.append((i + 1, dt, market, f"fact_check_fail: {issues_str}"))
                    continue
                elif fc_verdict == "flag":
                    issues_str = "; ".join(iss["issue"] for iss in fc.get("issues", []))
                    print(f"    FACT CHECK FLAG — saving with warning. {issues_str}")
            except Exception as fc_err:
                print(f"    FACT CHECK ERROR (saving anyway): {fc_err}")
            post_id = create_x_post(
                content=content, market=market, scheduled_at=dt,
                brand="hitpay", source="regenerate_aug26",
            )
            print(f"    → id={post_id}")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            x_errors.append((i + 1, dt, market, str(exc)))

    n = len(dates)
    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    print(f"Threads created: {n - len(threads_errors)} / {n}")
    print(f"X posts created: {n - len(x_errors)} / {n}")
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
