"""routing: redirect-rule DSL, limit merging, and the defaults fallback."""
import pytest

from lib import routing


class StubLoader:
    def __init__(self, rules):
        self._rules = rules

    def get(self):
        return self._rules


@pytest.fixture
def rules(monkeypatch):
    r = routing._defaults()
    monkeypatch.setattr(routing, "_loader", StubLoader(r))
    return r


class TestMatchRedirect:
    def test_default_rule_fires_on_sole_traffic_source(self, rules):
        hit = routing.match_redirect("MasterReportRow", ["TRAFFIC_SOURCE_ENUM"])
        assert hit and hit["suggest"] == "TimeRevenueStreamRow"

    def test_report_not_excludes_target(self, rules):
        assert routing.match_redirect(
            "TimeRevenueStreamRow", ["TRAFFIC_SOURCE_ENUM"]) is None

    def test_sole_dimension_means_exactly_one(self, rules):
        assert routing.match_redirect(
            "MasterReportRow", ["TRAFFIC_SOURCE_ENUM", "COUNTRY"]) is None
        assert routing.match_redirect("MasterReportRow", []) is None

    def test_has_dimension_and_report(self, monkeypatch):
        monkeypatch.setattr(routing, "_loader", StubLoader({"redirect_rules": [{
            "when": {"report": "MasterReportRow", "has_dimension": "brand"},
            "suggest": "SearchMasterReportRow", "message": "m",
        }]}))
        assert routing.match_redirect("MasterReportRow", ["COUNTRY", "BRAND"])
        assert routing.match_redirect("SearchMasterReportRow", ["BRAND"]) is None
        assert routing.match_redirect("MasterReportRow", ["COUNTRY"]) is None

    def test_empty_when_never_matches(self, monkeypatch):
        monkeypatch.setattr(routing, "_loader", StubLoader(
            {"redirect_rules": [{"when": {}, "suggest": "X", "message": "m"}]}))
        assert routing.match_redirect("Any", ["ANY"]) is None

    def test_first_match_wins(self, monkeypatch):
        monkeypatch.setattr(routing, "_loader", StubLoader({"redirect_rules": [
            {"when": {"has_dimension": "A"}, "suggest": "first", "message": "1"},
            {"when": {"has_dimension": "A"}, "suggest": "second", "message": "2"},
        ]}))
        assert routing.match_redirect("R", ["A"])["suggest"] == "first"


class TestMergeLimits:
    def test_override_and_key_normalization(self):
        out = routing.merge_limits({"BY_DAY": 185, "none": 735},
                                   {"by_day": "92", "None": 30})
        assert out == {"BY_DAY": 92, "none": 30}

    def test_bad_value_ignored(self):
        out = routing.merge_limits({"BY_DAY": 185}, {"BY_DAY": "a lot"})
        assert out == {"BY_DAY": 185}

    def test_none_override_is_noop(self):
        base = {"BY_DAY": 185}
        assert routing.merge_limits(base, None) == base


class TestLoadFallback:
    def test_malformed_json_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setattr(routing, "get_text", lambda key: "{not json")
        assert routing._load() == routing._defaults()

    def test_missing_file_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setattr(routing, "get_text", lambda key: "")
        assert routing._load() == routing._defaults()

    def test_partial_config_merges_over_defaults(self, monkeypatch):
        monkeypatch.setattr(routing, "get_text", lambda key: (
            '{"range_limits": {"max_range_days": {"BY_DAY": 90}},'
            ' "stream_to_report": {"search": "SearchMasterReportRow"}}'))
        out = routing._load()
        assert out["max_range_days"]["BY_DAY"] == 90
        assert out["max_range_days"]["BY_HOUR"] == 14          # default kept
        assert out["stream_to_report"] == {"SEARCH": "SearchMasterReportRow"}
        assert out["redirect_rules"] == routing._defaults()["redirect_rules"]

    def test_rules_without_message_dropped(self, monkeypatch):
        monkeypatch.setattr(routing, "get_text", lambda key: (
            '{"redirect_rules": [{"when": {"report": "X"}},'
            ' {"when": {"report": "Y"}, "suggest": "Z", "message": "ok"}]}'))
        out = routing._load()
        assert len(out["redirect_rules"]) == 1
        assert out["redirect_rules"][0]["message"] == "ok"
