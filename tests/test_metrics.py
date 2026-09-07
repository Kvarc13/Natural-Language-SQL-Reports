"""metrics: tool-level outcome classification and request parsing."""
import json

from lib import metrics


def _rpc_result(payload_text=None, is_error=False):
    result = {"content": ([{"type": "text", "text": payload_text}] if payload_text else [])}
    if is_error:
        result["isError"] = True
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


class TestClassifyOutcome:
    def test_auth_statuses(self):
        assert metrics.classify_outcome(401, "", b"")[0] == "auth"
        assert metrics.classify_outcome(403, "", b"")[0] == "auth"

    def test_http_error(self):
        status, detail = metrics.classify_outcome(500, "", b"boom")
        assert status == "error" and "500" in detail

    def test_plain_ok(self):
        body = _rpc_result(json.dumps({"status": "success", "s3_path": "x"}))
        assert metrics.classify_outcome(200, "application/json", body)[0] == "ok"

    def test_jsonrpc_error_object(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "error": {"code": -32000, "message": "bad tool"}}).encode()
        status, detail = metrics.classify_outcome(200, "application/json", body)
        assert status == "tool_error" and detail == "bad tool"

    def test_is_error_flag(self):
        status, detail = metrics.classify_outcome(
            200, "application/json", _rpc_result("exploded", is_error=True))
        assert status == "tool_error" and detail == "exploded"

    def test_in_band_status_error(self):
        payload = json.dumps({"status": "error", "stage": "validation",
                              "message": "range too large"})
        status, detail = metrics.classify_outcome(
            200, "application/json", _rpc_result(payload))
        assert status == "tool_error" and "range too large" in detail

    def test_in_band_error_key(self):
        payload = json.dumps({"error": "Schema not found"})
        status, detail = metrics.classify_outcome(
            200, "application/json", _rpc_result(payload))
        assert status == "tool_error" and "Schema not found" in detail

    def test_in_band_success_payload_is_ok(self):
        payload = json.dumps({"status": "pending", "status_url": "https://..."})
        assert metrics.classify_outcome(
            200, "application/json", _rpc_result(payload))[0] == "ok"

    def test_sse_framing(self):
        rpc = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [
            {"type": "text", "text": "sse fail"}]}}
        sse = f"event: message\ndata: {json.dumps(rpc)}\n\n".encode()
        status, detail = metrics.classify_outcome(200, "text/event-stream", sse)
        assert status == "tool_error" and detail == "sse fail"

    def test_unparsable_body_assumed_ok(self):
        assert metrics.classify_outcome(202, "application/json", b"")[0] == "ok"


class TestExtractRequestInfo:
    def test_tools_call(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "tools/call",
                           "params": {"name": "query_data",
                                      "arguments": {"sql": "SELECT 1"}}}).encode()
        tool, args = metrics.extract_request_info(body)
        assert tool == "query_data" and '"sql"' in args

    def test_args_capped(self):
        body = json.dumps({"method": "tools/call",
                           "params": {"name": "t", "arguments": {"x": "y" * 5000}}}).encode()
        _, args = metrics.extract_request_info(body)
        assert len(args) <= metrics._ARGS_CAP + 1  # +ellipsis

    def test_non_tool_method(self):
        assert metrics.extract_request_info(
            json.dumps({"method": "initialize"}).encode()) == ("initialize", None)

    def test_garbage(self):
        assert metrics.extract_request_info(b"\x00\xff")[0] == "unparsed"
