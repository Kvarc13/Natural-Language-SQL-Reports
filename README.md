# MCP Reports Platform

Self-service reporting for the whole team, driven by conversation with
Claude. An MCP (Model Context Protocol) server exposes a guarded reporting
toolset; three single-purpose backend bridges talk to the reports API,
the S3 datalake and the SQL engine. Everything is serverless, deployed with AWS
SAM, and owned by version-controlled CloudFormation stacks.

```
Claude (web / desktop / mobile)
   │  MCP over HTTPS · OAuth 2.0 + PKCE (Amazon Cognito)
   ▼
MCP server ──────────── Lambda + Function URL, FastMCP, telemetry, validation
   │  invoke-by-name
   ├── api-bridge ───── reports API (in-VPC): generate / status / cancel
   ├── upload-bridge ── streams report CSVs to S3, signs download links
   └── query-bridge ─── read-only DuckDB SQL over S3 CSVs, exports
              │
        S3 datalake (48 h lifecycle) · CloudFront (signed URLs) · Secrets Manager
```

## Components

| Stack | Purpose | Notes |
|---|---|---|
| `MCP` | MCP server: 9 tools + analyst prompt, auth, validation gates, telemetry | Python 3.12, FastMCP, Lambda Web Adapter |
| `api-bridge` | The only Lambda talking to the reports API (VPC endpoint) | stdlib-only; actions: `generate_report`, `check_report_status`, `cancel_report` |
| `upload-bridge` | Streams a finished report CSV into the datalake, returns a CloudFront-signed link | no VPC; vendored signing utils |
| `query-bridge` | Read-only SQL (DuckDB + httpfs) over datalake CSVs; capped inline results and full exports | owns and exports the shared `duckdb-layer` |
| `duckdb-bridge` | General-purpose SQL engine for BI pipelines | pins the layer published by `query-bridge` |

## MCP toolset

`get_report_schema` · `generate_report` · `check_report_status` ·
`cancel_report_generation` · `query_data` · `query_data_export` ·
`lookup_feed_or_advertiser` · `list_s3_reports` · `about_reports`
— plus the `analyst` prompt assembling business rules from S3.

Key behaviours:

- **Validation before the API.** Report names, columns and filters are checked
  against S3-hosted schemas (with an inverted column index suggesting the right
  report on a miss); date ranges are enforced per aggregation.
- **Range guardrails.** Hard per-aggregation caps (never bypassable) and
  confirmation thresholds (heavy-but-legal ranges require an explicit user
  yes) — a lesson from a real incident.
- **Self-pacing polling.** `check_report_status` polls the API every 20 s for
  up to ~80 s inside one call — the model has no clock, so the server owns the
  rhythm. Long builds can be cancelled with `cancel_report_generation`.
- **Read-only SQL.** Only `SELECT`/`WITH`/`SHOW`/`DESCRIBE` reach DuckDB;
  inline results are capped, full exports land on S3 behind signed links.
- **SSRF guard.** Status/cancel URLs must match the exact reports-api shape;
  the `/cancel` suffix is appended server-side only.

## Identity & data isolation

Cognito OAuth (authorization code + PKCE) is the only mode — no bearer tokens,
no local bypass. The server serves its own RFC 9728/8414 discovery documents
(augmenting Cognito's, which omits `code_challenge_methods_supported`). Every
request resolves to `sub:email`; reports land under a stable per-user prefix
`raw_reports/mcp-<email-local>-<hash8(sub)>/<YYYYMMDD>_<hash12>/`, cleaned up by
a 48 h bucket lifecycle. Unresolved identity = 503 + Retry-After, never a
shared fallback prefix.

## Configuration without deploys

Business configuration lives on S3 and hot-reloads on a TTL (default 300 s):

| S3 location | Controls |
|---|---|
| `schemas/reports/_index.json` + `<Report>.json` | available reports, columns, per-report range limits |
| `schemas/config/routing.json` | stream→report mapping, global range limits, redirect rules |
| `schemas/prompts/*.md` | analyst prompt: glossary, core/analysis rules, output format |

Adding a report type = uploading a schema file. Code defaults act only as a
last-resort fallback so guardrails hold even if S3 is unreachable.

## Observability

Every MCP request emits one CloudWatch EMF line (namespace `MCP`):
user, tool, arguments (capped), true tool-level outcome (`ok` / `tool_error` /
`auth` / `error` — parsed from the JSON-RPC body, since MCP reports tool
failures inside HTTP 200) and duration. Dimension sets `[Tool, Status]` and the
`[Status]` rollup feed two alarms (transport errors, tool-error spikes). Logs
Insights answers per-user questions; metrics answer scale questions.

## Repository layout

```
Automations_AWS/
├── MCP/                 # MCP server stack (server.py, lib/, tools/, tests/, Docs/)
├── bi-data-pipeline/
│   ├── bridges/
│   │   ├── api-bridge/
│   │   ├── upload-bridge/
│   │   ├── query-bridge/   # + layers/duckdb_layer(_src)/
│   │   ├── duckdb-bridge/
│   │   └── ...                      # api-bridge, sharepoint bridges (BI pipelines)
│   └── pipeline-engine/
└── pipelines/                    # pipeline definitions (YAML)
```

## Deployment

Each stack deploys independently:

```powershell
cd <stack-dir>
sam build; sam deploy        # confirm_changeset=true — review before applying
```

House rules learned the hard way:

- **Override + Default change together, in one commit** — `samconfig.toml`
  `parameter_overrides` is the operational truth; template `Default:` values
  are documentation and must not lie (CloudFormation ignores defaults on
  updates: unset parameters keep their previous value).
- **Refresh the Claude connector after any MCP tool-set change** — the client
  caches the tool list beyond a single conversation; `sam deploy` alone is not
  enough.
- **Windows/PowerShell:** pass Lambda invoke payloads via `file://payload.json`
  — inline JSON with spaces breaks at the PS/native quoting boundary.
- `.samignore` keeps `schemas/` (contains PII lookup CSVs), `Docs/` and `tests/`
  out of the Lambda package; verify with `ls .aws-sam/build/<Fn>/` after build.

## Testing

`MCP/tests/` — 94 pytest cases covering identity (byte-compatibility
of S3 prefixes with the pre-refactor algorithm), validation gates (date matrix,
column misses, redirect DSL), routing/limit merging, cache semantics, auth
middleware (401/503/happy path over a fake ASGI stack), outcome classification,
lookup CSV parsing (comma-safe first column, truncation notes), status polling
rhythm (fake clock) and the SSRF guard matrix.

## Operations

`DEPLOY_NOTES.md` (MCP) records the behaviour-change inventory and smoke
checklists; `MIGRATION.md` documents the completed decommissioning of the
legacy `report-bot` stack (blue/green, zero downtime) and the two
remaining housekeeping items: adopting the ownerless datalake bucket + signing
secret into a dedicated stack, and the operational docs pass.
