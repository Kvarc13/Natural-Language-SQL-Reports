"""
lib/routing.py — data-driven report-routing rules (S3-editable, TTL-cached).

Three rule groups, all editable in schemas/config/routing.json without a
deploy (edits apply on warm containers within PROMPT_CACHE_TTL_SEC):

  * stream_to_report — publisher_revenue_stream -> suggested report. MAGIC is
    intentionally unmapped: it can appear on either side, so we don't force it.
  * range_limits — GLOBAL per-aggregation caps: max_range_days are HARD,
    confirm_range_days trigger the confirm_large user-consent gate. A schema's
    own "limits" block overrides these per report (lib/report_registry.py).
  * redirect_rules — "wrong report for this question" checks. Each rule:
    {"when": {...}, "suggest": "<report>", "message": "..."} where "when"
    AND-combines: report, report_not, sole_dimension, has_dimension.

routing.json must be the current format (the keys above); unknown keys are
ignored. The constants below are the last-resort fallback so the guardrails
from the 2026-08 incident hold even with zero S3 config. When limits change,
update the prompt docs (core_rules / analysis_rules / readme on S3) in the
same motion — enforcement reads this file, the numbers the model quotes live
there.
"""
import json
import logging

from lib import config
from lib.cache import TtlLoader
from lib.s3 import get_text

logger = logging.getLogger(__name__)

# --- Last-resort defaults (guardrails must hold with zero S3 config) --------
_DEFAULT_STREAM_TO_REPORT = {
    "SEARCH": "SearchMasterReportRow",
    "SHOPNOMIX": "SearchMasterReportRow",
    "DOMAIN": "MasterReportRow",
    "POPUP": "MasterReportRow",
    # MAGIC intentionally omitted — can appear on either side, so we don't force it.
}

_DEFAULT_MAX_RANGE_DAYS = {"BY_HOUR": 14, "none": 735, "BY_DAY": 185, "BY_MONTH": 735}
# 'none' confirms at 95 like BY_DAY: a whole-range rollup is no heavier than a
# daily split of the same range.
_DEFAULT_CONFIRM_RANGE_DAYS = {"BY_HOUR": 3, "none": 95, "BY_DAY": 95, "BY_MONTH": 185}

# The built-in "per revenue stream" redirect — the same rule
# schemas/config/routing.json ships; present here so the check survives S3
# being unreachable.
_DEFAULT_REDIRECT_RULES = [{
    "when": {"report_not": "TimeRevenueStreamRow", "sole_dimension": "TRAFFIC_SOURCE_ENUM"},
    "suggest": "TimeRevenueStreamRow",
    "message": (
        "TRAFFIC_SOURCE_ENUM returns only the TECHNICAL traffic types (Search/Domain/"
        "Pop Up) and hides the business revenue streams MAGIC and "
        "SHOPNOMIX inside them — it does NOT answer a 'per revenue "
        "stream' question. Use report_name='TimeRevenueStreamRow' instead: it has a "
        "REVENUE_STREAM column (DOMAIN/MAGIC/SEARCH/POPUP/SHOPNOMIX) and "
        "the same financial metrics. Call get_report_schema('TimeRevenueStreamRow') for "
        "its exact columns, then regenerate. If you genuinely want the "
        "technical Search/Domain/Pop split (not business streams), add "
        "another dimension alongside TRAFFIC_SOURCE_ENUM to signal that intent."
    ),
}]


def _agg_key(k) -> str:
    """Normalize a range-limit key: 'none' stays 'none', aggregations upper-case."""
    return "none" if str(k).strip().lower() == "none" else str(k).strip().upper()


def merge_limits(base: dict, override, source: str = "routing.json") -> dict:
    """Copy of `base` with `override`'s per-aggregation values applied.

    Bad values are logged (with their source, for triage) and ignored — a
    typo in config must never turn a guardrail off.
    """
    merged = dict(base)
    for k, v in (override or {}).items():
        try:
            merged[_agg_key(k)] = int(v)
        except (TypeError, ValueError):
            logger.warning(f"[routing] bad range-limit value {k}={v!r} in {source}, ignored")
    return merged


def _defaults() -> dict:
    return {
        "stream_to_report": dict(_DEFAULT_STREAM_TO_REPORT),
        "max_range_days": dict(_DEFAULT_MAX_RANGE_DAYS),
        "confirm_range_days": dict(_DEFAULT_CONFIRM_RANGE_DAYS),
        "redirect_rules": [dict(r) for r in _DEFAULT_REDIRECT_RULES],
    }


def _load() -> dict:
    """Parse routing.json into the rules dict; any problem -> defaults."""
    rules = _defaults()
    raw = get_text(f"{config.SCHEMAS_CONFIG_PREFIX}/routing.json")
    if not raw:
        logger.info("[routing] no routing.json on S3 — using built-in defaults")
        return rules
    try:
        obj = json.loads(raw)

        s2r = {str(k).upper(): v for k, v in (obj.get("stream_to_report") or {}).items()}
        if s2r:
            rules["stream_to_report"] = s2r

        rl = obj.get("range_limits") or {}
        rules["max_range_days"] = merge_limits(
            rules["max_range_days"], rl.get("max_range_days"))
        rules["confirm_range_days"] = merge_limits(
            rules["confirm_range_days"], rl.get("confirm_range_days"))

        if isinstance(obj.get("redirect_rules"), list):
            rules["redirect_rules"] = [r for r in obj["redirect_rules"]
                                       if isinstance(r, dict) and r.get("message")]

        logger.info("[routing] rules loaded from S3 config")
        return rules
    except Exception as e:
        logger.error(f"[routing] routing.json malformed, using defaults: {e}")
        return _defaults()


_loader = TtlLoader(_load)


# --- Public API --------------------------------------------------------------

def suggested_report_for_stream(stream: str) -> str:
    """publisher_revenue_stream -> report name ('' when unmapped, e.g. MAGIC)."""
    return _loader.get()["stream_to_report"].get((stream or "").strip().upper(), "")


def range_limits() -> tuple[dict, dict]:
    """Global (max_range_days, confirm_range_days) per aggregation key."""
    rules = _loader.get()
    return dict(rules["max_range_days"]), dict(rules["confirm_range_days"])


def match_redirect(report_name: str, dims_upper: list[str]) -> dict | None:
    """First redirect rule whose 'when' matches, or None.

    Supported AND-ed conditions in when: report (equals), report_not
    (differs), sole_dimension (dims == exactly [X]), has_dimension (X in
    dims). An empty/absent 'when' never matches (a rule must be conditional).
    """
    for rule in _loader.get()["redirect_rules"]:
        when = rule.get("when") or {}
        if not when:
            continue
        if "report" in when and report_name != when["report"]:
            continue
        if "report_not" in when and report_name == when["report_not"]:
            continue
        if "sole_dimension" in when and \
                dims_upper != [str(when["sole_dimension"]).strip().upper()]:
            continue
        if "has_dimension" in when and \
                str(when["has_dimension"]).strip().upper() not in dims_upper:
            continue
        return rule
    return None
