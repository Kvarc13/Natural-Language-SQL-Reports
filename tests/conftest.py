"""Test env. lib/config.py raises on missing required vars BY DESIGN, so the
Cognito wiring must exist in the environment before anything imports lib."""
import os
import sys

os.environ.setdefault(
    "COGNITO_ISSUER",
    "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client-id")
os.environ.setdefault(
    "COGNITO_HOSTED_UI", "https://test.auth.us-east-1.amazoncognito.com")
os.environ.setdefault(
    "MCP_RESOURCE_URL",
    "https://example.lambda-url.us-east-1.on.aws/mcp")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
