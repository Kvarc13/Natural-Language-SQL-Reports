"""report_registry: limits precedence (schema > routing > defaults) and
schema column collection."""
import pytest

from lib import report_registry, routing


class StubLoader:
    def __init__(self, state):
        self._state = state

    def get(self):
        return self._state


def _state_with(schema):
    cols = report_registry._collect_columns(schema)
    return {"index_names": ["R"], "schemas": {"R": {"schema": schema, "cols": cols}},
            "columns": {}}


@pytest.fixture
def base_limits(monkeypatch):
    monkeypatch.setattr(
        routing, "range_limits",
        lambda: ({"BY_DAY": 185, "BY_HOUR": 14}, {"BY_DAY": 95, "BY_HOUR": 3}))


def test_schema_limits_override_globals(monkeypatch, base_limits):
    schema = {"limits": {"max_range_days": {"BY_DAY": 92},
                         "confirm_range_days": {"by_hour": 1}}}
    monkeypatch.setattr(report_registry, "_loader", StubLoader(_state_with(schema)))
    mx, cf = report_registry.limits_for("R")
    assert mx == {"BY_DAY": 92, "BY_HOUR": 14}   # overridden / inherited
    assert cf == {"BY_DAY": 95, "BY_HOUR": 1}    # inherited / overridden (norm. key)


def test_unknown_report_gets_globals(monkeypatch, base_limits):
    monkeypatch.setattr(report_registry, "_loader",
                        StubLoader({"index_names": [], "schemas": {}, "columns": {}}))
    mx, cf = report_registry.limits_for("nope")
    assert mx["BY_DAY"] == 185 and cf["BY_DAY"] == 95


def test_collect_columns_ignores_non_list_groups():
    schema = {
        "columns": {
            "dimensions": {"geo": ["Country", "city"], "_notes": "a string, not a group"},
            "metrics": {"money": ["profit"]},
        },
        "filters": {"required": ["publisher_feed"], "optional": ["country"]},
    }
    cols = report_registry._collect_columns(schema)
    assert cols["dimensions"] == {"COUNTRY", "CITY"}
    assert cols["metrics"] == {"PROFIT"}
    assert cols["filters"] == {"PUBLISHER_FEED", "COUNTRY"}


def test_inverted_index_and_accessors(monkeypatch):
    state = _state_with({"columns": {"dimensions": {"g": ["BRAND"]},
                                     "metrics": {"m": ["PROFIT"]}}})
    state["columns"] = {"BRAND": {"R"}, "PROFIT": {"R"}}
    monkeypatch.setattr(report_registry, "_loader", StubLoader(state))
    assert report_registry.index_names() == ["R"]
    assert report_registry.reports_with("brand") == ["R"]
    assert report_registry.columns_of("R")["dimensions"] == {"BRAND"}
    assert report_registry.columns_of("missing") is None
