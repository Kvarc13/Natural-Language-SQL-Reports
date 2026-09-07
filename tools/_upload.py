"""
tools/_upload.py — Step 2 of report generation: stream the Gateway's csv_url to S3.

Shared by generate_zeropark_report (synchronous success) and check_report_status
(async success). Invokes zeropark-upload-bridge, which streams the CSV into
raw_reports/{user_id}/{thread_ts}/{report}_{HHMMSS}.csv — the path query_data reads.

Returns a metadata-ONLY JSON string: never numeric report data. Any figure the
model states must come from a query_data call, not from this result.
"""
import json

from lib.identity import identity
from lib.invoke import invoke_lambda
from lib import config


def upload_csv(csv_url: str, report_name: str) -> str:
    user_id, thread_ts = identity()
    up = invoke_lambda(config.S3_UPLOAD_LAMBDA, {
        "action": "upload",
        "csv_url": csv_url,
        "report_name": report_name,
        "user_id": user_id,
        "thread_ts": thread_ts,
    })
    if up.get("status") != "success":
        return json.dumps({
            "status": "error",
            "stage": "s3_upload",
            "message": up.get("message", "Unknown upload error"),
        })
    # Metadata only — strip any download_url/keys; querying is via s3_path.
    return json.dumps({
        "status": "success",
        "s3_path": up.get("s3_path"),
        "row_count": up.get("row_count"),
        "columns": up.get("columns"),
        "report_name": report_name,
    })
