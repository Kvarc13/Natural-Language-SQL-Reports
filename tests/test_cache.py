"""cache.TtlLoader — the one loader behind prompts, routing and the registry."""
import time

from lib import config
from lib.cache import TtlLoader


def counter_loader(values):
    calls = {"n": 0}

    def load():
        calls["n"] += 1
        return values[min(calls["n"] - 1, len(values) - 1)]

    return calls, load


def test_lazy_single_load_with_ttl_zero(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_CACHE_TTL_SEC", 0)
    calls, load = counter_loader([{"a": 1}])
    loader = TtlLoader(load)
    assert calls["n"] == 0                      # nothing at construction
    assert loader.get() == {"a": 1}
    assert loader.get() == {"a": 1}
    assert calls["n"] == 1                      # TTL=0 pins the first copy


def test_reload_after_ttl(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_CACHE_TTL_SEC", 1)
    calls, load = counter_loader([{"v": 1}, {"v": 2}])
    loader = TtlLoader(load)
    assert loader.get() == {"v": 1}
    loader._loaded_at = time.time() - 2         # age the entry past the TTL
    assert loader.get() == {"v": 2}
    assert calls["n"] == 2


def test_incomplete_value_retried_every_call(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_CACHE_TTL_SEC", 300)
    calls, load = counter_loader([
        {"schemas": {}},                        # degraded (S3 hiccup)
        {"schemas": {}},                        # still degraded
        {"schemas": {"R": 1}},                  # recovered
    ])
    loader = TtlLoader(load, is_complete=lambda s: bool(s["schemas"]))
    assert loader.get() == {"schemas": {}}
    assert loader.get() == {"schemas": {}}      # retried despite fresh TTL
    assert loader.get() == {"schemas": {"R": 1}}
    assert calls["n"] == 3
    assert loader.get() == {"schemas": {"R": 1}}
    assert calls["n"] == 3                      # complete value now cached
