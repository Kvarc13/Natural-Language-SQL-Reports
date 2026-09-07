"""lookup: stream extraction from unquoted backend CSV and routing-hint logic."""
import pytest

from lib import routing
from tools import lookup


class StubLoader:
    def __init__(self, rules):
        self._rules = rules

    def get(self):
        return self._rules


@pytest.fixture(autouse=True)
def default_rules(monkeypatch):
    monkeypatch.setattr(routing, "_loader", StubLoader(routing._defaults()))


HEADER = ("publisher_revenue_stream,user_first_name,user_last_name,user_email,"
          "publisher_feed_name,publisher_feed_hash,publisher_feed_id")


def test_streams_survive_commas_in_feed_names():
    # Unquoted backend CSV: the comma inside the feed name shifts later
    # columns — the FIRST column must still parse cleanly.
    data = "\n".join([
        HEADER,
        "DOMAIN,Jan,Kowalski,jan@x.com,Feeds, Inc | DOMAIN | RON,hash-a,1",
        "SEARCH,Jan,Kowalski,jan@x.com,MS | SEARCH | Net,hash-b,2",
    ])
    res = {"data": data, "row_count": 2}
    assert lookup._streams_from(res) == {"DOMAIN", "SEARCH"}


def test_truncation_note_dropped():
    data = "\n".join([
        HEADER,
        "DOMAIN,a,b,c,d,e,1",
        "(UWAGA: wynik obciety do 50 wierszy)",
    ])
    res = {"data": data, "row_count": 50, "is_truncated": True}
    assert lookup._streams_from(res) == {"DOMAIN"}


def test_streams_empty_on_no_data():
    assert lookup._streams_from({"data": "", "row_count": 0}) == set()
    assert lookup._streams_from({}) == set()


class TestHint:
    def test_single_stream_suggests_report(self):
        hint, suggested = lookup._hint_for({"DOMAIN"})
        assert suggested == "MasterReportRow"
        assert "All matched feeds are DOMAIN traffic" in hint
        assert "report_name='MasterReportRow'" in hint

    def test_same_report_from_two_streams_still_single(self):
        hint, suggested = lookup._hint_for({"SEARCH", "SHOPNOMIX"})
        assert suggested == "SearchMasterReportRow"
        assert "SEARCH/SHOPNOMIX" in hint

    def test_multi_report_hint_without_suggestion(self):
        hint, suggested = lookup._hint_for({"DOMAIN", "SEARCH"})
        assert suggested == ""
        assert "span multiple traffic types" in hint
        assert "DOMAIN/SEARCH" in hint

    def test_magic_only_yields_nothing(self):
        # MAGIC is intentionally unmapped -> no reports -> no hint at all.
        assert lookup._hint_for({"MAGIC"}) == ("", "")


def test_feed_match_regex_strips_pipe_suffix():
    # The SQL string must carry a literal backslash-pipe for DuckDB's regex
    # (matches " |suffix" in feed names); Python-level escaping is the usual
    # place to get this wrong.
    assert " \\|.*" in lookup._FEED_MATCH
