"""
lib/identity.py — Caller identity -> stable, readable S3 prefix.

The backend forms S3 paths as raw_reports/{user_id}/{thread_ts}/... . Both
segments derive from the authenticated caller's label, set per request by the
auth middleware.

INVARIANT: the label is ALWAYS "sub:email". lib/auth.py refuses to serve a
request (503 + Retry-After) until the caller's email is resolved, so a request
that reaches any tool carries both parts. The prefix must be identical on
every request — a fallback prefix would split one user's data across folders
(a report generated under one prefix "disappears" for a follow-up query_data)
— so identity() RAISES on a label that breaks the invariant instead of
inventing one. That path is unreachable through the middleware; reaching it
means an upstream regression, and a loud tool error beats silent data
splitting.

Prefix design:
  * hash part  <- sha256(sub)[:8] — stable forever, survives email changes,
    disambiguates sanitized-name collisions;
  * readable part <- the email's local part, lowercased, sanitized to
    [a-z0-9._-], capped at 32 chars — for humans browsing S3;
  * thread segment <- caller + UTC date, deterministically: all of a caller's
    work on a given day shares one prefix regardless of which container
    handles each request, and the 48 h S3 lifecycle cleans everything up.
    (For true per-conversation isolation, thread the MCP session id in here —
    date-scoping is the correct default given the TTL.)
"""
import hashlib
import re
from datetime import datetime, timezone

from lib.auth import current_label

_SAFE = re.compile(r"[^a-z0-9._-]+")


def _user_id(label: str) -> str:
    """Readable, stable per-caller prefix: mcp-<email-local-part>-<hash8(sub)>."""
    sub, _, email = label.partition(":")
    digest = hashlib.sha256(sub.encode()).hexdigest()[:8]
    human = _SAFE.sub("-", email.split("@", 1)[0].lower()).strip("-._")[:32]
    # An email local part can sanitize to nothing (e.g. "___@x"); the hash
    # alone still satisfies stability and uniqueness.
    return f"mcp-{human}-{digest}" if human else f"mcp-{digest}"


def _thread_segment(label: str) -> str:
    """Deterministic 'thread' segment: caller + UTC day. No per-process randomness."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    digest = hashlib.sha256(f"{label}:{day}".encode()).hexdigest()[:12]
    return f"{day}_{digest}"


def identity() -> tuple[str, str]:
    """Return (user_id, thread_ts) for the current request's caller.

    Raises on a label without the "sub:email" shape — see the module
    docstring for why failing loud beats a fallback prefix.
    """
    label = current_label()
    sub, sep, email = label.partition(":")
    if not sub or not sep or "@" not in email:
        raise RuntimeError(
            "identity(): caller label does not carry 'sub:email' (auth "
            "middleware bypassed or regressed) — refusing to build an S3 prefix."
        )
    return _user_id(label), _thread_segment(label)
