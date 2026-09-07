"""
lib/metrics.py — structured per-request telemetry via CloudWatch EMF.

One JSON line per MCP request answering: WHO (user email), WHAT (tool +
arguments), and DID IT WORK (true tool-level outcome, not just HTTP status).
The auth middleware hands every authenticated request to run_instrumented(),
which wraps the ASGI app — measurement lives here, auth stays auth.

Why response parsing matters: in MCP a FAILED tool call still returns HTTP 200
— the failure travels inside the JSON-RPC body (result.isError / error object).
Judging success by HTTP status alone would count validation rejections and
backend failures as "ok". We therefore classify:

  ok          2xx and the JSON-RPC result carries no error
  tool_error  2xx but the tool reported failure (isError / JSON-RPC error)
  auth        401/403
  error       transport/HTTP failure (5xx etc.)

The line doubles as an EMF metric record: namespace ZeroparkMCP, dimension sets
[Tool, Status] AND [Status]. The Status-only rollup exists so a CloudWatch
alarm can watch "all errors regardless of tool" — EMF only materializes the
dimension sets you declare, so without the rollup an alarm on
Dimensions=[Status=error] would silently match NO metric and never fire.
Cost: a few extra metric series (one per Status value), pennies.

Logs Insights answers per-user questions; the metrics answer scale questions.
"""
import json
import logging
import time

logger = logging.getLogger(__name__)

_NAMESPACE = "ZeroparkMCP"
_ARGS_CAP = 600          # max chars of serialized tool arguments kept in the log
_ERR_CAP = 300           # max chars of error detail
_BODY_CAP = 256 * 1024   # max response bytes buffered for outcome parsing


def extract_request_info(body: bytes):
    """(tool, args_json_or_None) from a JSON-RPC request. Never raises."""
    try:
        msg = json.loads(body or b"{}")
        method = msg.get("method", "unknown")
        if method == "tools/call":
            params = msg.get("params", {})
            tool = params.get("name", "tools/call")
            args = params.get("arguments") or {}
            args_s = json.dumps(args, ensure_ascii=False)
            if len(args_s) > _ARGS_CAP:
                args_s = args_s[:_ARGS_CAP] + "…"
            return tool, args_s
        return method, None
    except Exception:
        return "unparsed", None


def _parse_response_payload(content_type: str, body: bytes):
    """JSON-RPC message dict from a buffered response body, or None.

    Handles both plain JSON (json_response=True) and SSE ("data: {...}" lines),
    since the transport can emit either depending on the request's Accept.
    """
    try:
        text = body.decode("utf-8", errors="replace")
        if "text/event-stream" in content_type:
            datas = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")]
            return json.loads(datas[-1]) if datas else None
        return json.loads(text) if text.strip() else None
    except Exception:
        return None


def classify_outcome(http_status: int, content_type: str, response_body: bytes):
    """(status, error_detail) with tool-level truth, per the module docstring."""
    if http_status in (401, 403):
        return "auth", None
    if not (200 <= http_status < 400):
        return "error", f"http {http_status}"

    msg = _parse_response_payload(content_type, response_body)
    if not isinstance(msg, dict):
        return "ok", None  # non-parsable (e.g. empty 202) — assume transport ok

    if "error" in msg:  # JSON-RPC protocol-level error
        detail = str(msg["error"].get("message", msg["error"]))[:_ERR_CAP]
        return "tool_error", detail

    result = msg.get("result") or {}
    if result.get("isError"):  # MCP tool signalled failure in-band
        detail = ""
        for c in result.get("content", []):
            if c.get("type") == "text":
                detail = c.get("text", "")[:_ERR_CAP]
                break
        return "tool_error", detail or "tool reported isError"

    # Graceful in-tool failures: this codebase's tools catch exceptions and
    # return a JSON payload instead of raising — either {"error": "..."}
    # (schema) or {"status": "error", ...} (generate/status/upload/invoke).
    # Detect those so validation rejections and backend failures don't count
    # as "ok". Only small payloads are parsed (error JSONs are short; big
    # query-result tables are skipped by the size guard).
    for c in result.get("content", []):
        if c.get("type") != "text":
            continue
        text = (c.get("text") or "").strip()
        if len(text) < 4096 and text.startswith("{"):
            try:
                payload = json.loads(text)
            except Exception:
                break
            if isinstance(payload, dict) and (
                payload.get("status") == "error" or "error" in payload
            ):
                detail = str(payload.get("message") or payload.get("error") or text)[:_ERR_CAP]
                return "tool_error", detail
        break
    return "ok", None


def emit(user_label: str, tool: str, args: str, status: str, http_status: int,
         duration_ms: float, error: str | None = None) -> None:
    """Print one EMF line; stdout of Lambda lands in CloudWatch Logs."""
    user = user_label.split(":", 1)[1] if ":" in user_label else (user_label or "anonymous")
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": _NAMESPACE,
                # Two dimension sets: per-tool detail AND a Status-only rollup.
                # The rollup is what template.yaml's error alarm watches — EMF
                # does not synthesize aggregate dimensions on its own.
                "Dimensions": [["Tool", "Status"], ["Status"]],
                "Metrics": [
                    {"Name": "Requests", "Unit": "Count"},
                    {"Name": "DurationMs", "Unit": "Milliseconds"},
                ],
            }],
        },
        "Tool": tool,
        "Status": status,
        "Requests": 1,
        "DurationMs": round(duration_ms, 1),
        "user": user,
        "http_status": http_status,
    }
    if args:
        doc["args"] = args
    if error:
        doc["error"] = error[:_ERR_CAP]
    try:
        print(json.dumps(doc, ensure_ascii=False))
    except Exception as e:  # telemetry must never break a request
        logger.info(f"[metrics] emit failed: {e}")


async def run_instrumented(app, scope, receive, send, user_label: str) -> None:
    """Run the ASGI app for one request, then emit one EMF line.

    Only /mcp POSTs are measured — everything else passes straight through.
    The request body is buffered (MCP requests are small JSON) to read the
    tool name + arguments, then replayed downstream unchanged. The response
    body is buffered (capped at _BODY_CAP) because MCP reports tool failures
    INSIDE a 200 — true success needs the payload, not the code. Telemetry
    failures never affect the request.
    """
    path = scope.get("path", "")
    method = scope.get("method", "")
    if not (method == "POST" and path.rstrip("/").endswith("/mcp")):
        await app(scope, receive, send)
        return

    # Buffer the request body to read the JSON-RPC method/tool + arguments.
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg)
        if msg.get("type") != "http.request" or not msg.get("more_body"):
            break
    body = b"".join(c.get("body", b"") for c in chunks if c.get("type") == "http.request")
    tool, args = extract_request_info(body)

    replay = list(chunks)

    async def replay_receive():
        if replay:
            return replay.pop(0)
        return await receive()

    # Capture status + response body (capped) for classify_outcome.
    cap = {"status": 0, "ctype": "", "buf": b"", "over": False}

    async def counting_send(msg):
        if msg.get("type") == "http.response.start":
            cap["status"] = msg.get("status", 0)
            for k, v in msg.get("headers") or []:
                if k.lower() == b"content-type":
                    cap["ctype"] = v.decode("latin-1")
                    break
        elif msg.get("type") == "http.response.body" and not cap["over"]:
            cap["buf"] += msg.get("body", b"")
            if len(cap["buf"]) > _BODY_CAP:
                cap["buf"] = cap["buf"][:_BODY_CAP]
                cap["over"] = True
        await send(msg)

    started = time.monotonic()
    crashed = None
    try:
        await app(scope, replay_receive, counting_send)
    except Exception as e:
        crashed = repr(e)
        raise
    finally:
        if crashed:
            status, detail = "error", crashed
        else:
            status, detail = classify_outcome(
                cap["status"] or 500, cap["ctype"], cap["buf"])
        emit(user_label, tool, args, status, cap["status"] or 500,
             (time.monotonic() - started) * 1000.0, detail)
