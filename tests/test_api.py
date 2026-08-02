"""Step 7 REST API 测试（FastAPI TestClient，22/23 章 HTTP 面）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app
from datasentry_core.models.enums import Severity


def _sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "customers.csv"
    p.write_text(
        "name,status,price\n alice ,Active,10\nbob,n/a,9999\ncarol,inactive,250\n",
        encoding="utf-8",
    )
    return p


class TestApiApp:
    def test_health(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["service"] == "datasentry"
        assert body["workspace"] == str(tmp_path)

    def test_root_lists_endpoints(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "POST /scans" in resp.json()["endpoints"]

    def test_scan_full_cycle(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        resp = client.post("/scans", json={"path": str(csv)})
        assert resp.status_code == 201
        body = resp.json()
        run_id = body["run"]["id"]
        assert "issues" in body
        # 详情 / issues / score / report / list
        assert client.get(f"/scans/{run_id}").status_code == 200
        issues_resp = client.get(f"/scans/{run_id}/issues")
        assert issues_resp.status_code == 200
        assert isinstance(issues_resp.json(), list)
        score_resp = client.get(f"/scans/{run_id}/score")
        assert score_resp.status_code == 200
        assert "overall" in score_resp.json()
        assert "dimensions" in score_resp.json()
        report_resp = client.get(f"/scans/{run_id}/report")
        assert report_resp.status_code == 200
        assert report_resp.json()["scan"]["id"] == run_id
        runs = client.get("/scans")
        assert run_id in runs.json()

    def test_scan_not_found_path(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.post("/scans", json={"path": str(tmp_path / "missing.csv")})
        assert resp.status_code == 404
        assert resp.json()["detail"]

    def test_scan_empty_detectors(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        resp = client.post(
            "/scans",
            json={"path": str(csv), "detectors": [], "seed": 7},
        )
        assert resp.status_code == 201
        assert resp.json()["run"]["status"] == "completed"

    def test_get_scan_unknown_404(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        assert client.get("/scans/nope").status_code == 404

    def test_list_all_issues_filter(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        client.post("/scans", json={"path": str(csv)})
        resp = client.get("/issues", params={"severity_at_least": "high"})
        assert resp.status_code == 200
        assert all(Severity(i["severity"]) is not None for i in resp.json())


class TestRepairApi:
    def test_repair_propose_apply_rollback(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        scan = client.post("/scans", json={"path": str(csv)}).json()
        run_id = scan["run"]["id"]
        issues = scan["issues"]
        whitespace = next(
            i for i in issues if "leading_or_trailing_whitespace" in i["detector_ids"]
        )  # type: ignore[index]
        issue_id = whitespace["id"]  # type: ignore[index]

        prop = client.post(
            f"/scans/{run_id}/repairs/propose",
            json={"issue_id": issue_id, "source_path": str(csv)},
        )
        assert prop.status_code == 200
        assert prop.json()["operation"] == "trim_whitespace"

        preview = client.post(
            f"/scans/{run_id}/repairs/preview",
            json={"issue_id": issue_id, "source_path": str(csv)},
        )
        assert preview.status_code == 200
        body = preview.json()
        assert body["proposal"]["issue_id"] == issue_id
        assert body["preview"]["rule_failures_before"]["leading_or_trailing_whitespace"] > 0  # type: ignore[index]

        rep = client.post(
            f"/scans/{run_id}/repairs/apply",
            json={"issue_id": issue_id, "source_path": str(csv)},
        )
        assert rep.status_code == 200
        run = rep.json()
        assert run["status"] == "applied"
        repairs = client.get("/repairs").json()
        assert any(r["id"] == run["id"] for r in repairs)

        rolled = client.post(f"/repairs/{run['id']}/rollback").json()
        assert rolled["status"] == "rolled_back"

    def test_repair_propose_unmapped(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        scan = client.post("/scans", json={"path": str(csv)}).json()
        run_id = scan["run"]["id"]
        issues = scan["issues"]
        # 找一个不在 MVP 修复映射的 issue（若有）；否则跳过
        unmapped = next(
            (
                i
                for i in issues
                if not any(
                    d
                    in {
                        "leading_or_trailing_whitespace",
                        "inconsistent_case",
                        "suspicious_missing_token",
                        "invalid_date",
                        "impossible_date",
                        "iqr_outlier",
                        "percentile_outlier",
                        "modified_zscore",
                    }
                    for d in i["detector_ids"]
                )
            ),
            None,
        )
        if unmapped is None:
            pytest.skip("no unmapped issue in fixture")
        resp = client.post(
            f"/scans/{run_id}/repairs/propose",
            json={"issue_id": unmapped["id"], "source_path": str(csv)},
        )
        assert resp.status_code == 200
        assert resp.json() is None
