"""tools/cancel.py — cancel_report_generation (kills a pending report build).

The /cancel suffix is appended by the BACKEND (Gateway), never taken from the
model — this tool accepts only the exact status_url shape that
generate_zeropark_report issues (same SSRF guard as check_report_status), so
the set of fetchable URLs stays closed.

Ownership caveat (accepted, same class as query_data's data access): a
status_url carries no owner, so any authenticated user can cancel any
correctly-shaped build. Internal tool, low blast radius — a cancelled report
can simply be regenerated.
"""
import json

from lib.invoke import invoke_lambda
from lib import config
from tools.status import _STATUS_URL_RE


def _cancel(status_url: str) -> dict:
    """Ask the Gateway to cancel the build; shape the outcome for the model."""
    res = invoke_lambda(config.GATEWAY_LAMBDA, {
        "action": "cancel_report",
        "status_url": status_url,
    })
    if res.get("status") == "cancelled":
        return {
            "status": "cancelled",
            "status_url": status_url,
            "message": ("Report build cancelled. This status_url is dead — do not "
                        "poll it and do not regenerate unless the user asks."),
        }
    return {
        "status": "error", "stage": "gateway",
        "message": res.get("message", "Unknown gateway error"),
    }


def register(mcp):
    @mcp.tool()
    def cancel_report_generation(status_url: str, report_name: str = "") -> str:
        """Cancel a report that is still building on the reports API.

        Call this when the user no longer wants a pending report: they say to
        cancel/stop, they abandon waiting, or they ask for a different or
        narrower report while this one is still building — in that case cancel
        the old build FIRST, then generate the new one. Never cancel on your
        own initiative; only on the user's explicit or clearly implied decision
        to drop the pending report.

        Pass the exact status_url from the pending result of
        generate_zeropark_report (or check_report_status) — the URL is
        validated and anything other than a reports-api status URL is rejected.

        Returns status='cancelled' on success: the build stops server-side and
        that status_url is dead — do not poll it afterwards. Cancelling a
        report that already finished may return an error from the API; that is
        harmless — if check_report_status already returned success, the CSV is
        on S3 and stays usable.
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
        out = _cancel(status_url)
        if report_name:
            out["report_name"] = report_name
        return json.dumps(out)
