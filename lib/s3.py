"""
lib/s3.py — Thin S3 helpers shared by tools and lib modules.

Kept deliberately small: a text getter that never raises (returns "" on any
miss), a raw getter that distinguishes "not found" for schema lookups, and a
paginated CSV lister for list_s3_reports. All reads use the execution role —
no keys.

list_csv paginates because a caller's prefix can hold more than one page of
objects (~48 h of reports, and one splitting session can add 15+ exports);
max_keys caps the TOTAL number of returned entries across pages as a safety
valve — the 48 h bucket lifecycle keeps real listings far below it.
"""
import boto3

import logging

from lib import config

logger = logging.getLogger(__name__)

_s3 = boto3.client("s3", region_name=config.REGION)


def get_text(key: str) -> str:
    """Return the object body as text, or '' if missing/unreadable. Never raises."""
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        logger.info(f"[s3] get_text miss for {key}: {e}")
        return ""


def get_schema_json(report_name: str) -> tuple[str, bool]:
    """Return (schema_json_text, found). found=False specifically on NoSuchKey."""
    key = f"{config.SCHEMAS_REPORTS_PREFIX}/{report_name}.json"
    try:
        obj = _s3.get_object(Bucket=config.S3_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8"), True
    except _s3.exceptions.NoSuchKey:
        return "", False
    except Exception as e:
        logger.error(f"[s3] schema read error for {key}: {e}")
        return "", False


def list_csv(prefix: str, max_keys: int = 1000) -> list[dict]:
    """List .csv objects under a prefix, paginated. Returns [] on error.

    max_keys caps the TOTAL number of returned entries (across pages) as a
    safety valve; the 48 h lifecycle keeps real listings far below it.
    """
    try:
        out = []
        paginator = _s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.S3_BUCKET, Prefix=prefix):
            for o in page.get("Contents", []):
                key = o["Key"]
                if not key.endswith(".csv"):
                    continue
                out.append({
                    "key": key,
                    "filename": key.rsplit("/", 1)[-1],
                    "size_kb": round(o["Size"] / 1024, 1),
                    "last_modified": o["LastModified"].isoformat(),
                })
                if len(out) >= max_keys:
                    logger.warning(f"[s3] list_csv hit cap ({max_keys}) for {prefix}")
                    return out
        return out
    except Exception as e:
        logger.error(f"[s3] list_csv error for {prefix}: {e}")
        return []
