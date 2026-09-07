"""tools/list_reports.py — list_s3_reports (all CSVs stored under the caller's prefix).

v1.1 simplifications (rationale in review notes):
  * report_type parameter REMOVED. The scope is one user's files within the
    48 h S3 lifecycle — a handful of CSVs whose filenames already start with
    the report name, so a server-side type filter added a hardcoded name map
    (_TYPE_MAP, already stale vs _index.json) for zero benefit.
  * Visibility widened from the CURRENT DAY segment to the WHOLE user prefix.
    The daily thread segment changes at UTC midnight, which used to hide a
    report generated at 23:50 ten minutes later — even though the file stays
    on S3 for ~48 h and query_data reads it fine by full s3_path. The daily
    segment remains the WRITE location (folder hygiene); it is no longer a
    visibility boundary. The 48 h bucket lifecycle keeps this list short by
    construction.
"""
import json

from lib.identity import identity
from lib.s3 import list_csv
from lib import config


def register(mcp):
    @mcp.tool()
    def list_s3_reports() -> str:
        """List report CSVs already generated for you (last ~48h, newest first).

        Use this to find the s3_path of a report you generated earlier — today
        OR yesterday — so you can query it with query_data instead of
        regenerating. Filenames start with the report name (e.g.
        'MasterReportRow_...', 'TimeRevenueStreamRow_...'), so pick by name.

        Returns each file's s3_path, filename, size, and last_modified. Files
        expire after ~48h (S3 lifecycle); anything older is gone — regenerate
        if needed. An empty list means nothing is stored yet: generate a
        report first.
        """
        user_id, _ = identity()
        prefix = f"{config.RAW_REPORTS_PREFIX}/{user_id}/"
        files = list_csv(prefix)
        if not files:
            return json.dumps({"files": [], "message": "No reports stored for you yet."})

        files.sort(key=lambda x: x["last_modified"], reverse=True)
        out = [{
            "s3_path": f"s3://{config.S3_BUCKET}/{f['key']}",
            "filename": f["filename"],
            "size_kb": f["size_kb"],
            "last_modified": f["last_modified"],
        } for f in files]
        return json.dumps({"files": out, "count": len(out)})
