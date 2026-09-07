"""identity(): prefix format, determinism, invariant enforcement, and — most
important — byte-for-byte compatibility with the pre-refactor algorithm, so
existing users keep their S3 prefixes after deploy."""
import hashlib
import re
from datetime import datetime, timezone

import pytest

from lib import auth, identity


def _with_label(label):
    return auth._CALLER_LABEL.set(label)


def old_user_id(label: str) -> str:
    """The pre-refactor algorithm, verbatim (fallbacks included)."""
    seed = label or "local-dev"
    stable, _, rest = seed.partition(":")
    digest = hashlib.sha256(stable.encode()).hexdigest()[:8]
    human = ""
    if rest and "@" in rest:
        human = re.sub(r"[^a-z0-9._-]+", "-", rest.split("@", 1)[0].lower()).strip("-._")[:32]
    return f"mcp-{human}-{digest}" if human else f"mcp-{digest}"


def old_thread_segment(label: str) -> str:
    seed = label or "local-dev"
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    digest = hashlib.sha256(f"{seed}:{day}".encode()).hexdigest()[:12]
    return f"{day}_{digest}"


def test_prefixes_identical_to_pre_refactor():
    for label in (
        "abc-123:jan.kowalski@codewise.com",
        "f00:UPPER.Case+tag@example.org",
        "sub:___@weird.io",  # local part sanitizes to nothing -> hash-only form
    ):
        tok = _with_label(label)
        try:
            uid, thread = identity.identity()
        finally:
            auth._CALLER_LABEL.reset(tok)
        assert uid == old_user_id(label)
        assert thread == old_thread_segment(label)


def test_user_id_shape_and_stability():
    tok = _with_label("some-sub-uuid:Jan.Kowalski@codewise.com")
    try:
        uid1, t1 = identity.identity()
        uid2, t2 = identity.identity()
    finally:
        auth._CALLER_LABEL.reset(tok)
    assert uid1 == uid2 and t1 == t2
    assert uid1.startswith("mcp-jan.kowalski-")
    assert re.fullmatch(r"mcp-jan\.kowalski-[0-9a-f]{8}", uid1)
    assert re.fullmatch(r"\d{8}_[0-9a-f]{12}", t1)


def test_email_change_keeps_hash_part():
    tok = _with_label("same-sub:old@x.com")
    try:
        uid_old, _ = identity.identity()
    finally:
        auth._CALLER_LABEL.reset(tok)
    tok = _with_label("same-sub:new@x.com")
    try:
        uid_new, _ = identity.identity()
    finally:
        auth._CALLER_LABEL.reset(tok)
    assert uid_old.rsplit("-", 1)[1] == uid_new.rsplit("-", 1)[1]


@pytest.mark.parametrize("bad", ["", "just-a-sub", "sub:no-at-sign", ":email@x.com"])
def test_invariant_violations_raise(bad):
    tok = _with_label(bad)
    try:
        with pytest.raises(RuntimeError, match="sub:email"):
            identity.identity()
    finally:
        auth._CALLER_LABEL.reset(tok)
