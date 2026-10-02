"""Step 43：MCP stdio 服务器（JSON-RPC 2.0 over stdio）。"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

from datasentry.mcp_server import McpServer


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / "orders.csv"
    _write_csv(
        path,
        ["id", "amount"],
        [[1, 10.0], [2, 20.0], [2, 30.0], [None, None]],
    )
    return path


MUTATING_TOOLS = [
    "job_create",
    "job_remove",
    "job_trigger",
    "job_update",
    "pii_delete_session",
    "pii_purge_sessions",
    "pii_restore",
    "pii_rotate_key",
    "repair_apply_batch",
    "repair_rollback_batch",
]


def _with_confirmation(params: dict | None) -> dict | None:
    """Pre-existing behavioural tests opt into the D5-12 guard here, in one place.

    The guard itself is asserted by `TestConfirmationGate`, which calls `_handle_message`
    directly; every other test here is about what a tool does once confirmation was given, so
    injecting it centrally keeps those tests meaningful without editing 16 call sites.
    """
    if not isinstance(params, dict) or params.get("name") not in MUTATING_TOOLS:
        return params
    arguments = dict(params.get("arguments") or {})
    arguments.setdefault("confirm", True)
    if params.get("name") == "repair_apply_batch":
        arguments.setdefault("preview_only", False)
    return {**params, "arguments": arguments}


def _call(server: McpServer, message_id: int, method: str, params: dict | None = None) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = _with_confirmation(params)
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
                "job_update",
                "job_remove",
                "trends_list",
                "profiles_get",
                "comparison_build",
                "pii_sessions",
                "pii_restore",
                "pii_delete_session",
                "pii_rotate_key",
                "pii_purge_sessions",
                "repair_propose_batch",
                "repair_apply_batch",
                "repair_rollback_batch",
                "repair_verify",
                "report_export",
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

    def test_repair_propose_apply_rollback_batch_tools(self, tmp_path: Path) -> None:
        """V39：MCP 批量修复三工具——propose（只读）→ apply（副本）→ rollback。"""
        (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
        dirty = tmp_path / "ws" / "dirty.csv"
        _write_csv(
            dirty,
            ["name", "status", "price"],
            [[" alice ", "Active", 10], ["bob", "n/a", 9999], ["carol", "inactive", 250]],
        )
        server = McpServer(project=tmp_path / "ws")
        try:
            scan = _call(
                server,
                1,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(dirty)}},
            )["result"]["content"][0]["text"]
            scan_run_id = json.loads(scan)["scan_run_id"]

            proposed = _call(
                server,
                2,
                "tools/call",
                {
                    "name": "repair_propose_batch",
                    "arguments": {"scan_run_id": scan_run_id, "source_path": str(dirty)},
                },
            )["result"]["content"][0]["text"]
            data = json.loads(proposed)
            assert "errors" in data and data["failed"] == 0
            assert any(x["proposed"] for x in data["issues"])

            applied = _call(
                server,
                3,
                "tools/call",
                {
                    "name": "repair_apply_batch",
                    "arguments": {"scan_run_id": scan_run_id, "source_path": str(dirty)},
                },
            )["result"]["content"][0]["text"]
            app = json.loads(applied)
            assert app["failed"] == 0
            run_ids = [x["run_id"] for x in app["applied"] if x.get("applied")]
            assert run_ids, "at least one applied run expected"

            verified = _call(
                server,
                4,
                "tools/call",
                {"name": "repair_verify", "arguments": {"repair_run_id": run_ids[0]}},
            )["result"]["content"][0]["text"]
            vr = json.loads(verified)
            assert vr.get("verify_scan_run_id")
            assert vr["verify_issue_count"] < vr["source_issue_count"]

            rolled = _call(
                server,
                5,
                "tools/call",
                {"name": "repair_rollback_batch", "arguments": {"repair_run_ids": run_ids}},
            )["result"]["content"][0]["text"]
            rb = json.loads(rolled)
            assert rb["failed"] == 0
            assert all(x["status"] == "rolled_back" for x in rb["rolled_back"])
        finally:
            server.close()

    def test_repair_apply_batch_unknown_issue_partial(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        """V39：未知 issue id → errors 报告 + failed 计数。"""
        server = McpServer(project=tmp_path / "ws")
        try:
            scan = _call(
                server,
                1,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )["result"]["content"][0]["text"]
            scan_run_id = json.loads(scan)["scan_run_id"]
            res = _call(
                server,
                2,
                "tools/call",
                {
                    "name": "repair_apply_batch",
                    "arguments": {
                        "scan_run_id": scan_run_id,
                        "source_path": str(sample_csv),
                        "issue_ids": ["iss_not_exists"],
                    },
                },
            )["result"]["content"][0]["text"]
            data = json.loads(res)
            assert data["failed"] == 1
            assert "iss_not_exists" in data["errors"]
        finally:
            server.close()

    def test_repair_apply_batch_preview_only_writes_nothing(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        """D5-08：preview_only=true 只返回操作预览，不落库、不写副本。"""
        import hashlib

        (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
        dirty = tmp_path / "ws" / "dirty.csv"
        _write_csv(
            dirty,
            ["name", "status", "price"],
            [[" alice ", "Active", 10], ["bob", "n/a", 9999], ["carol", "inactive", 250]],
        )
        server = McpServer(project=tmp_path / "ws")
        try:
            scan = _call(
                server,
                1,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(dirty)}},
            )["result"]["content"][0]["text"]
            scan_run_id = json.loads(scan)["scan_run_id"]
            before = hashlib.sha256(dirty.read_bytes()).hexdigest()
            res = _call(
                server,
                2,
                "tools/call",
                {
                    "name": "repair_apply_batch",
                    "arguments": {
                        "scan_run_id": scan_run_id,
                        "source_path": str(dirty),
                        "preview_only": True,
                    },
                },
            )["result"]["content"][0]["text"]
            data = json.loads(res)
            assert data["failed"] == 0
            assert data["applied"], "expected at least one preview entry"
            assert not any(x.get("applied") for x in data["applied"])
            previews = [x for x in data["applied"] if x.get("reason") == "preview_only"]
            assert previews, "expected at least one preview_only entry"
            assert all("operation" in x for x in previews)
            assert hashlib.sha256(dirty.read_bytes()).hexdigest() == before
            assert list((tmp_path / "ws" / ".datasentry" / "repairs").glob("*.csv")) == []
        finally:
            server.close()

    def test_drift_thresholds_and_report_export(self, tmp_path: Path, sample_csv: Path) -> None:
        """D1-06：drift_compare/drift_latest 接受 CLI 同名阈值；report_export可用。"""
        server = McpServer(project=tmp_path / "ws")
        try:
            first = _call(
                server,
                40,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )["result"]["content"][0]["text"]
            run_a = json.loads(first)["scan_run_id"]
            with sample_csv.open("a", encoding="utf-8") as fh:
                fh.write("3,40.0\n3,50.0\n")
            second = _call(
                server,
                41,
                "tools/call",
                {"name": "scan_file", "arguments": {"path": str(sample_csv)}},
            )["result"]["content"][0]["text"]
            run_b = json.loads(second)["scan_run_id"]

            cmp_default = _call(
                server,
                42,
                "tools/call",
                {
                    "name": "drift_compare",
                    "arguments": {
                        "reference_run_id": run_a,
                        "current_run_id": run_b,
                    },
                },
            )["result"]["content"][0]["text"]
            assert json.loads(cmp_default)["id"]
            cmp_tuned = _call(
                server,
                43,
                "tools/call",
                {
                    "name": "drift_compare",
                    "arguments": {
                        "reference_run_id": run_a,
                        "current_run_id": run_b,
                        "row_ratio_threshold": 0.99,
                        "score_threshold": 100.0,
                    },
                },
            )["result"]["content"][0]["text"]
            assert json.loads(cmp_tuned)["id"]

            report = _call(
                server,
                44,
                "tools/call",
                {"name": "report_export", "arguments": {"scan_run_id": run_b}},
            )["result"]["content"][0]["text"]
            payload = json.loads(report)
            assert payload["scan"]["id"] == run_b
            assert "issues" in payload
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
                {"name": "scan_file", "arguments": {"path": str(tmp_path / "ws" / "nope.csv")}},
            )
            assert response["error"]["code"] == -32603
        finally:
            server.close()

    def test_scan_file_sampling_passthrough(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                30,
                "tools/call",
                {
                    "name": "scan_file",
                    "arguments": {
                        "path": str(sample_csv),
                        "sampling_size": 3,
                        "sampling_method": "reservoir",
                        "sampling_seed": 7,
                    },
                },
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload["status"] == "completed"
            assert payload["row_count"] == 4
            assert "scan_run_id" in payload
        finally:
            server.close()

    def test_advertised_sampling_methods_match_core(self, tmp_path: Path) -> None:
        """D1-02: the schema must list every method core accepts, not three of them."""
        from datasentry.mcp_server import SAMPLING_METHODS
        from datasentry_core.models.scan import SamplingConfig

        core = set(get_args(SamplingConfig.model_fields["method"].annotation))
        assert core == set(SAMPLING_METHODS)
        server = McpServer(project=tmp_path / "ws")
        try:
            schema = _tool_map(server)["scan_file"]["inputSchema"]
            assert set(schema["properties"]["sampling_method"]["enum"]) == core
        finally:
            server.close()

    def test_every_advertised_sampling_method_completes(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        """The three methods D1-02 found unadvertised still have to work when an agent uses them."""
        server = McpServer(project=tmp_path / "ws")
        try:
            for method in ("stratified", "time_based", "rare_oversampling", "none"):
                payload = _payload(
                    _call(
                        server,
                        31,
                        "tools/call",
                        {
                            "name": "scan_file",
                            "arguments": {"path": str(sample_csv), "sampling_method": method},
                        },
                    )
                )
                assert payload["status"] == "completed", method
        finally:
            server.close()

    def test_scan_file_detectors_and_tags_passthrough(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                31,
                "tools/call",
                {
                    "name": "scan_file",
                    "arguments": {
                        "path": str(sample_csv),
                        "detectors": ["uniqueness_violation"],
                        "tags": {"team": "data"},
                    },
                },
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            assert payload["status"] == "completed"
            assert payload["total_issues"] >= 1
        finally:
            server.close()

    def test_scan_file_sampling_parity_with_cli(self, tmp_path: Path, sample_csv: Path) -> None:
        from datasentry.client import DataSentry

        server = McpServer(project=tmp_path / "ws")
        try:
            mcp_scan = json.loads(
                _call(
                    server,
                    32,
                    "tools/call",
                    {
                        "name": "scan_file",
                        "arguments": {
                            "path": str(sample_csv),
                            "sampling_size": 3,
                            "sampling_method": "reservoir",
                            "sampling_seed": 7,
                        },
                    },
                )["result"]["content"][0]["text"]
            )
        finally:
            server.close()
        cli = DataSentry(project=tmp_path / "ws")
        mcp_runs = cli.get_detector_runs(mcp_scan["scan_run_id"])
        sampled = [r for r in mcp_runs if r.sampling is not None and r.sampling.sampled]
        assert sampled
        assert all(r.sampling.method == "reservoir" for r in sampled)
        assert all(r.sampling.sample_size == 3 for r in sampled)
        assert all(r.sampling.full_size == 4 for r in sampled)
        cli.close()


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


class TestJobsV13:
    """Step 88（ADR-088）：MCP job_update / job_remove。"""

    def test_tools_list_includes_update_remove(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(server, 9, "tools/list")["result"]
            names = {t["name"] for t in result["tools"]}
            assert {"job_update", "job_remove"} <= names
        finally:
            server.close()

    def test_job_update_cron_and_disable(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            text = _call(
                server,
                10,
                "tools/call",
                {
                    "name": "job_create",
                    "arguments": {
                        "name": "nightly",
                        "path": str(sample_csv),
                        "cron": "0 9 * * *",
                    },
                },
            )["result"]["content"][0]["text"]
            job_id = json.loads(text)["job"]["job_id"]
            updated = json.loads(
                _call(
                    server,
                    11,
                    "tools/call",
                    {
                        "name": "job_update",
                        "arguments": {"job_id": job_id, "cron": "0 12 * * *", "enabled": False},
                    },
                )["result"]["content"][0]["text"]
            )
            assert updated["ok"] is True
            assert updated["job"]["cron"] == "0 12 * * *"
            assert updated["job"]["enabled"] is False
            assert "12:00" in updated["job"]["next_run_at"]
        finally:
            server.close()

    def test_job_update_invalid_cron(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            text = _call(
                server,
                12,
                "tools/call",
                {
                    "name": "job_create",
                    "arguments": {
                        "name": "nightly",
                        "path": str(sample_csv),
                        "cron": "0 9 * * *",
                    },
                },
            )["result"]["content"][0]["text"]
            job_id = json.loads(text)["job"]["job_id"]
            result = json.loads(
                _call(
                    server,
                    13,
                    "tools/call",
                    {
                        "name": "job_update",
                        "arguments": {"job_id": job_id, "cron": "bad"},
                    },
                )["result"]["content"][0]["text"]
            )
            assert result["ok"] is False
            assert "invalid cron" in result["error"]
        finally:
            server.close()

    def test_job_update_unknown_job(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = json.loads(
                _call(
                    server,
                    14,
                    "tools/call",
                    {
                        "name": "job_update",
                        "arguments": {"job_id": "job_nope", "enabled": True},
                    },
                )["result"]["content"][0]["text"]
            )
            assert result["ok"] is False
            assert "not found" in result["error"]
        finally:
            server.close()

    def test_job_remove(self, tmp_path: Path, sample_csv: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            text = _call(
                server,
                15,
                "tools/call",
                {
                    "name": "job_create",
                    "arguments": {
                        "name": "nightly",
                        "path": str(sample_csv),
                        "cron": "0 9 * * *",
                    },
                },
            )["result"]["content"][0]["text"]
            job_id = json.loads(text)["job"]["job_id"]
            removed = json.loads(
                _call(
                    server,
                    16,
                    "tools/call",
                    {"name": "job_remove", "arguments": {"job_id": job_id}},
                )["result"]["content"][0]["text"]
            )
            assert removed["ok"] is True
            assert removed["removed"] is True
            gone = json.loads(
                _call(
                    server,
                    17,
                    "tools/call",
                    {"name": "job_remove", "arguments": {"job_id": job_id}},
                )["result"]["content"][0]["text"]
            )
            assert gone["ok"] is False
            assert "not found" in gone["error"]
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


def _tool_map(server: McpServer) -> dict:
    body = _call(server, 2, "tools/list")["result"]["tools"]
    return {t["name"]: t for t in body}


def _payload(response: dict) -> object:
    return json.loads(response["result"]["content"][0]["text"])


class TestConfirmationGate:
    """D5-08 and D5-12: no MCP call may mutate state unless it says so, loudly."""

    def _raw(self, server: McpServer, message_id: int, name: str, arguments: dict) -> dict:
        """Call the transport directly, bypassing `_call`.

        `_call` injects the confirmation this class is testing for, so a refusal assertion written
        through it would pass without the guard existing.
        """
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None
        return response

    def test_every_mutating_tool_requires_confirm(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            tools = _tool_map(server)
            for name in MUTATING_TOOLS:
                schema = tools[name]["inputSchema"]
                assert "confirm" in schema["properties"], name
                assert "confirm" in schema["required"], name
        finally:
            server.close()

    def test_apply_batch_requires_an_explicit_preview_choice(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            required = _tool_map(server)["repair_apply_batch"]["inputSchema"]["required"]
            assert "preview_only" in required
        finally:
            server.close()

    def test_absent_confirm_is_a_transport_error_and_writes_nothing(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            for name, args in (
                ("pii_rotate_key", {}),
                ("job_create", {"name": "j1", "path": "p.csv", "cron": "5 * * * *"}),
            ):
                response = self._raw(server, 11, name, args)
                assert response["error"]["code"] == -32602, name
                assert response["id"] == 11, name
                assert "confirm" in response["error"]["message"], name
            assert "result" not in response
            assert _payload(_call(server, 12, "tools/call", {"name": "jobs_list"})) == []
            assert not (tmp_path / "ws" / ".datasentry" / "vault.key").exists()
        finally:
            server.close()

    def test_confirm_false_is_refused_like_an_absent_confirm(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = self._raw(
                server,
                13,
                "job_create",
                {"name": "j1", "path": "p.csv", "cron": "5 * * * *", "confirm": False},
            )
            assert response["error"]["code"] == -32602
            assert "confirm=true" in response["error"]["message"]
        finally:
            server.close()

    def test_apply_batch_without_an_explicit_preview_choice_writes_nothing(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        """D5-08's exact red shape: scan id + source path and nothing else must not apply.

        Only the refusal channel is asserted (no `result` frame), not its code: the code value is
        row r8's subject.
        """
        dirty = tmp_path / "ws" / "dirty.csv"
        _write_csv(dirty, ["name", "status"], [[" alice ", "Active"], ["bob", "n/a"]])
        server = McpServer(project=tmp_path / "ws")
        try:
            scan = _payload(
                _call(
                    server,
                    16,
                    "tools/call",
                    {"name": "scan_file", "arguments": {"path": str(dirty)}},
                )
            )
            response = self._raw(
                server,
                17,
                "repair_apply_batch",
                {"scan_run_id": scan["scan_run_id"], "source_path": str(dirty), "confirm": True},
            )
            assert "result" not in response, response
            assert "preview_only" in response["error"]["message"], response["error"]["message"]
            assert list((tmp_path / "ws" / ".datasentry" / "repairs").glob("*.csv")) == []
        finally:
            server.close()

    def test_explicit_confirm_performs_the_call(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _payload(
                _call(
                    server,
                    15,
                    "tools/call",
                    {
                        "name": "job_create",
                        "arguments": {
                            "name": "j1",
                            "path": "p.csv",
                            "cron": "5 * * * *",
                            "confirm": True,
                        },
                    },
                )
            )
            assert result["ok"] is True, result
            assert result["job"]["name"] == "j1", result
        finally:
            server.close()


class TestErrorCodes:
    """D1-04 and D1-08: a client mistake is an Invalid params frame, never a Server error."""

    def test_missing_required_argument_is_invalid_params(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(server, 50, "tools/call", {"name": "quality_score", "arguments": {}})
            assert response["error"]["code"] == -32602
            assert "scan_run_id" in response["error"]["message"]
            assert response["id"] == 50
        finally:
            server.close()

    def test_unknown_argument_is_invalid_params(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                51,
                "tools/call",
                {"name": "list_issues", "arguments": {"severityAtLeast": "high"}},
            )
            assert response["error"]["code"] == -32602
            assert "unknown argument" in response["error"]["message"]
        finally:
            server.close()

    def test_value_outside_the_advertised_enum_is_invalid_params(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                52,
                "tools/call",
                {
                    "name": "scan_file",
                    "arguments": {"path": str(sample_csv), "sampling_method": "nonsense_value"},
                },
            )
            assert response["error"]["code"] == -32602
            assert "rare_oversampling" in response["error"]["message"]
        finally:
            server.close()

    def test_wrong_json_type_is_invalid_params(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                53,
                "tools/call",
                {"name": "pii_purge_sessions", "arguments": {"olderThanDays": True}},
            )
            assert response["error"]["code"] == -32602
            assert "must be of type integer" in response["error"]["message"]
        finally:
            server.close()

    def test_arguments_must_be_an_object(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            response = _call(
                server,
                54,
                "tools/call",
                {"name": "list_issues", "arguments": ["not", "map"]},
            )
            assert response["error"]["code"] == -32602
            assert "arguments must be a JSON object" in response["error"]["message"]
        finally:
            server.close()

    def test_parse_error_gets_a_frame_and_the_server_survives(
        self, tmp_path: Path, sample_csv: Path
    ) -> None:
        """`not-json` used to be dropped in silence; the session must answer -32700 and stay up."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "datasentry.cli", "mcp", "--project", str(tmp_path / "ws")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write("not-json-at-all\n")
            proc.stdin.flush()
            parse = json.loads(proc.stdout.readline())
            assert parse["error"]["code"] == -32700
            assert parse["id"] is None

            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
            proc.stdin.flush()
            assert json.loads(proc.stdout.readline())["result"] == {}

            proc.stdin.write(json.dumps(["a JSON array, not a request"]) + "\n")
            proc.stdin.flush()
            assert json.loads(proc.stdout.readline())["error"]["code"] == -32600
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)


class TestMalformedToolName:
    """复核 A-9：`name` 不是字符串时，错在客户端，不能打死服务器。

    r8 把参数校验错误归到 -32602，但查表本身先抛了 `TypeError: unhashable type`，
    从 `_handle_message` 逃出去就会终结 stdio 循环——一个客户端的输入错误换来服务端死亡，
    正是该轮想消灭的那类错配。
    """

    @pytest.mark.parametrize("bad_name", [["x"], {"k": "v"}, 7, True, None])
    def test_a_non_string_tool_name_is_a_client_error(
        self, tmp_path: Path, bad_name: object
    ) -> None:
        server = McpServer(project=tmp_path / "ws")
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {"name": bad_name, "arguments": {}},
            }
        )
        assert response is not None
        assert response["id"] == 42
        assert response["error"]["code"] == -32602
        assert "name" in response["error"]["message"]

    def test_the_session_survives_a_malformed_call(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        server._handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": ["x"]}}
        )
        after = server._handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert after["result"]["tools"], "the server stopped answering after one bad frame"


class TestPathContainment:
    """复核 A-2/A-3/A-4：MCP 的四个路径面与 REST/UI 用同一条收束规则。

    `scan_file` 早先经门面的 `enforce_scan_containment` 收过，但 `contract_validate`、
    `job_create`、`repair_propose_batch`、`repair_apply_batch` 各自把参数直接交给磁盘。
    实测改前：`repair_apply_batch` 把工作区外文件的字节作为回滚快照复制进了工作区，
    `contract_validate` 把文件内容回显在错误文本里。拒绝走 -32602（客户端错），不是 -32603。
    """

    def _call(self, server: McpServer, name: str, args: dict) -> dict:
        return server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )

    @pytest.fixture()
    def faces(self, tmp_path: Path) -> tuple[McpServer, str, str, str]:
        """(server, 工作区内文件, 工作区外文件, 真实 scan_run_id)。"""
        ws = tmp_path / "proj"
        ws.mkdir()
        inside = ws / "inside.csv"
        inside.write_text("name,status,price\n alice ,Active,10\nbob,n/a,9999\n", encoding="utf-8")
        outside = tmp_path / "outside.csv"
        outside.write_text("name,status,price\n payroll ,Active,99999\n", encoding="utf-8")
        from datasentry.client import DataSentry

        facade = DataSentry(project=ws)
        try:
            run, _d, _i = facade.scan_file(inside)
            scan_run_id = run.id
        finally:
            facade.close()
        server = McpServer(project=ws)
        return server, str(inside), str(outside), scan_run_id

    def test_contract_validate_refuses_a_path_outside_the_workspace(
        self, faces: tuple[McpServer, str, str, str]
    ) -> None:
        server, _inside, outside, _run = faces
        response = self._call(server, "contract_validate", {"path": outside})
        assert response["error"]["code"] == -32602
        assert (
            outside not in response["error"]["message"]
            or "outside workspace" in response["error"]["message"]
        )
        assert "payroll" not in json.dumps(response), "file content echoed back through the error"

    def test_job_create_refuses_an_out_of_workspace_scan_target(
        self, faces: tuple[McpServer, str, str, str]
    ) -> None:
        server, _inside, outside, _run = faces
        response = self._call(
            server,
            "job_create",
            {"name": "j", "path": outside, "cron": "0 3 * * *", "confirm": True},
        )
        assert response["error"]["code"] == -32602

    @pytest.mark.parametrize("tool", ["repair_propose_batch", "repair_apply_batch"])
    def test_repair_faces_refuse_an_out_of_workspace_source(
        self, faces: tuple[McpServer, str, str, str], tool: str
    ) -> None:
        server, _inside, outside, scan_run_id = faces
        args: dict[str, object] = {"scan_run_id": scan_run_id, "source_path": outside}
        if tool == "repair_apply_batch":
            args.update({"preview_only": False, "confirm": True})
        response = self._call(server, tool, args)
        assert response["error"]["code"] == -32602
        repairs = Path(server._client.workspace) / ".datasentry" / "repairs"
        assert not any(p.name.endswith(".before.csv") for p in repairs.glob("*.csv")), (
            "the refused call still copied the outside file into the workspace"
        )

    def test_a_workspace_relative_target_still_registers(
        self, faces: tuple[McpServer, str, str, str]
    ) -> None:
        """收束不能顺手把正常用法关掉。"""
        server, inside, _outside, _run = faces
        response = self._call(
            server,
            "job_create",
            {"name": "j", "path": "inside.csv", "cron": "0 3 * * *", "confirm": True},
        )
        assert response["result"]["isError"] is False
        assert json.loads(response["result"]["content"][0]["text"])["ok"] is True
        assert inside
        server.close()


class TestFifthFaceAndRawEcho:
    """复核 A-8-mcp：`confine()` 收了四个面，`scan_file` 走门面的那条路仍漏。

    同一句"工作区外"的拒绝，四个面回 -32602（客户端错），第五个面回 -32603（服务端内部错）——
    按 id 关联响应的 Agent 会把一次永久性拒绝当成抖动去重试；而工具结果里的异常文本会把请求
    路径原样回显。
    """

    def _call(self, server: McpServer, name: str, args: dict) -> dict:
        return server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )

    @pytest.fixture()
    def server(self, tmp_path: Path) -> McpServer:
        ws = tmp_path / "proj"
        ws.mkdir()
        return McpServer(project=ws)

    def test_scan_file_refusal_is_a_client_error_too(self, server: McpServer) -> None:
        outside = server._client.workspace.parent / "A8 No Pe.csv"
        outside.write_text("name,v\n a ,1\n", encoding="utf-8")
        response = self._call(server, "scan_file", {"path": str(outside)})
        message = response["error"]["message"]
        assert response["error"]["code"] == -32602, message
        assert "outside workspace" in message

    def test_a_missing_in_workspace_file_does_not_echo_its_path(self, server: McpServer) -> None:
        target = str(server._client.workspace / "No Pe/x.csv")
        response = self._call(server, "scan_file", {"path": target})
        assert "No Pe" not in json.dumps(response), response

    def test_no_mcp_site_turns_an_exception_into_text_by_hand(self) -> None:
        """护栏只证明它真正证明的那件事：异常→文本的几种写法都必须走 `safe_detail`。

        解析整个模块而不是类体——`_json_safe` 与模块级 helper 在类体外，只看类体会让一条已在
        文件里的通路隐形。判定按名字：凡 `except ... as X` 绑定过的名字（含同名参数）都算，所以
        helper 里的 `str(exc)` 也在网内。它不覆盖工具结果载荷：`report_export` 等面把用户自己的
        `source_path` 放进结果是设计如此，REST 同一形状，不是跨面漂移。
        """
        import ast
        import inspect
        from pathlib import Path as _P

        source = _P(inspect.getsourcefile(McpServer)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bound = {h.name for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and h.name}
        assert bound, "the guard tests nothing if no handler binds a name"
        parent: dict[int, ast.AST] = {}
        for holder in ast.walk(tree):
            for child in ast.iter_child_nodes(holder):
                parent[id(child)] = holder

        def uses_exception(node: ast.AST | None) -> bool:
            if node is None:
                return False
            return any(isinstance(n, ast.Name) and n.id in bound for n in ast.walk(node))

        def flagged(node: ast.AST) -> bool:
            holder = parent.get(id(node))
            if isinstance(node, ast.Name) and node.id in bound:
                if isinstance(holder, ast.FormattedValue):
                    return True
                if isinstance(holder, ast.Attribute) and holder.attr in {
                    "__str__",
                    "__repr__",
                    "args",
                    "__cause__",
                    "__context__",
                }:
                    return True
                return (
                    isinstance(holder, ast.Call)
                    and isinstance(holder.func, ast.Name)
                    and holder.func.id in {"str", "repr"}
                )
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                return uses_exception(node.right)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
            ):
                return uses_exception(node.func.value) or any(uses_exception(a) for a in node.args)
            return False

        raw = sorted(node.lineno for node in ast.walk(tree) if flagged(node))
        assert raw == [], (
            f"mcp_server.py lines {raw} turn an exception into text verbatim; route it through "
            "`safe_detail(exc)` so a request path cannot ride back out in the message"
        )
