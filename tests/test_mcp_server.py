"""Step 43：MCP stdio 服务器（JSON-RPC 2.0 over stdio）。"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from datasentry.mcp_server import McpServer


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    _write_csv(
        path,
        ["id", "amount"],
        [[1, 10.0], [2, 20.0], [2, 30.0], [None, None]],
    )
    return path


def _call(server: McpServer, message_id: int, method: str, params: dict | None = None) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    response = server._handle_message(message)
    assert response is not None
    return response


class TestHandshake:
    def test_initialize(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 1, "initialize")
            assert response["result"]["protocolVersion"] == "2024-11-05"
            assert response["result"]["serverInfo"]["name"] == "datasentry"
        finally:
            server.close()

    def test_initialized_notification_no_response(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            assert (
                server._handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
                is None
            )
        finally:
            server.close()

    def test_ping(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            assert _call(server, 2, "ping")["result"] == {}
        finally:
            server.close()

    def test_unknown_method_error(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 3, "nope")
            assert response["error"]["code"] == -32601
        finally:
            server.close()


class TestTools:
    def test_tools_list_shape(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(server, 4, "tools/list")["result"]
            tools = result["tools"]
            names = {t["name"] for t in tools}
            assert {
                "scan_file",
                "list_issues",
                "quality_score",
                "drift_compare",
                "drift_latest",
                "detectors_list",
                "contract_validate",
                "jobs_list",
                "job_create",
                "job_trigger",
                "trends_list",
                "profiles_get",
                "comparison_build",
            } == names
            for tool in tools:
                assert tool["inputSchema"]["type"] == "object"
        finally:
            server.close()

    def test_scan_file_tool(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                5,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )
            text = response["result"]["content"][0]["text"]
            payload = json.loads(text)
            assert payload["status"] == "completed"
            assert payload["row_count"] == 4
            assert payload["total_issues"] >= 1
            assert "scan_run_id" in payload
        finally:
            server.close()

    def test_list_issues_tool(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            _call(
                server,
                6,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )
            response = _call(server, 7, "tools/call", {"name": "list_issues", "arguments": {}})
            issues = json.loads(response["result"]["content"][0]["text"])
            assert issues
            assert all("issue_type" in i and "severity" in i for i in issues)
        finally:
            server.close()

    def test_detectors_list_tool(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 8, "tools/call", {"name": "detectors_list", "arguments": {}})
            detectors = json.loads(response["result"]["content"][0]["text"])
            assert len(detectors) == 39
            ids = {d["detector_id"] for d in detectors}
            assert "foreign_key_violation" in ids
            assert "model_outlier" in ids
        finally:
            server.close()

    def test_unknown_tool_error(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 9, "tools/call", {"name": "ghost", "arguments": {}})
            assert response["error"]["code"] == -32602
        finally:
            server.close()

    def test_tool_exception_maps_to_error(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                10,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(tmp_path / "nope.csv")}},
            )
            assert response["error"]["code"] == -32603
        finally:
            server.close()


class TestDataSurfaceTools:
    def test_trends_list_empty(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 11, "tools/call", {"name": "trends_list", "arguments": {}})
            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload == {"trends": [], "count": 0}
        finally:
            server.close()

    def test_trends_list_and_filter(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            _call(
                server,
                12,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )
            response = _call(server, 13, "tools/call", {"name": "trends_list", "arguments": {}})
            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload["count"] == 1
            trend = payload["trends"][0]
            assert trend["dataset_id"] == "orders"
            assert trend["latest_score"] is not None
            assert trend.get("points")
            assert trend["points"][0]["run_id"].startswith("scan_")
            filtered = json.loads(
                _call(
                    server,
                    14,
                    "tools/call",
                    {"name": "trends_list", "arguments": {"dataset_id": "nope"}},
                )["result"]["content"][0]["text"]
            )
            assert filtered == {"trends": [], "count": 0}
        finally:
            server.close()

    def test_profiles_get_present_and_missing(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            scan = json.loads(
                _call(
                    server,
                    15,
                    "tools/call",
                    {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
                )["result"]["content"][0]["text"]
            )
            run_id = scan["scan_run_id"]
            response = _call(
                server,
                16,
                "tools/call",
                {"name": "profiles_get", "arguments": {"scan_run_id": run_id}},
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload["ok"] is True
            assert "column_profiles" in payload["profile"]
            missing = json.loads(
                _call(
                    server,
                    17,
                    "tools/call",
                    {"name": "profiles_get", "arguments": {"scan_run_id": "run_nope"}},
                )["result"]["content"][0]["text"]
            )
            assert missing["ok"] is False
            assert "not found" in missing["error"]
        finally:
            server.close()

    def test_comparison_build(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            first = json.loads(
                _call(
                    server,
                    18,
                    "tools/call",
                    {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
                )["result"]["content"][0]["text"]
            )
            single = json.loads(
                _call(
                    server,
                    19,
                    "tools/call",
                    {
                        "name": "comparison_build",
                        "arguments": {
                            "dataset_id": "orders",
                            "current_run_id": first["scan_run_id"],
                        },
                    },
                )["result"]["content"][0]["text"]
            )
            assert single == {"ok": True, "comparison": None}

            second = json.loads(
                _call(
                    server,
                    20,
                    "tools/call",
                    {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
                )["result"]["content"][0]["text"]
            )
            multi = json.loads(
                _call(
                    server,
                    21,
                    "tools/call",
                    {
                        "name": "comparison_build",
                        "arguments": {
                            "dataset_id": "orders",
                            "current_run_id": second["scan_run_id"],
                        },
                    },
                )["result"]["content"][0]["text"]
            )
            assert multi["ok"] is True
            assert multi["comparison"] is not None
            assert len(multi["comparison"]) == 2
            assert multi["comparison"][-1]["current"] is True
            assert multi["comparison"][1]["delta"] is not None
        finally:
            server.close()


class TestStdioLoop:
    def test_serve_stdio_real_process(self, tmp_path: Path, sample_csv: Path) -> None:
        workspace = tmp_path / "ws"
        proc = subprocess.Popen(
            [sys.executable, "-m", "datasentry.cli", "mcp", "--project", str(workspace)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
            proc.stdin.flush()
            init_line = proc.stdout.readline()
            init = json.loads(init_line)
            assert init["result"]["serverInfo"]["name"] == "datasentry"

            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "scan_file",
                            "arguments": {"path": str(sample_csv)},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()
            scan_line = proc.stdout.readline()
            scan = json.loads(scan_line)
            payload = json.loads(scan["result"]["content"][0]["text"])
            assert payload["status"] == "completed"
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)


class TestJobsTools:
    def test_tools_list_includes_jobs(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(server, 4, "tools/list")["result"]
            names = {t["name"] for t in result["tools"]}
            assert {"jobs_list", "job_create", "job_trigger"} <= names
        finally:
            server.close()

    def test_job_create_and_trigger(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            text = _call(
                server,
                5,
                "tools/call",
                {
                    "name": "job_create",
                    "arguments": {
                        "name": "nightly",
                        "path": str(sample_csv),
                        "cron": "0 9 * * *",
                        "gate_quality_min": 50.0,
                    },
                },
            )["result"]["content"][0]["text"]
            created = json.loads(text)
            assert created["ok"] is True
            job_id = created["job"]["job_id"]
            assert created["job"]["gate_quality_min"] == 50.0

            listed = json.loads(
                _call(server, 6, "tools/call", {"name": "jobs_list", "arguments": {}})["result"][
                    "content"
                ][0]["text"]
            )
            assert job_id in {j["job_id"] for j in listed}

            triggered = json.loads(
                _call(
                    server,
                    7,
                    "tools/call",
                    {"name": "job_trigger", "arguments": {"job_id": job_id}},
                )["result"]["content"][0]["text"]
            )
            assert triggered["ok"] is True
            assert triggered["run"]["status"] == "completed"
        finally:
            server.close()

    def test_job_create_invalid_cron(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(
                server,
                8,
                "tools/call",
                {
                    "name": "job_create",
                    "arguments": {
                        "name": "bad",
                        "path": "x.csv",
                        "cron": "61 * * * *",
                    },
                },
            )["result"]["content"][0]["text"]
            result = json.loads(result)
            assert result["ok"] is False
            assert "invalid cron" in result["error"]
        finally:
            server.close()

    def test_job_trigger_unknown(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(
                server,
                9,
                "tools/call",
                {"name": "job_trigger", "arguments": {"job_id": "nope"}},
            )["result"]["content"][0]["text"]
            result = json.loads(result)
            assert result["ok"] is False
        finally:
            server.close()
