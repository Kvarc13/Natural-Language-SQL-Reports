"""tools/status.py — check_report_status (drives polling for large reports).

The polling RHYTHM lives here, not in the model: an LLM has no clock, so
"wait 30-60s between calls" degenerates into back-to-back requests. Instead
one tool call polls the Gateway every _POLL_INTERVAL_SEC for up to
_POLL_BUDGET_SEC, sleeping in between, and only then returns pending. The
model may call again immediately — real time still passes inside each call.
Trade-off (accepted): a sleeping call holds one ReservedConcurrency slot for
up to the budget; at this deployment's scale (cap 10, ~30 users) that is
comfortable, and each in-loop Gateway invoke is a cheap one-shot check.
"""
import json
import re
import time

from lib.invoke import invoke_lambda
from lib import config
from tools._upload import upload_csv

# status_url is forwarded to the Gateway, which FETCHES it. Without a shape
# check this is an SSRF primitive: a prompt-injected model could hand us any
# URL (metadata endpoints, internal services) and the backend would GET it.
# Only the reports-api status URL shape issued by generate_zeropark_report is
# legal — exact host, exact path, a UUID, nothing else (no ports, queries,
# fragments, userinfo, or trailing segments).
_STATUS_URL_RE = re.compile(
    r"^https?://reports-api\.zeropark\.codewise\.com/report/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_POLL_INTERVAL_SEC = 20   # spacing between status checks inside ONE tool call
_POLL_BUDGET_SEC = 80     # max in-call polling time before returning pending


def _poll(status_url: str) -> dict:
    """Check the report status, re-checking every _POLL_INTERVAL_SEC until it
    leaves 'pending' or the in-call budget runs out. Returns the Gateway's
    final response dict (its 'status' is still 'pending' on budget exhaustion).
    """
    deadline = time.monotonic() + _POLL_BUDGET_SEC
    while True:
        res = invoke_lambda(config.GATEWAY_LAMBDA, {
            "action": "check_report_status",
            "status_url": status_url,
        })
        if res.get("status") != "pending":
            return res
        if time.monotonic() + _POLL_INTERVAL_SEC > deadline:
            return res
        time.sleep(_POLL_INTERVAL_SEC)


def register(mcp):
    @mcp.tool()
    def check_report_status(status_url: str, report_name: str) -> str:
        """Check whether a pending report is ready; if so, store it on S3.

        Call this ONLY after generate_zeropark_report returned status='pending' WITH
        a status_url. Pass the exact status_url and report_name from that result —
        the URL is validated and anything other than a reports-api status URL is
        rejected.

        This tool paces its own polling: one call keeps checking the report for
        up to ~80s (every 20s) before answering. You never need to wait between
        calls — if it returns pending, simply call it again immediately with
        the SAME status_url.

        Behaviour:
          - Still building after this call's polling window -> status='pending'.
            Call again with the SAME status_url. Do NOT regenerate.
          - Ready -> runs the S3 upload and returns status='success' with s3_path,
            row_count, columns (metadata only — query_data for any number).
          - Failure -> status='error'.

        Keep calling until success or error. If it's still pending after several
        calls (many minutes of build time), tell the user the report is unusually
        large — they can keep waiting, retry later, or cancel it
        (cancel_report_generation).
        """
        if not _STATUS_URL_RE.match(str(status_url or "").strip()):
            return json.dumps({
                "status": "error",
                "stage": "validation",
                "message": (
                    "status_url is not a valid reports-api status URL. Pass the "
                    "EXACT status_url returned by generate_zeropark_report "
                    "(http://reports-api.zeropark.codewise.com/report/<uuid>); "
                    "no other URL is accepted."
                ),
            })

        res = _poll(status_url)
        status = res.get("status")
        if status == "pending":
            return json.dumps({
                "status": "pending",
                "status_url": status_url,
                "report_name": report_name,
                "message": (
                    "Still building after this call's ~80s polling window. Call "
                    "check_report_status again with the same status_url — the tool "
                    "paces its own polling, no need to wait between calls. If the user no "
                    "longer wants this report, call cancel_report_generation instead."
                ),
            })
        if status != "success":
            return json.dumps({
                "status": "error", "stage": "gateway",
                "message": res.get("message", "Unknown gateway error"),
            })
        return upload_csv(res.get("csv_url", ""), report_name)
