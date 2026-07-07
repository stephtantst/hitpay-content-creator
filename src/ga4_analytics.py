"""GA4 Data API integration for per-post blog analytics.

Same auth pattern as geo-tracker: no service account — the signed-in user's own
Google OAuth access token (with analytics.readonly scope) is used, scoped to
whatever GA4 access that user's own Google account already has. The caller
(api.py) is responsible for obtaining/refreshing that token from the session.

A single report call pulls all `/blog/` pagePath rows for a date range; slugs
are recovered by stripping the optional market prefix (/sg/, /my/, /ph/, ...)
since the same post is reachable under several market-prefixed paths and the
DB's `country` field is not a reliable way to pick one.
"""
import re
import time
from datetime import datetime, timezone

from config import GA4_PROPERTY_ID

_SLUG_RE = re.compile(r"^/(?:[a-z]{2}/)?blog/([^/?#]+)/?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 600


def _get_client(access_token: str):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2.credentials import Credentials

    credentials = Credentials(token=access_token)
    return BetaAnalyticsDataClient(credentials=credentials)


def _extract_slug(page_path: str) -> str | None:
    match = _SLUG_RE.match(page_path)
    return match.group(1) if match else None


def fetch_blog_analytics(
    access_token: str | None,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Return {"configured", "as_of", "data": {slug: metrics}} for all `/blog/*` pages.

    `access_token` is the current user's Google OAuth access token (analytics.readonly
    scope). If missing (e.g. GA4_PROPERTY_ID unset, or user hasn't re-authenticated
    since this feature shipped), returns {"configured": False}.

    Pass `start_date`/`end_date` (YYYY-MM-DD) for a custom range; otherwise `days`
    is used as a rolling "N days ago to today" window.
    """
    if not GA4_PROPERTY_ID:
        return {"configured": False, "data": {}}
    if not access_token:
        return {"configured": True, "error": "not_authenticated", "data": {}}

    use_custom_range = bool(
        start_date and end_date and _DATE_RE.match(start_date) and _DATE_RE.match(end_date)
    )
    if use_custom_range:
        range_key = f"{start_date}:{end_date}"
        ga_start, ga_end = start_date, end_date
    else:
        range_key = f"{days}d"
        ga_start, ga_end = f"{days}daysAgo", "today"

    cached = _cache.get(range_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        FilterExpression,
        FilterExpressionList,
        Filter,
        Metric,
        RunReportRequest,
    )

    client = _get_client(access_token)
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=ga_start, end_date=ga_end)],
        dimensions=[Dimension(name="pagePath"), Dimension(name="hostName")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
            Metric(name="engagementRate"),
        ],
        dimension_filter=FilterExpression(
            and_group=FilterExpressionList(
                expressions=[
                    FilterExpression(
                        filter=Filter(
                            field_name="hostName",
                            string_filter=Filter.StringFilter(
                                match_type=Filter.StringFilter.MatchType.CONTAINS,
                                value="hitpayapp.com",
                            ),
                        )
                    ),
                    FilterExpression(
                        filter=Filter(
                            field_name="pagePath",
                            string_filter=Filter.StringFilter(
                                match_type=Filter.StringFilter.MatchType.CONTAINS,
                                value="/blog/",
                            ),
                        )
                    ),
                ]
            )
        ),
        limit=10000,
    )

    try:
        response = client.run_report(request)
    except Exception as e:  # noqa: BLE001 - surface as data, don't crash the endpoint
        result = {"configured": True, "error": str(e), "data": {}}
        _cache[range_key] = (time.time(), result)
        return result

    by_slug: dict[str, dict] = {}
    for row in response.rows:
        page_path = row.dimension_values[0].value
        slug = _extract_slug(page_path)
        if not slug:
            continue

        sessions = int(float(row.metric_values[0].value or 0))
        users = int(float(row.metric_values[1].value or 0))
        avg_duration = float(row.metric_values[2].value or 0)
        bounce_rate = float(row.metric_values[3].value or 0)
        engagement_rate = float(row.metric_values[4].value or 0)

        entry = by_slug.setdefault(
            slug,
            {"sessions": 0, "users": 0, "_weighted_duration": 0.0, "_weighted_bounce": 0.0, "_weighted_engagement": 0.0},
        )
        entry["sessions"] += sessions
        entry["users"] += users
        entry["_weighted_duration"] += avg_duration * sessions
        entry["_weighted_bounce"] += bounce_rate * sessions
        entry["_weighted_engagement"] += engagement_rate * sessions

    data = {}
    for slug, entry in by_slug.items():
        sessions = entry["sessions"]
        data[slug] = {
            "sessions": sessions,
            "users": entry["users"],
            "avg_duration": round(entry["_weighted_duration"] / sessions, 1) if sessions else 0.0,
            "bounce_rate": round(entry["_weighted_bounce"] / sessions, 4) if sessions else 0.0,
            "engagement_rate": round(entry["_weighted_engagement"] / sessions, 4) if sessions else 0.0,
        }

    result = {
        "configured": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    _cache[range_key] = (time.time(), result)
    return result
