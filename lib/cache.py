"""
lib/cache.py — one TTL-cached loader for S3-backed docs and config.

Prompts, routing rules and the report registry all follow the same rhythm:
load from S3 lazily, serve the cached copy, reload once
config.PROMPT_CACHE_TTL_SEC lapses (0 = keep the first copy for the whole
container lifetime). This module is that rhythm, written once.
"""
import time

from lib import config


class TtlLoader:
    """Lazily calls ``load()`` and caches the result until the TTL lapses.

    ``is_complete(value)`` lets a caller mark a loaded value as degraded
    (e.g. an empty schema registry after an S3 hiccup): an incomplete value is
    retried on EVERY call rather than pinned for a whole TTL window. The
    default treats any truthy value as complete.
    """

    def __init__(self, load, is_complete=bool):
        self._load = load
        self._is_complete = is_complete
        self._value = None
        self._loaded_at = 0.0

    def get(self):
        ttl = config.PROMPT_CACHE_TTL_SEC
        stale = ttl > 0 and (time.time() - self._loaded_at) > ttl
        if self._value is None or stale or not self._is_complete(self._value):
            self._value = self._load()
            self._loaded_at = time.time()
        return self._value
