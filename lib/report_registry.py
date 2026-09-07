"""
lib/report_registry.py — TTL-cached registry of report schemas (the S3 truth).

The schemas on S3 are the single source of per-report knowledge, so adding
report #5..#N = upload <name>.json + add an _index.json entry. No code:

  * loads schemas/reports/_index.json plus every listed schema (TTL cache,
    lib/cache.py — same rhythm as prompts and routing);
  * builds an INVERTED COLUMN INDEX (column -> reports that have it), so a
    request for BRAND against MasterReportRow can answer "BRAND lives in
    SearchMasterReportRow, SearchTargetMasterReportRow" without any
    hand-maintained list;
  * resolves per-report RANGE LIMITS: a schema's optional "limits" block
      "limits": {"max_range_days": {"BY_DAY": 92}, "confirm_range_days": {...}}
    overrides the global values from routing.json (which in turn override the
    code defaults in lib/routing.py).

FAIL-OPEN BY DESIGN: if S3 is unreachable or the index is malformed, accessors
return empty/None and callers skip schema validation (the Gateway still
rejects truly invalid columns). Range limits always resolve — worst case to
the code defaults — so the incident guardrails never turn off. An EMPTY
registry is retried on every call (not only after the TTL), so one S3 hiccup
doesn't blind validation for a whole cache window.
"""
import json
import logging

from lib import config, routing
from lib.cache import TtlLoader
from lib.s3 import get_text, get_schema_json

logger = logging.getLogger(__name__)


def _collect_columns(schema: dict) -> dict:
    """{'dimensions': set, 'metrics': set, 'filters': set} from one schema.

    Dimension/metric groups are dicts of lists; non-list members (e.g. a
    '_metric_notes' string) are ignored. Filters = required + optional names.
    """
    cols = schema.get("columns") or {}
    out = {"dimensions": set(), "metrics": set(), "filters": set()}
    for kind in ("dimensions", "metrics"):
        for group in (cols.get(kind) or {}).values():
            if isinstance(group, list):
                out[kind].update(str(c).upper() for c in group)
    f = schema.get("filters") or {}
    out["filters"].update(
        str(c).upper() for c in (f.get("required") or []) + (f.get("optional") or [])
    )
    return out


def _load() -> dict:
    """Read _index.json + every listed schema into one registry state dict."""
    empty = {"index_names": [], "schemas": {}, "columns": {}}
    raw = get_text(f"{config.SCHEMAS_REPORTS_PREFIX}/_index.json")
    try:
        index = json.loads(raw) if raw else []
        if not isinstance(index, list):
            raise ValueError("_index.json is not a list")
    except Exception as e:
        logger.error(f"[registry] _index.json unreadable ({e}) — registry empty, "
                     "schema validation will be skipped (fail-open)")
        return empty

    index_names, schemas, columns = [], {}, {}
    for entry in index:
        name = (entry or {}).get("report_name")
        if not name:
            continue
        index_names.append(name)
        text, found = get_schema_json(name)
        if not found:
            logger.warning(f"[registry] schema listed in _index but missing on S3: {name}")
            continue
        try:
            schema = json.loads(text)
        except Exception as e:
            logger.error(f"[registry] schema {name}.json malformed, skipping: {e}")
            continue
        cols = _collect_columns(schema)
        schemas[name] = {"schema": schema, "cols": cols}
        for kind in ("dimensions", "metrics", "filters"):
            for c in cols[kind]:
                columns.setdefault(c, set()).add(name)

    logger.info(f"[registry] loaded {len(schemas)}/{len(index_names)} schemas, "
                f"{len(columns)} distinct columns")
    return {"index_names": index_names, "schemas": schemas, "columns": columns}


_loader = TtlLoader(_load, is_complete=lambda state: bool(state["schemas"]))


def index_names() -> list[str]:
    """Report names listed in _index.json ([] when the index is unavailable)."""
    return list(_loader.get()["index_names"])


def columns_of(report_name: str) -> dict | None:
    """{'dimensions','metrics','filters'} sets for a report, or None if its
    schema isn't loaded (missing/malformed/S3 down) — callers then skip
    column validation for it."""
    entry = _loader.get()["schemas"].get(report_name)
    return entry["cols"] if entry else None


def reports_with(column: str) -> list[str]:
    """All reports (sorted) that have this column as dimension, metric, or filter."""
    return sorted(_loader.get()["columns"].get(str(column).upper(), set()))


def limits_for(report_name: str) -> tuple[dict, dict]:
    """(max_range_days, confirm_range_days) for a report, keyed by aggregation.

    Resolution: schema's optional "limits" block > routing.json range_limits >
    code defaults. Always returns complete dicts (every aggregation key)."""
    base_max, base_confirm = routing.range_limits()
    entry = _loader.get()["schemas"].get(report_name)
    if not entry:
        return base_max, base_confirm
    lim = entry["schema"].get("limits") or {}
    return (
        routing.merge_limits(base_max, lim.get("max_range_days"),
                             source=f"{report_name}.json"),
        routing.merge_limits(base_confirm, lim.get("confirm_range_days"),
                             source=f"{report_name}.json"),
    )
