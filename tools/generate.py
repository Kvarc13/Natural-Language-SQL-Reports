"""tools/generate.py — generate_zeropark_report (validation gates + 2-step chain).

Per-report knowledge is data on S3, so new reports need no code changes here:
  * COLUMN VALIDATION — every requested dimension/metric/filter name is
    checked against the chosen report's actual schema (lib/report_registry).
    A miss answers with the reports that DO have the column (inverted index
    across all schemas).
  * REDIRECT RULES — "wrong report for this question" checks come from
    routing.json (lib/routing.match_redirect); the TRAFFIC_SOURCE_ENUM rule
    is the built-in default.
  * RANGE LIMITS — resolved per report: schema "limits" block > routing.json
    range_limits > code defaults in lib/routing.py. When limits change,
    update routing.json AND the prompt docs (core_rules / analysis_rules /
    readme on S3) in one motion — enforcement lives here, the numbers the
    model quotes live there.
Registry unavailable (S3 hiccup) -> column checks are SKIPPED (fail-open, the
Gateway still rejects invalid columns), but range limits always hold.
"""
import json
from datetime import datetime, timedelta, timezone

from lib.invoke import invoke_lambda
from lib import config, report_registry, routing
from tools._upload import upload_csv

_BOUNDARY_FORMAT = "%Y-%m-%d %H"
_MAX_FUTURE = timedelta(hours=48)  # covers "full today" (tomorrow 00) with slack
_DEFAULT_METRICS = ["TOTAL_VISITS", "SOLD_VISITS", "PROFIT"]

# The gateway forwards time_aggregation VERBATIM to reports-api, which accepts
# only BY_HOUR | BY_DAY | BY_MONTH (confirmed empirically: lowercase 'day'
# bounced with HTTP 400 "Time aggregation is invalid!"). 'none' means no
# aggregation (gateway handles it). Lowercase synonyms normalize to BY_*.
_AGG_SYNONYMS = {
    "none": "none", "": "none",
    "by_hour": "BY_HOUR", "hour": "BY_HOUR", "hourly": "BY_HOUR",
    "by_day": "BY_DAY", "day": "BY_DAY", "daily": "BY_DAY", "date": "BY_DAY",
    "by_month": "BY_MONTH", "month": "BY_MONTH", "monthly": "BY_MONTH",
}


def _limits_summary(max_days: dict) -> str:
    """Human line like 'BY_HOUR 14d, BY_DAY 185d, BY_MONTH 735d, none 735d'."""
    order = ["BY_HOUR", "BY_DAY", "BY_MONTH", "none"]
    keys = order + sorted(k for k in max_days if k not in order)
    return ", ".join(f"{k} {max_days[k]}d" for k in keys if k in max_days)


def _validate_dates(date_from: str, date_to: str, agg: str, confirm_large: bool,
                    max_days: dict, confirm_days: dict):
    """Return an error/confirmation payload (dict) or None when the range is OK.

    Guardrails from the 2026-08 incident (a 5.5-year request hung the reports
    API; builds can't be cancelled, so control is pre-flight only):
      * HARD per-aggregation caps — confirm_large does NOT override them;
      * CONFIRM thresholds — legal but heavy: refuse once, generate only after
        the user's explicit yes relayed as confirm_large=true.
    """
    try:
        start = datetime.strptime(str(date_from).strip(), _BOUNDARY_FORMAT)
        end = datetime.strptime(str(date_to).strip(), _BOUNDARY_FORMAT)
    except (ValueError, TypeError):
        return {
            "status": "error",
            "stage": "validation",
            "message": (
                f"date_from='{date_from}' / date_to='{date_to}' do not match the "
                "required 'YYYY-MM-DD HH' boundary format (e.g. '2026-08-02 00')."
            ),
        }

    if start >= end:
        return {
            "status": "error",
            "stage": "validation",
            "message": (
                f"date_from ({date_from}) must be strictly before date_to "
                f"({date_to}); date_to is an exclusive end boundary."
            ),
        }

    now = datetime.now(timezone.utc).replace(tzinfo=None)  # boundaries are UTC-naive
    if end > now + _MAX_FUTURE:
        return {
            "status": "error",
            "stage": "validation",
            "message": (
                f"date_to ({date_to}) lies in the future. The latest valid end "
                "boundary is tomorrow 00 (which covers the whole of today)."
            ),
        }

    range_days = (end - start).total_seconds() / 86400.0
    hard = max_days[agg]
    if range_days > hard:
        return {
            "status": "error",
            "stage": "validation",
            "message": (
                f"Requested range is {range_days:.0f} days; the maximum for "
                f"time_aggregation='{agg}' on this report is {hard} days. This "
                "is a HARD limit protecting the reports API — do NOT retry "
                "with confirm_large and do NOT split the range into many "
                "sequential requests to bypass it. Tell the user the limit and "
                "ask them to narrow the question (shorter period, or a coarser "
                f"view; limits here: {_limits_summary(max_days)})."
            ),
        }

    confirm_at = confirm_days[agg]
    if range_days > confirm_at and not confirm_large:
        return {
            "status": "confirmation_required",
            "stage": "validation",
            "message": (
                f"Requested range is {range_days:.0f} days with "
                f"time_aggregation='{agg}' — above the {confirm_at}-day "
                "confirmation threshold. This will be a heavy report. Nothing "
                "was generated. Ask the user to explicitly confirm they need "
                "this full range; if they say yes, call "
                "generate_zeropark_report again with the SAME arguments plus "
                "confirm_large=true. Never set confirm_large=true without the "
                "user's explicit approval."
            ),
        }

    return None


def _validate_columns(report_name: str, dimensions: list, metrics: list,
                      filters: list | None):
    """Check every requested name against the report's schema; None when OK.

    Three miss classes, each with an actionable message:
      * name exists in this report but in a DIFFERENT role (a filter used as a
        dimension, etc.) -> say which role it has;
      * name exists in OTHER reports -> list them (and suggest the single
        common report when unambiguous);
      * name exists NOWHERE -> likely a typo.
    Skipped entirely (fail-open) when the schema isn't loaded.
    """
    cols = report_registry.columns_of(report_name)
    if not cols:
        return None  # registry unavailable for this report — Gateway will judge

    checks = (
        ("dimension", [str(d) for d in dimensions], "dimensions"),
        ("metric", [str(m) for m in metrics], "metrics"),
        ("filter", [str(f.get("name")) for f in (filters or [])
                    if isinstance(f, dict) and f.get("name")], "filters"),
    )
    everywhere_here = cols["dimensions"] | cols["metrics"] | cols["filters"]
    problems, candidate_sets = [], []
    for role, requested, key in checks:
        valid = cols[key]
        for name in requested:
            up = name.upper()
            if up in valid:
                continue
            if up in everywhere_here:
                roles = [k for k in ("dimensions", "metrics", "filters") if up in cols[k]]
                problems.append(
                    f"'{name}' exists in {report_name} but not as a {role} "
                    f"(it is a {'/'.join(r[:-1] for r in roles)})")
                continue
            others = [r for r in report_registry.reports_with(up) if r != report_name]
            if others:
                problems.append(
                    f"'{name}' is not in {report_name} — reports that have it: "
                    f"{', '.join(others)}")
                candidate_sets.append(set(others))
            else:
                problems.append(
                    f"'{name}' does not exist in ANY report (typo? check the schema)")

    if not problems:
        return None

    out = {
        "status": "error",
        "stage": "validation",
        "message": (
            "Schema mismatch: " + "; ".join(problems) + ". Column and filter "
            "names must be EXACT matches from the chosen report's schema — "
            "call get_report_schema('_index') and re-check before regenerating."
        ),
    }
    if candidate_sets:
        common = set.intersection(*candidate_sets)
        if len(common) == 1:
            out["suggested_report"] = next(iter(common))
    return out


def register(mcp):
    @mcp.tool()
    def generate_zeropark_report(
        report_name: str,
        date_from: str,
        date_to: str,
        dimensions: list[str],
        metrics: list[str],
        filters: list[dict] | None = None,
        time_aggregation: str = "none",
        confirm_large: bool = False,
    ) -> str:
        """Generate a Zeropark report and store it as CSV on S3 for later querying.

        Two-step chain (do not skip step 2): zeropark-api-bridge fetches the report
        from the Zeropark API and returns a csv_url; zeropark-upload-bridge then streams that
        CSV to S3. The returned s3_path is what query_data reads.

        Dates are boundary timestamps in 'YYYY-MM-DD HH' form, and date_to is an
        EXCLUSIVE end boundary (a point in time, not "last hour included"). To
        cover a full day X you MUST end at the NEXT day's 00:
          yesterday (Aug 2):  date_from='2026-08-02 00', date_to='2026-08-03 00'
          last 7 full days:   date_from='2026-07-27 00', date_to='2026-08-03 00'
        Ending at 'X 23' silently DROPS the 23:00-23:59 hour — never do that for
        whole-day ranges. When the user NAMES a period ('yesterday', 'last week',
        'March'), compute the exact boundaries yourself — don't ask them to spell
        out timestamps. When the user gives NO time range at all, default to the
        LAST 7 FULL DAYS and state that assumption in your reply; never assume a
        longer range than the user asked for.

        RANGE LIMITS are enforced server-side per time_aggregation (and can be
        stricter per report). The current thresholds are listed in the analyst
        rules and quoted verbatim in rejection/confirmation messages. Hard caps
        are never bypassable: if a range is rejected, do NOT split it into
        windows on your own initiative — tell the user the limit and the
        options (shorter period, or coarser aggregation). Only if the user then
        explicitly asks to split, use AT MOST TWO sequential windows — never
        more. Heavy-but-legal ranges return status='confirmation_required'
        first: relay the warning to the user, and ONLY after their explicit
        "yes" repeat the call with confirm_large=true. Never set
        confirm_large=true on your own. Generate ONE report at a time — never
        call this tool in parallel, and don't start a new generation while
        another is pending; if the user drops a pending report in favour of a
        different one, cancel it first (cancel_report_generation).

        report_name must come from get_report_schema('_index'). Every name in
        dimensions/metrics/filters[].name must be an EXACT schema match — the
        server validates them against the chosen report's schema and, on a
        miss, tells you which reports do have the column. Default metrics if
        unspecified: TOTAL_VISITS, SOLD_VISITS, PROFIT.

        FILTERS ARE FULLY SUPPORTED — pass them at generation time; do NOT avoid
        them or defer filtering to SQL. Format:
        filters=[{"name": "PUBLISHER_FEED", "values": ["ibis-leopard", "columbine-fowl"]}]
        Names come from the schema's filters list; PUBLISHER_FEED takes
        publisher_feed_hash values (not display names). Filters marked * in the
        schema are required and accept exactly one value.

        time_aggregation: 'none' (default, one row per dimension combo),
        'BY_HOUR', 'BY_DAY', or 'BY_MONTH' — these exact values (the API is
        strict). With BY_* a time column is ADDED AUTOMATICALLY to the report
        (don't add it to dimensions). Lowercase synonyms (day, daily, hour,
        month, ...) are normalized to the BY_* form; anything else is rejected
        with the allowed list. Use BY_HOUR only when explicitly asked (it
        multiplies rows); BY_MONTH may need adjustments per the API docs.

        SCOPE / double counting: MasterReportRow already INCLUDES all
        Search/Commerce Media traffic. NEVER generate MasterReportRow and a
        Search* report and sum them — Search gets counted twice. Whole-Zeropark
        picture = MasterReportRow alone; Search* = Search-only deep dives.

        ONLY generate when the data isn't already on S3. If a report is already stored
        this session, query it with query_data instead of regenerating.

        Returns metadata ONLY (s3_path, row_count, columns). NO report numbers — any
        figure you state must come from a query_data call.

        Large reports: if this returns status='pending' WITH a status_url, do NOT
        regenerate — call check_report_status with that status_url until success. If
        it returns status='pending' WITHOUT a status_url (an invoke timeout), the
        report is too large for one synchronous call: split the date range into
        shorter windows (e.g. weekly) and generate them one at a time (allowed here —
        this range already passed validation).
        """
        # --- Normalize + validate time_aggregation (see _AGG_SYNONYMS above).
        agg_key = str(time_aggregation or "none").strip().lower()
        normalized_agg = _AGG_SYNONYMS.get(agg_key)
        if normalized_agg is None:
            return json.dumps({
                "status": "error",
                "stage": "validation",
                "message": (
                    f"time_aggregation='{time_aggregation}' is not supported. "
                    "Allowed values: 'none' (default), 'BY_HOUR', 'BY_DAY', "
                    "'BY_MONTH'. With BY_* the time column is added automatically."
                ),
            })
        time_aggregation = normalized_agg
        effective_metrics = metrics or list(_DEFAULT_METRICS)

        # --- Gate 1: report must exist (per _index). Skipped when the index
        # is unavailable (fail-open).
        known = report_registry.index_names()
        if known and report_name not in known:
            return json.dumps({
                "status": "error",
                "stage": "validation",
                "message": (
                    f"Unknown report_name '{report_name}'. Available reports: "
                    f"{', '.join(known)}. Call get_report_schema('_index') and "
                    "pick one of them."
                ),
            })

        # --- Gate 2: date range (format, order, future, per-report caps).
        max_days, confirm_days = report_registry.limits_for(report_name)
        date_problem = _validate_dates(
            date_from, date_to, time_aggregation, confirm_large,
            max_days, confirm_days)
        if date_problem:
            return json.dumps(date_problem)

        # --- Gate 3: data-driven redirect rules ("wrong report for this
        # question"), e.g. TRAFFIC_SOURCE_ENUM as the sole dimension.
        dims_upper = [str(d).upper() for d in dimensions]
        rule = routing.match_redirect(report_name, dims_upper)
        if rule:
            out = {
                "status": "error",
                "stage": "validation",
                "message": rule["message"],
            }
            if rule.get("suggest"):
                out["suggested_report"] = rule["suggest"]
            return json.dumps(out)

        # --- Gate 4: every requested name must exist in the chosen schema.
        column_problem = _validate_columns(
            report_name, dimensions, effective_metrics, filters)
        if column_problem:
            return json.dumps(column_problem)

        gw = invoke_lambda(config.GATEWAY_LAMBDA, {
            "action": "generate_report",
            "params": {
                "report_name": report_name,
                "date_range": "CUSTOM",
                "date_from": date_from,
                "date_to": date_to,
                "dimensions": dimensions,
                "metrics": effective_metrics,
                "filters": filters or [],
                "time_aggregation": time_aggregation,
            },
        })

        status = gw.get("status")

        if status == "pending":
            status_url = gw.get("status_url", "")
            if status_url:
                # Gateway exhausted its ~75s poll window but the report exists.
                return json.dumps({
                    "status": "pending",
                    "status_url": status_url,
                    "report_name": report_name,
                    "message": (
                        f"Report '{report_name}' is large and still building after ~1.5 min. "
                        "Do NOT regenerate. Call check_report_status with the status_url "
                        "and report_name below — it paces its own polling. Repeat until success. "
                        "If the user decides they no longer want this report, call "
                        "cancel_report_generation with the same status_url."
                    ),
                })
            # Pending WITHOUT status_url == boto3 read_timeout: the invoke itself
            # timed out before the Gateway returned. There is nothing to poll.
            return json.dumps({
                "status": "pending",
                "report_name": report_name,
                "message": (
                    f"Report '{report_name}' exceeded the synchronous time budget and "
                    "returned no status_url to poll. This usually means the range is too "
                    "large. Split it into shorter windows (e.g. one week at a time) and "
                    "generate them one at a time, then combine with query_data."
                ),
            })

        if status != "success":
            return json.dumps({
                "status": "error", "stage": "gateway",
                "message": gw.get("message", "Unknown gateway error"),
            })

        return upload_csv(gw.get("csv_url", ""), report_name)
