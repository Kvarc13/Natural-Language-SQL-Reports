"""auth: the Cognito audience quirk, discovery documents, and the middleware
state machine (401 / 503 / pass-through with label set)."""
import asyncio
import json

import pytest

from lib import auth, config


class TestAudience:
    def test_id_token(self):
        assert auth._check_audience({"token_use": "id", "aud": config.COGNITO_CLIENT_ID})
        assert not auth._check_audience({"token_use": "id", "aud": "someone-else"})

    def test_access_token(self):
        assert auth._check_audience(
            {"token_use": "access", "client_id": config.COGNITO_CLIENT_ID})
        assert not auth._check_audience(
            {"token_use": "access", "client_id": "someone-else"})

    def test_unknown_token_use_accepts_either(self):
        assert auth._check_audience({"aud": config.COGNITO_CLIENT_ID})
        assert auth._check_audience({"client_id": config.COGNITO_CLIENT_ID})
        assert not auth._check_audience({"aud": "x", "client_id": "y"})


class TestDiscoveryDocs:
    def test_protected_resource(self):
        doc = json.loads(auth._protected_resource_metadata())
        assert doc["resource"] == config.MCP_RESOURCE_URL
        assert doc["authorization_servers"] == [auth._SERVER_BASE]
        assert not auth._SERVER_BASE.endswith("/mcp")

    def test_authorization_server_advertises_s256(self):
        doc = json.loads(auth._authorization_server_metadata())
        assert "S256" in doc["code_challenge_methods_supported"]
        assert doc["authorization_endpoint"].startswith(config.COGNITO_HOSTED_UI)
        assert doc["token_endpoint"].startswith(config.COGNITO_HOSTED_UI)
        assert doc["jwks_uri"] == config.COGNITO_JWKS_URI


def _run(mw, scope):
    """Drive the middleware once; return list of sent ASGI messages."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    return sent


def _scope(path="/mcp", headers=None):
    return {"type": "http", "method": "POST", "path": path,
            "headers": headers or []}


@pytest.fixture
def seen():
    """Inner app that records the caller label active during the request."""
    record = {}

    async def app(scope, receive, send):
        record["label"] = auth.current_label()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"jsonrpc":"2.0","id":1,"result":{"content":[]}}'})

    return record, auth.CognitoAuthMiddleware(app)


class TestMiddleware:
    def test_no_token_gets_401_with_discovery_pointer(self, seen):
        _, mw = seen
        sent = _run(mw, _scope())
        start = sent[0]
        assert start["status"] == 401
        www = dict(start["headers"])[b"www-authenticate"].decode()
        assert "resource_metadata=" in www
        assert auth._PROTECTED_RESOURCE_PATH in www

    def test_invalid_token_gets_401(self, seen, monkeypatch):
        _, mw = seen
        monkeypatch.setattr(auth, "_validate_jwt", lambda p: None)
        sent = _run(mw, _scope(headers=[(b"authorization", b"Bearer bad")]))
        assert sent[0]["status"] == 401

    def test_identity_pending_gets_503_with_retry(self, seen, monkeypatch):
        _, mw = seen
        monkeypatch.setattr(auth, "_validate_jwt", lambda p: auth._IDENTITY_PENDING)
        sent = _run(mw, _scope(headers=[(b"authorization", b"Bearer ok")]))
        assert sent[0]["status"] == 503
        assert dict(sent[0]["headers"])[b"retry-after"] == b"2"

    def test_valid_token_passes_with_label_set_and_reset(self, seen, monkeypatch):
        record, mw = seen
        monkeypatch.setattr(auth, "_validate_jwt", lambda p: "sub-1:user@x.com")
        sent = _run(mw, _scope(headers=[(b"authorization", b"Bearer good")]))
        assert sent[0]["status"] == 200
        assert record["label"] == "sub-1:user@x.com"   # visible inside request
        assert auth.current_label() == ""              # reset afterwards

    def test_discovery_paths_are_public(self, seen):
        _, mw = seen
        for path in (auth._PROTECTED_RESOURCE_PATH,
                     auth._PROTECTED_RESOURCE_PATH + "/mcp",
                     "/.well-known/oauth-authorization-server",
                     "/.well-known/openid-configuration"):
            sent = _run(mw, _scope(path=path))
            assert sent[0]["status"] == 200, path
            json.loads(sent[1]["body"])  # valid JSON

    def test_non_http_scope_passes_through(self):
        called = {}

        async def app(scope, receive, send):
            called["yes"] = True

        mw = auth.CognitoAuthMiddleware(app)
        asyncio.run(mw({"type": "lifespan"}, None, None))
        assert called.get("yes")
