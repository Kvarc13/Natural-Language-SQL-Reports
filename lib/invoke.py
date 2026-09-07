"""
lib/invoke.py — Synchronous Lambda invocation, shared by all tools.

Uses the SAME boto3 config as the backend's own utils (read_timeout=330, no
retries) because this server calls the exact same backend Lambdas — different
timeouts would make large reports behave differently here than everywhere
else. Retries are DISABLED because they corrupted TCP on long in-VPC
connections (HANDOFF §7.1); the Gateway may poll the Zeropark API for ~75 s
within one invoke.

Contract:
  * NEVER raises. Always returns a dict with a "status" key.
  * A boto3 ReadTimeoutError is returned as {"status": "pending"} — NOT an
    error. The Gateway may still be working when the client-side read timeout
    fires; "pending" lets the caller drive check_report_status instead of
    surfacing a crash. Note: this pending has NO status_url (the Gateway
    hadn't returned yet), whereas a Gateway-returned pending DOES — callers
    that poll must handle both (see tools/generate.py).
  * A Lambda FunctionError (the invoked Lambda raised) becomes
    {"status": "error", "message": ...} rather than propagating.
"""
import json

import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError

import logging

from lib import config

logger = logging.getLogger(__name__)

_lambda = boto3.client(
    "lambda",
    region_name=config.REGION,
    config=Config(
        read_timeout=config.LAMBDA_READ_TIMEOUT,
        connect_timeout=config.LAMBDA_CONNECT_TIMEOUT,
        retries={"max_attempts": 0},
    ),
)


def invoke_lambda(function_name: str, payload: dict) -> dict:
    """Invoke a Lambda synchronously. Returns a dict with 'status'; never raises."""
    logger.info(f"Invoking Lambda: {function_name}")
    try:
        resp = _lambda.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        result = json.loads(resp["Payload"].read().decode("utf-8") or "{}")
        if resp.get("FunctionError"):
            error_msg = result.get("errorMessage", "Unknown Lambda error")
            logger.error(f"Lambda {function_name} FunctionError: {error_msg}")
            return {"status": "error", "message": f"Lambda error: {error_msg}"}
        logger.info(f"Lambda {function_name} -> status: {result.get('status')}")
        return result
    except ReadTimeoutError:
        # Gateway still working when boto3 timed out — surface as pending, not error.
        logger.warning(f"Lambda {function_name} ReadTimeout — treating as pending")
        return {"status": "pending", "message": "boto3 read_timeout"}
    except Exception as e:
        logger.error(f"Lambda invoke error ({function_name}): {e}")
        return {"status": "error", "message": str(e)}
