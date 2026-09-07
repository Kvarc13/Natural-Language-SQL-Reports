"""
lib/config.py — Central configuration for the Zeropark MCP server.

Everything comes from environment variables set by template.yaml — the
template is the single source of truth for deployment values. The values the
server cannot run without (the Cognito wiring and its own public URL) have NO
code defaults: a missing one raises at import, so a misdeployed function fails
its first invoke loudly instead of starting half-configured or, worse, open.
Numeric tunables keep safe defaults. Nothing here hits the network; import
side effects are limited to reading os.environ.
"""
import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name} — set it via "
            "template.yaml. This server has no unauthenticated or default mode."
        )
    return value


# --- S3 / backend resources -------------------------------------------------
S3_BUCKET = os.environ.get("S3_BUCKET", "zeropark-reports-datalake")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# The three backend Lambdas this server orchestrates. Referenced by name; this
# server never recreates them (each bridge owns its own stack). The template
# passes the deployed names in as env vars; these defaults must match them.
GATEWAY_LAMBDA = os.environ.get("ZEROPARK_GATEWAY_LAMBDA", "zeropark-api-bridge")
S3_UPLOAD_LAMBDA = os.environ.get("S3_UPLOAD_LAMBDA", "zeropark-upload-bridge")
DUCKDB_LAMBDA = os.environ.get("DUCKDB_QUERY_LAMBDA", "zeropark-query-bridge")

# --- S3 key layout (must match the backend Lambdas exactly) -----------------
SCHEMAS_REPORTS_PREFIX = "schemas/reports"      # {report}.json + _index.json
SCHEMAS_LOOKUP_PREFIX = "schemas/lookup"        # PublisherFeeds.csv, Users.csv
SCHEMAS_PROMPTS_PREFIX = "schemas/prompts"      # *.md business docs
SCHEMAS_CONFIG_PREFIX = "schemas/config"        # routing.json (editable rules)
RAW_REPORTS_PREFIX = "raw_reports"              # generated CSVs, 48h TTL

# --- Auth: Amazon Cognito OAuth (the ONLY mode) ------------------------------
# Cognito quirk (see lib/auth.py): ID tokens carry 'aud'=client_id, access
# tokens carry 'client_id' (no 'aud'). Validation accepts either, keyed on
# token_use.
COGNITO_ISSUER = _required("COGNITO_ISSUER")
COGNITO_CLIENT_ID = _required("COGNITO_CLIENT_ID")

# Hosted UI domain: the actual authorize/token endpoints live here. Needed
# because we serve our own authorization-server metadata (Cognito's discovery
# omits code_challenge_methods_supported, which Claude requires for PKCE).
COGNITO_HOSTED_UI = _required("COGNITO_HOSTED_UI")

# The public URL of THIS MCP server (its own /mcp endpoint). Required by the
# OAuth protected-resource metadata (RFC 9728): the 'resource' field must
# equal it exactly.
MCP_RESOURCE_URL = _required("MCP_RESOURCE_URL")

# For Cognito the JWKS document always lives at a fixed path under the issuer —
# derived, not configured, so the two can never drift apart.
COGNITO_JWKS_URI = f"{COGNITO_ISSUER}/.well-known/jwks.json"

# --- Prompt docs cache TTL ----------------------------------------------------
# S3-backed docs/config (prompts, routing rules, report schemas) are cached per
# container and reloaded once this many seconds pass (see lib/cache.py). Set to
# 0 to cache for the whole container lifetime (edits then need a cold start).
PROMPT_CACHE_TTL_SEC = int(os.environ.get("PROMPT_CACHE_TTL_SEC", "300"))

# --- boto3 Lambda client tuning ----------------------------------------------
# Mirrors the backend's own utils EXACTLY. The Gateway polls the Zeropark API
# in-VPC for up to ~75s; read_timeout must sit above that. Retries are DISABLED
# because they corrupted TCP on long VPC connections (HANDOFF §7.1). A
# ReadTimeout is treated as "pending", not an error (see lib/invoke.py).
LAMBDA_READ_TIMEOUT = int(os.environ.get("LAMBDA_READ_TIMEOUT", "330"))
LAMBDA_CONNECT_TIMEOUT = int(os.environ.get("LAMBDA_CONNECT_TIMEOUT", "10"))
