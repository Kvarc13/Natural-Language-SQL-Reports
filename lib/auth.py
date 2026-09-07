"""
lib/auth.py — Cognito OAuth for the public Function URL. The ONLY auth mode.

The middleware validates a Cognito-issued JWT on every request:
  * signature via the pool's JWKS (RS256), cached by PyJWKClient (it refreshes
    on unknown kid, so key rotation is handled for us);
  * iss == COGNITO_ISSUER, exp not passed;
  * audience: Cognito ID tokens carry `aud`=client_id, access tokens carry
    `client_id` (NO `aud`). We accept EITHER, keyed on `token_use` — this is
    the #1 Cognito gotcha (fastmcp #3739): validating `aud` alone rejects
    every access token. See _check_audience.

IDENTITY GATE: the caller label is ALWAYS "sub:email". The S3 prefix derives
its readable part from the email (lib/identity.py), and the prefix must be
identical on every request — so email resolution is a gate, not a best effort.
Resolution order:
  1. per-container cache (sub -> email; successes only);
  2. hosted UI /oauth2/userInfo (the access token is the credential, 3 s);
  3. Cognito ListUsers by sub via the Lambda execution role (needs
     cognito-idp:ListUsers on the pool — see template.yaml);
  4. if BOTH fail, the request is answered 503 + Retry-After and NEVER reaches
     a tool. There is no hash-only fallback prefix — that was the source of
     the "same user, two prefixes" bug.

The middleware also serves the OAuth discovery Claude needs:
  * 401 + WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"
    on any unauthenticated request (Claude only reads WWW-Authenticate on 401);
  * GET /.well-known/* -> RFC 9728 / RFC 8414 documents (see the functions
    below for why we serve them ourselves instead of pointing at Cognito).

There is no unauthenticated mode: a request either carries a valid JWT with a
resolved identity, or it ends here with 401/503. The rest of the server only
reads current_label().
"""
import json
import logging
import urllib.request
from contextvars import ContextVar

import boto3
import jwt
from jwt import PyJWKClient

from lib import config, metrics

logger = logging.getLogger(__name__)

# Per-request caller label ("sub:email"). Read by lib.identity.identity().
_CALLER_LABEL: ContextVar[str] = ContextVar("caller_label", default="")


def current_label() -> str:
    return _CALLER_LABEL.get()


_jwks_client = PyJWKClient(config.COGNITO_JWKS_URI)

_PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
_AUTH_SERVER_META_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
)

# e.g. https://xxx.lambda-url.us-east-1.on.aws (MCP_RESOURCE_URL minus /mcp)
_SERVER_BASE = config.MCP_RESOURCE_URL.rsplit("/mcp", 1)[0]


def _protected_resource_metadata() -> bytes:
    """RFC 9728 document: tells Claude which authorization server to use.

    We point authorization_servers at OUR OWN base URL (not Cognito's issuer)
    because Cognito's discovery document omits code_challenge_methods_supported,
    and Claude refuses PKCE flows against servers that don't advertise S256.
    We serve a complete metadata document ourselves (endpoints still Cognito's).
    """
    doc = {
        "resource": config.MCP_RESOURCE_URL,
        "authorization_servers": [_SERVER_BASE],
    }
    return json.dumps(doc).encode()


def _authorization_server_metadata() -> bytes:
    """RFC 8414 / OIDC discovery, proxied-and-augmented for Cognito.

    Cognito supports PKCE but does NOT advertise it in its own discovery
    document. MCP clients (Claude) require code_challenge_methods_supported
    to include S256 and abort the flow otherwise — this augmented document is
    the standard fix (see empires-security/mcp-oauth2-aws-cognito).
    Endpoints point at the real Cognito hosted UI; only metadata is ours.
    """
    doc = {
        "issuer": _SERVER_BASE,
        "authorization_endpoint": f"{config.COGNITO_HOSTED_UI}/oauth2/authorize",
        "token_endpoint": f"{config.COGNITO_HOSTED_UI}/oauth2/token",
        "jwks_uri": config.COGNITO_JWKS_URI,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid", "email", "profile"],
    }
    return json.dumps(doc).encode()


def _check_audience(claims: dict) -> bool:
    """
    Cognito audience check, tolerant of both token types:
      * id token:     token_use == 'id',     aud       == client_id
      * access token: token_use == 'access', client_id == client_id
    """
    want = config.COGNITO_CLIENT_ID
    token_use = claims.get("token_use")
    if token_use == "id":
        return claims.get("aud") == want
    if token_use == "access":
        return claims.get("client_id") == want
    # Unknown token_use: accept if EITHER claim matches (defensive).
    return want in (claims.get("aud"), claims.get("client_id"))


# --- Email resolution (the identity gate) -----------------------------------
# Cognito ACCESS tokens don't carry an email claim (only ID tokens do), and
# Claude typically sends access tokens. The email is REQUIRED for the stable
# readable S3 prefix, so we resolve it through two independent paths and
# refuse the request (503) if both fail. Successes are cached per container;
# failures are NOT cached (a transient error must not pin a container to the
# failing state).
_EMAIL_CACHE: dict = {}
# Pool ID is the last path segment of the issuer URL (…/us-east-1_XXXXXXXX).
_USER_POOL_ID = config.COGNITO_ISSUER.rsplit("/", 1)[-1]
_cognito = boto3.client("cognito-idp", region_name=config.REGION)

# Sentinel: the JWT itself is VALID, but the email could not be resolved right
# now. Middleware turns this into 503 (retry), not 401 (re-auth).
_IDENTITY_PENDING = object()


def _email_from_userinfo(access_token: str):
    """Path 1: hosted UI userInfo. One HTTPS call, the token IS the credential."""
    try:
        req = urllib.request.Request(
            f"{config.COGNITO_HOSTED_UI}/oauth2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            info = json.loads(r.read().decode())
        return info.get("email") or None
    except Exception as e:
        logger.info(f"[auth] userInfo lookup failed: {e}")
        return None


def _email_from_cognito(sub: str):
    """Path 2 (fallback): ask the user pool directly via the execution role.

    Independent of the hosted UI — an AWS control-plane call in-region.
    Requires cognito-idp:ListUsers on the pool (granted in template.yaml).
    """
    try:
        resp = _cognito.list_users(
            UserPoolId=_USER_POOL_ID,
            Filter=f'sub = "{sub}"',
            Limit=1,
        )
        users = resp.get("Users") or []
        if not users:
            logger.warning(f"[auth] ListUsers: no user for sub {sub[:8]}…")
            return None
        for attr in users[0].get("Attributes", []):
            if attr.get("Name") == "email":
                return attr.get("Value") or None
        return None
    except Exception as e:
        logger.warning(f"[auth] Cognito ListUsers fallback failed for {sub[:8]}…: {e}")
        return None


def _resolve_email(sub: str, access_token):
    """Cache -> userInfo -> ListUsers. Returns email or None (gate fails)."""
    if sub in _EMAIL_CACHE:
        return _EMAIL_CACHE[sub]
    email = _email_from_userinfo(access_token) if access_token else None
    if not email:
        email = _email_from_cognito(sub)
    if email:
        _EMAIL_CACHE[sub] = email  # cache SUCCESSES only
    return email


def _validate_jwt(presented: str):
    """
    Validate 'Bearer <jwt>'.
    Returns:
      * "sub:email" label     — valid token, identity resolved;
      * None                  — invalid/missing token  -> 401 (re-auth);
      * _IDENTITY_PENDING     — VALID token but the email could not be
                                resolved right now      -> 503 (retry).
    Verifies signature (JWKS/RS256), issuer, expiry, and audience.
    """
    if not presented.startswith("Bearer "):
        return None
    token = presented[len("Bearer "):].strip()
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.COGNITO_ISSUER,
            # We do audience ourselves (Cognito's split aud/client_id), so
            # tell pyjwt not to enforce 'aud'.
            options={"verify_aud": False},
        )
    except Exception as e:
        logger.info(f"[auth] JWT rejected: {e}")
        return None

    if not _check_audience(claims):
        logger.info("[auth] JWT audience mismatch")
        return None

    sub = claims.get("sub")
    if not sub:
        logger.info("[auth] JWT missing sub")
        return None

    # Identity gate: email is REQUIRED (stable readable S3 prefix).
    email = claims.get("email")  # present on ID tokens (scope email)
    if not email:
        # userInfo only works with an access token; for an ID token the
        # ListUsers fallback inside _resolve_email does the job.
        access_token = token if claims.get("token_use") == "access" else None
        email = _resolve_email(sub, access_token)
    if not email:
        logger.warning(
            f"[auth] email unresolved for sub {sub[:8]}… (userInfo AND "
            "ListUsers failed) — answering 503, client should retry"
        )
        return _IDENTITY_PENDING

    # Label format "sub:email" — identity derives the S3 prefix from it:
    # hash part from sub (stable forever), readable part from email.
    return f"{sub}:{email}"


class CognitoAuthMiddleware:
    """ASGI middleware gating the MCP app: Cognito JWTs, OAuth discovery,
    the email identity gate, and per-request telemetry (lib/metrics)."""

    def __init__(self, app):
        self.app = app

    async def _send_json(self, send, status, body, extra_headers=None):
        headers = [(b"content-type", b"application/json")]
        if extra_headers:
            headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Serve OAuth discovery documents (public, no auth). Claude probes
        # both the bare path and path-suffixed variants (…/mcp), so match by
        # prefix. It may also ask for authorization-server metadata here.
        if path.startswith(_PROTECTED_RESOURCE_PATH):
            await self._send_json(send, 200, _protected_resource_metadata())
            return
        if path.startswith(_AUTH_SERVER_META_PATHS):
            await self._send_json(send, 200, _authorization_server_metadata())
            return

        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"authorization", b"").decode()
        label = _validate_jwt(presented) if presented else None

        if label is None:
            # 401 + WWW-Authenticate so Claude discovers the auth server.
            # Claude honours WWW-Authenticate ONLY on 401 (not 200).
            www = (f'Bearer resource_metadata='
                   f'"{_SERVER_BASE}{_PROTECTED_RESOURCE_PATH}"').encode()
            await self._send_json(
                send, 401, b'{"error":"unauthorized"}',
                extra_headers=[(b"www-authenticate", www)],
            )
            return

        if label is _IDENTITY_PENDING:
            # Valid token, but the email (readable S3 prefix) could not be
            # resolved through EITHER path. Serving the request now would
            # split the user's data across prefixes — refuse and let the
            # client retry. 503 (not 401): re-auth would not help.
            await self._send_json(
                send, 503,
                b'{"error":"identity_unavailable",'
                b'"message":"Could not resolve user identity (email) right now; retry shortly."}',
                extra_headers=[(b"retry-after", b"2")],
            )
            return

        token = _CALLER_LABEL.set(label)
        try:
            await metrics.run_instrumented(self.app, scope, receive, send, label)
        finally:
            _CALLER_LABEL.reset(token)
