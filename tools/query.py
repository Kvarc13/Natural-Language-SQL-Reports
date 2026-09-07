"""tools/query.py — query_data (inline) and query_data_export (full CSV + link)."""
import json

from lib.identity import identity
from lib.invoke import invoke_lambda
from lib import config


def register(mcp):
    @mcp.tool()
    def query_data(sql: str) -> str:
        """Run read-only DuckDB SQL on report CSVs already on S3 (inline, <=50 rows).

        This is the ONLY valid source of numbers in an answer — call it before stating
        any figure. Use for previews, top-N, SUM/AVG/COUNT, GROUP BY, sorting,
        filtering, period comparisons, and JOINs between two stored reports. Allowed:
        SELECT, WITH, SHOW, DESCRIBE.

        Read files with read_csv_auto('s3://<bucket>/raw_reports/...', all_varchar=true).
        ALWAYS pass all_varchar=true — the report CSVs have empty strings in numeric
        columns, and without it DuckDB throws 'Cannot mix VARCHAR and BIGINT' or
        'Could not convert string to INT64'. CAST when aggregating, e.g.
        CAST(PROFIT AS DOUBLE).

        Column traps: there is NO REVENUE or COST column — use ADVERTISER_COST,
        PUBLISHER_PAYOUT; PROFIT = ADVERTISER_COST - PUBLISHER_PAYOUT; MARGIN =
        PROFIT / ADVERTISER_COST. DuckDB is case-sensitive on column names — use the
        exact header names from the report. If the tool returns an error, report it —
        never invent fallback data.
        """
        return json.dumps(invoke_lambda(config.DUCKDB_LAMBDA, {"action": "query", "sql": sql}))

    @mcp.tool()
    def query_data_export(sql: str, export_name: str = "query_result") -> str:
        """Like query_data but writes the FULL result to a new CSV on S3.

        Use when the result exceeds ~50 rows, for JOINs of two reports, splitting a
        report into per-value files, or when the user wants to download full data.
        export_name is a short label (no extension), e.g. 'brands_comparison_vs_30d'.
        Same SQL rules as query_data (all_varchar=true, no REVENUE/COST column).

        Returns row_count, columns, a preview, filename, and download_url (a presigned
        link, valid ~24h). Give the user a short summary (row count) AND the link
        rendered as a Markdown link: [filename](download_url), using the filename and
        download_url fields EXACTLY as returned — do not edit, shorten, or re-type the
        URL; the signature breaks if a single character changes. The link is a bearer
        credential (anyone with it can download for 24h), so don't paste it anywhere
        public.
        """
        user_id, thread_ts = identity()
        result = invoke_lambda(config.DUCKDB_LAMBDA, {
            "action": "query_export",
            "sql": sql,
            "export_name": export_name,
            "user_id": user_id,
            "thread_ts": thread_ts,
        })
        # Derive a clean filename for the Markdown link; keep s3_key internal.
        if isinstance(result, dict):
            s3_key = result.get("s3_key", "")
            if s3_key:
                result["filename"] = s3_key.rsplit("/", 1)[-1]
            result.pop("s3_key", None)
        return json.dumps(result)
