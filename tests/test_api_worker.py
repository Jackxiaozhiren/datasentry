"""Step 91（ADR-091）：worker 端点 POST /rpc/execute 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n")
    return p


class TestRpcExecute:
    def test_disabled_without_token(self, tmp_path: Path) -> None:
        with TestClient(create_app(project=tmp_path)) as client:
            resp = client.post("/rpc/execute", json={"project": "p", "path": "x.csv"})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["detail"]

    def test_missing_token_401(self, tmp_path: Path) -> None:
        with TestClient(create_app(project=tmp_path, worker_token="s3cret")) as client:
            resp = client.post("/rpc/execute", json={"project": "p", "path": "x.csv"})
        assert resp.status_code == 401

    def test_wrong_token_401(self, tmp_path: Path) -> None:
        with TestClient(create_app(project=tmp_path, worker_token="s3cret")) as client:
            resp = client.post(
                "/rpc/execute",
                headers={"X-Datasentry-Token": "nope"},
                json={"project": "p", "path": "x.csv"},
            )
        assert resp.status_code == 401

    def test_execute_success_runs_scan(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path)
        app = create_app(project=tmp_path, worker_token="s3cret")
        with TestClient(app) as client:
            resp = client.post(
                "/rpc/execute",
                headers={"X-Datasentry-Token": "s3cret"},
                json={
                    "project": str(tmp_path),
                    "path": str(csv),
                    "dataset_id": "orders",
                    "table_name": "orders",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scan_run_id"]
        assert body["total_issues"] >= 3
        assert body["quality_score"] > 0
        assert body["skipped"] is False
        assert "gate" in body

    def test_invalid_body_422(self, tmp_path: Path) -> None:
        with TestClient(create_app(project=tmp_path, worker_token="s3cret")) as client:
            resp = client.post(
                "/rpc/execute",
                headers={"X-Datasentry-Token": "s3cret"},
                json={"path": 42},
            )
        assert resp.status_code == 422

    def test_missing_file_500(self, tmp_path: Path) -> None:
        with TestClient(create_app(project=tmp_path, worker_token="s3cret")) as client:
            resp = client.post(
                "/rpc/execute",
                headers={"X-Datasentry-Token": "s3cret"},
                json={"project": str(tmp_path), "path": str(tmp_path / "nope.csv")},
            )
        assert resp.status_code == 500
        assert "scan failed" in resp.json()["detail"]

    def test_env_token_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_WORKER_TOKEN", "env-token")
        with TestClient(create_app(project=tmp_path)) as client:
            resp = client.post(
                "/rpc/execute",
                headers={"X-Datasentry-Token": "env-token"},
                json={"project": "p", "path": "x.csv"},
            )
        assert resp.status_code != 401
