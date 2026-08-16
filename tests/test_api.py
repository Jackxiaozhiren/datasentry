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

    def test_scan_progress_endpoint(self, tmp_path: Path) -> None:
        """V25：GET /scans/progress 返回扫描进度快照（完成态 39/39）。"""
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        resp = client.post("/scans", json={"path": str(csv)})
        assert resp.status_code == 201
        prog = client.get("/scans/progress", params={"path": str(csv)})
        assert prog.status_code == 200
        body = prog.json()
        assert body["scanning"] is False
        assert body["done"] == body["total"] == 39
        assert body["detector"] == ""

    def test_scan_progress_missing_path(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/scans/progress", params={"path": "/nonexistent.csv"})
        assert resp.status_code == 404

    def test_scan_progress_failure_marked(self, tmp_path: Path) -> None:
        """V25：扫描失败时进度槽标记 scanning=false（不悬挂）。"""
        client = TestClient(create_app(project=tmp_path))
        resp = client.post("/scans", json={"path": str(tmp_path / "nope.csv")})
        assert resp.status_code in (404, 500)
        prog = client.get("/scans/progress", params={"path": str(tmp_path / "nope.csv")})
        assert prog.status_code == 200
        assert prog.json()["scanning"] is False

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

    def test_trends_json_endpoint(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        empty = client.get("/trends")
        assert empty.status_code == 200
        assert empty.json() == {"trends": [], "count": 0}
        client.post("/scans", json={"path": str(csv)})
        client.post("/scans", json={"path": str(csv)})
        body = client.get("/trends").json()
        assert body["count"] == 1
        trend = body["trends"][0]
        assert trend["dataset_id"] == "customers"
        assert len(trend["points"]) == 2
        assert {"score", "issues_total", "finished_at"} <= set(trend["points"][0])
        assert "delta" in trend and "direction" in trend

    def test_trends_dataset_filter(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        client.post("/scans", json={"path": str(csv)})
        body = client.get("/trends", params={"dataset_id": "nope"}).json()
        assert body == {"trends": [], "count": 0}

    def test_scan_profiles_endpoint(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        assert client.get("/scans/nope/profiles").status_code == 404
        resp = client.post("/scans", json={"path": str(csv)})
        run_id = resp.json()["run"]["id"]
        body = client.get(f"/scans/{run_id}/profiles")
        assert body.status_code == 200
        data = body.json()
        assert "column_profiles" in data
        assert isinstance(data["column_profiles"], dict)

    def test_interactive_report_html_endpoint(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        run_id = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        resp = client.get(f"/scans/{run_id}/report.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert 'id="issue-table"' in html
        assert "serverBaseUrl" in html  # server 模式注入工作台联动
        assert "<link" not in html and "<script src=" not in html

    def test_report_html_lang_zh(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        run_id = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        resp = client.get(f"/scans/{run_id}/report.html", params={"lang": "zh"})
        assert resp.status_code == 200
        assert "数据质量报告" in resp.text

    def test_report_html_lang_invalid_falls_back_en(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        run_id = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        resp = client.get(f"/scans/{run_id}/report.html", params={"lang": "fr"})
        assert resp.status_code == 200  # 未知语言回退 en
        assert "DataSentry Data Quality Report" in resp.text
        assert "数据质量报告" not in resp.text


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
