"""
Zeropark Report MCP Server
==========================

Thin MCP front-end exposing the zeropark-report-bot backend Lambdas
(ZeroparkGatewayLambda, S3UploadLambda, DuckDBQueryLambda) to the Claude apps.
It reimplements NO report logic — every tool reshapes a request and invokes a
backend Lambda.

Layout:
    lib/     config, auth, identity, invoke, s3, metrics, cache, prompts,
             routing, report_registry                      (infrastructure)
    tools/   one module per tool, each exposing register(mcp)   (behaviour)
    server.py (this file) builds the FastMCP app and mounts everything

Tools (9): get_report_schema, generate_zeropark_report, check_report_status,
cancel_report_generation, query_data, query_data_export,
lookup_feed_or_advertiser, list_s3_reports, about_zeropark_reports — plus the
zeropark_analyst prompt.

Auth: Amazon Cognito OAuth, the ONLY mode. The public Function URL
(AuthType NONE) is fronted by CognitoAuthMiddleware, which validates
Cognito-issued JWTs (both token types), resolves the caller's identity
(sub + email) and sets a per-request ContextVar so every user's reports land
under their own S3 prefix (lib/identity.py). Unauthenticated requests end at
the middleware with 401 — there is no bypass mode and no local entry point.
See lib/auth.py.

Entry point (the only one): `app` below — ASGI, served by the Lambda Web
Adapter (run.sh -> uvicorn server:app). MCP endpoint path: /mcp.
"""
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from lib import config
from lib.auth import CognitoAuthMiddleware
from tools import (
    schema, generate, status, cancel, query, lookup, list_reports, prompt,
)

# stateless_http=True is REQUIRED for serverless: each request is
# self-contained, nothing assumes a long-lived connection or session affinity.
# json_response=True returns plain JSON instead of an SSE stream — SSE
# keep-alives would be broken by Lambda's response buffering anyway.
#
# transport_security: the SDK's streamable-http transport ships DNS-rebinding
# protection that by default only allows localhost Hosts — correct for local
# servers, wrong for a public Lambda Function URL (it would 421 every request
# with "Invalid Host header"). Auth is enforced by the JWT middleware, so we
# allow exactly: our public host (derived from MCP_RESOURCE_URL) and
# 127.0.0.1:8080 / localhost:8080 — the latter two are what the Lambda Web
# Adapter's readiness probe uses in-container; they are not a side entrance
# (the middleware still answers 401 there).
_public_host = config.MCP_RESOURCE_URL.split("//", 1)[-1].split("/", 1)[0]
_security = TransportSecuritySettings(
    allowed_hosts=[_public_host, "127.0.0.1:8080", "localhost:8080"],
    allowed_origins=["https://claude.ai", "https://claude.com"],
)

mcp = FastMCP(
    "zeropark-reports",
    stateless_http=True,
    json_response=True,
    transport_security=_security,
)

# Mount every tool + the analyst prompt. Each module owns its own schema and
# description; server.py only wires them up.
for module in (schema, generate, status, cancel, query, lookup, list_reports, prompt):
    module.register(mcp)

# --- ASGI entry point (Lambda Web Adapter) -----------------------------------
# The Web Adapter runs uvicorn against this app (run.sh); the MCP endpoint is
# served at /mcp. Auth wraps the WHOLE app — discovery documents are the only
# unauthenticated paths, and the middleware serves those itself.
app = CognitoAuthMiddleware(mcp.streamable_http_app())
