"""tools/lookup.py — lookup_feed_or_advertiser (fuzzy match + report routing hint)."""
import json

from lib.invoke import invoke_lambda
from lib import config, routing

# The one fuzzy-match condition for publisher feeds: exact substrings on
# email/feed name, Jaro-Winkler for typos on last name / email domain / base
# feed name (everything before the first " |" segment separator).
_FEED_MATCH = """user_email ILIKE '%' || q || '%'
   OR publisher_feed_name ILIKE '%' || q || '%'
   OR jaro_winkler_similarity(LOWER(user_last_name), LOWER(q)) > 0.85
   OR jaro_winkler_similarity(LOWER(SPLIT_PART(user_email, '@', 2)), LOWER(q) || '.com') > 0.85
   OR jaro_winkler_similarity(LOWER(REGEXP_REPLACE(publisher_feed_name, ' \\|.*', '')), LOWER(q)) > 0.8"""


def _streams_from(feed_res: dict) -> set[str]:
    """DISTINCT publisher_revenue_stream values out of the inline CSV result.

    The backend's CSV is NOT quoted, so a comma inside a feed name shifts
    every later column — which is exactly why the query selects
    publisher_revenue_stream FIRST: nothing precedes it, so the first field of
    every line is always intact. When the result is truncated, the backend
    appends a human note as the final line; drop it before parsing.
    """
    lines = [ln for ln in (feed_res.get("data") or "").split("\n") if ln.strip()]
    if feed_res.get("is_truncated") and lines:
        lines = lines[:-1]
    return {ln.split(",", 1)[0].strip().upper() for ln in lines[1:]}


def _hint_for(streams: set[str]) -> tuple[str, str]:
    """(report_routing_hint, suggested_report) from the matched feeds' streams."""
    reports = {routing.suggested_report_for_stream(s) for s in streams}
    reports.discard("")

    if len(reports) == 1:
        only = next(iter(reports))
        hint = (f"All matched feeds are {'/'.join(sorted(streams))} traffic — "
                f"use report_name='{only}' for reports on these feeds.")
        return hint, only
    if len(reports) > 1:
        hint = ("Matched feeds span multiple traffic types "
                f"({'/'.join(sorted(streams))}); pick the report per feed: "
                "SEARCH/SHOPNOMIX -> SearchMasterReportRow, DOMAIN/POPUP -> MasterReportRow.")
        return hint, ""
    return "", ""


def register(mcp):
    @mcp.tool()
    def lookup_feed_or_advertiser(query: str, revenue_stream: str = "") -> str:
        """Resolve a company/person/feed name to publisher feeds (and their hashes).

        Call this whenever the user names an entity (e.g. 'Sovrn', 'Parking Crew',
        'porraceous-wild') that needs to become a PUBLISHER_FEED filter — that filter
        takes the publisher_feed_hash, never the display name, so resolve first.

        Fuzzy matching (handles typos) runs against schemas/lookup/PublisherFeeds.csv,
        falling back to schemas/lookup/Users.csv for advertisers. Optional
        revenue_stream narrows feeds by traffic type: DOMAIN, SEARCH, POPUP, MAGIC,
        or SHOPNOMIX.

        Returns publisher/feed rows including publisher_feed_hash, plus a
        'suggested_report' (or 'report_routing_hint') derived from the feeds'
        revenue_stream — use it to pick the report type (SEARCH/SHOPNOMIX ->
        SearchMasterReportRow, DOMAIN/POPUP -> MasterReportRow). Show the user the
        feed name + hash + revenue_stream, confirm which feed(s) they mean, then use
        the hash(es) as PUBLISHER_FEED filter values. If it returns advertiser rows
        instead, that entity is an Advertiser (no publisher feeds). Personal columns
        (email, id) are for matching only — don't surface them to the user.
        """
        q = query.replace("'", "''")
        bucket = config.S3_BUCKET
        feeds_csv = f"s3://{bucket}/{config.SCHEMAS_LOOKUP_PREFIX}/PublisherFeeds.csv"
        users_csv = f"s3://{bucket}/{config.SCHEMAS_LOOKUP_PREFIX}/Users.csv"

        stream_clause = ""
        if revenue_stream:
            rs = revenue_stream.replace("'", "''").upper()
            stream_clause = f"\n   AND publisher_revenue_stream = '{rs}'"

        # publisher_revenue_stream deliberately FIRST — see _streams_from.
        feed_sql = f"""
WITH search AS (SELECT '{q}' AS q)
SELECT DISTINCT publisher_revenue_stream,
       user_first_name, user_last_name, user_email,
       publisher_feed_name, publisher_feed_hash, publisher_feed_id
FROM read_csv_auto('{feeds_csv}', all_varchar=true), search
WHERE ({_FEED_MATCH}){stream_clause}
ORDER BY publisher_feed_name
""".strip()

        feed_res = invoke_lambda(config.DUCKDB_LAMBDA, {"action": "query", "sql": feed_sql})

        if feed_res.get("status") == "success" and feed_res.get("row_count", 0) > 0:
            hint, suggested = _hint_for(_streams_from(feed_res))
            out = {"match_type": "publisher_feed", **feed_res}
            if hint:
                out["report_routing_hint"] = hint
            if suggested:
                out["suggested_report"] = suggested
            return json.dumps(out)

        # Fallback: advertisers / all users.
        adv_sql = f"""
WITH search AS (SELECT '{q}' AS q)
SELECT user_id, user_first_name, user_last_name, user_email, user_type,
       user_company_name
FROM read_csv_auto('{users_csv}', all_varchar=true), search
WHERE user_email ILIKE '%' || q || '%'
   OR user_company_name ILIKE '%' || q || '%'
   OR jaro_winkler_similarity(LOWER(user_last_name), LOWER(q)) > 0.85
   OR jaro_winkler_similarity(LOWER(user_first_name || ' ' || user_last_name), LOWER(q)) > 0.8
LIMIT 20
""".strip()
        adv_res = invoke_lambda(config.DUCKDB_LAMBDA, {"action": "query", "sql": adv_sql})
        return json.dumps({"match_type": "user_advertiser", **adv_res})
