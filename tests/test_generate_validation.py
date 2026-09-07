"""generate's pre-flight gates: the incident guardrails (_validate_dates) and
the schema gate (_validate_columns)."""
from datetime import datetime, timedelta, timezone

import pytest

from tools import generate
from lib import report_registry

MAX = {"BY_HOUR": 14, "none": 735, "BY_DAY": 185, "BY_MONTH": 735}
CONFIRM = {"BY_HOUR": 3, "none": 95, "BY_DAY": 95, "BY_MONTH": 185}


def _dates(days_back: int, days_len: int):
    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_back)
    end = start + timedelta(days=days_len)
    fmt = "%Y-%m-%d %H"
    return start.strftime(fmt), end.strftime(fmt)


def v(date_from, date_to, agg="BY_DAY", confirm=False):
    return generate._validate_dates(date_from, date_to, agg, confirm, MAX, CONFIRM)


class TestDates:
    def test_ok_range(self):
        f, t = _dates(10, 7)
        assert v(f, t) is None

    def test_bad_format(self):
        out = v("2026-08-02", "2026-08-03 00")
        assert out["status"] == "error" and "YYYY-MM-DD HH" in out["message"]

    def test_from_not_before_to(self):
        assert v("2026-08-03 00", "2026-08-03 00")["status"] == "error"
        assert v("2026-08-04 00", "2026-08-03 00")["status"] == "error"

    def test_future_end_rejected(self):
        f, t = _dates(-3, 2)  # starts 3 days in the future
        out = v(f, t)
        assert out["status"] == "error" and "future" in out["message"]

    def test_tomorrow_00_allowed(self):
        # 'full today' ends at tomorrow 00 — inside the 48h slack.
        f, t = _dates(0, 1)
        assert v(f, t) is None

    def test_hard_cap_rejects_even_with_confirm(self):
        f, t = _dates(200, 186)  # BY_DAY hard cap is 185
        out = v(f, t, confirm=True)
        assert out["status"] == "error" and "HARD limit" in out["message"]

    def test_confirm_threshold(self):
        f, t = _dates(100, 96)  # above confirm (95), below hard (185)
        out = v(f, t)
        assert out["status"] == "confirmation_required"
        assert "confirm_large=true" in out["message"]
        assert v(f, t, confirm=True) is None  # explicit consent passes

    def test_at_cap_boundary_passes(self):
        f, t = _dates(200, 185)  # exactly the hard cap, > not >=
        assert v(f, t, confirm=True) is None

    def test_by_hour_much_stricter(self):
        f, t = _dates(20, 15)
        assert v(f, t, agg="BY_HOUR", confirm=True)["status"] == "error"


FAKE_COLS = {
    "MasterReportRow": {
        "dimensions": {"COUNTRY", "TRAFFIC_SOURCE_ENUM"},
        "metrics": {"PROFIT", "TOTAL_VISITS"},
        "filters": {"PUBLISHER_FEED", "COUNTRY"},
    },
    "SearchMasterReportRow": {
        "dimensions": {"BRAND", "COUNTRY"},
        "metrics": {"PROFIT"},
        "filters": {"PUBLISHER_FEED"},
    },
}


@pytest.fixture
def fake_registry(monkeypatch):
    monkeypatch.setattr(report_registry, "columns_of", lambda r: FAKE_COLS.get(r))
    monkeypatch.setattr(
        report_registry, "reports_with",
        lambda c: sorted(r for r, cols in FAKE_COLS.items()
                         if c.upper() in cols["dimensions"] | cols["metrics"] | cols["filters"]))
    # generate imported names by module reference (report_registry.columns_of),
    # so patching the lib module is enough.


class TestColumns:
    def test_all_valid(self, fake_registry):
        assert generate._validate_columns(
            "MasterReportRow", ["COUNTRY"], ["PROFIT"],
            [{"name": "PUBLISHER_FEED", "values": ["x"]}]) is None

    def test_role_mismatch_reported(self, fake_registry):
        out = generate._validate_columns("MasterReportRow", ["PUBLISHER_FEED"], [], None)
        assert out["status"] == "error"
        assert "not as a dimension" in out["message"]

    def test_other_report_suggested(self, fake_registry):
        out = generate._validate_columns("MasterReportRow", ["BRAND"], [], None)
        assert "SearchMasterReportRow" in out["message"]
        assert out["suggested_report"] == "SearchMasterReportRow"

    def test_typo_reported(self, fake_registry):
        out = generate._validate_columns("MasterReportRow", ["PROFITZ"], [], None)
        assert "ANY report" in out["message"]

    def test_fail_open_without_schema(self, fake_registry):
        assert generate._validate_columns("UnknownReport", ["WHATEVER"], [], None) is None


def test_agg_synonyms_table():
    assert generate._AGG_SYNONYMS["daily"] == "BY_DAY"
    assert generate._AGG_SYNONYMS["hour"] == "BY_HOUR"
    assert generate._AGG_SYNONYMS[""] == "none"
    assert "weekly" not in generate._AGG_SYNONYMS
