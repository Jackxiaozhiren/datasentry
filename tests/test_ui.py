"""Step 24 Web UI 测试（服务端渲染核心页，fastapi TestClient）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from datasentry.api import create_app


def _sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "customers.csv"
    p.write_text(
        "name,status,price\n alice ,Active,10\nbob,n/a,9999\ncarol,inactive,250\n",
        encoding="utf-8",
    )
    return p


def _scan(client: TestClient, tmp_path: Path) -> str:
    csv = _sample_csv(tmp_path)
    resp = client.post("/scans", json={"path": str(csv)})
    assert resp.status_code == 201
    return resp.json()["run"]["id"]


class TestUiPages:
    def test_home_empty(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert "DataSentry" in resp.text
        assert "No scans yet" in resp.text
        assert "New scan" in resp.text

    def test_home_shows_scans_after_scan(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        resp = client.get("/ui/")
        assert run_id in resp.text
        assert "customers" in resp.text

    def test_scan_detail_page(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        resp = client.get(f"/ui/scans/{run_id}")
        assert resp.status_code == 200
        assert "Issues" in resp.text
        assert "Repair workbench" in resp.text
        assert f"/ui/scans/{run_id}/issues/" in resp.text

    def test_scan_detail_severity_filter(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        resp = client.get(f"/ui/scans/{run_id}", params={"severity": "high"})
        assert resp.status_code == 200
        assert 'href="?severity=high"' in resp.text

    def test_scan_detail_unknown_404(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/scans/nope")
        assert resp.status_code == 404
        assert "not found" in resp.text.lower()

    def test_workbench_propose_and_apply(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        scan = client.post("/scans", json={"path": str(csv)}).json()
        run_id = scan["run"]["id"]
        issues = scan["issues"]
        whitespace = next(
            i for i in issues if "leading_or_trailing_whitespace" in i["detector_ids"]
        )
        issue_id = whitespace["id"]

        page = client.get(f"/ui/scans/{run_id}/issues/{issue_id}")
        assert page.status_code == 200
        assert "Repair workbench" in page.text
        assert "Propose repair" in page.text

        proposed = client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "propose"},
            follow_redirects=True,
        )
        assert proposed.status_code == 200
        assert "trim_whitespace" in proposed.text
        assert "Preview" in proposed.text

        applied = client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "apply"},
            follow_redirects=True,
        )
        assert applied.status_code == 200
        assert "Repair applied" in applied.text
        assert "Rollback" in applied.text

    def test_workbench_unknown_action(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        issue_id = client.get(f"/scans/{run_id}/issues").json()[0]["id"]
        resp = client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": "x.csv", "action": "explode"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "unknown action" in resp.text

    def test_ui_create_scan_form(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/ui/scans",
            data={"path": str(csv)},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/ui/scans/")
        followed = client.get(resp.headers["location"])
        assert followed.status_code == 200

    def test_ui_create_scan_form_error(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.post(
            "/ui/scans",
            data={"path": str(tmp_path / "missing.csv")},
            follow_redirects=True,
        )
        assert resp.status_code == 404
        assert "not found" in resp.text.lower()


class TestUiSecurity:
    def test_column_name_escaped(self, tmp_path: Path) -> None:
        p = tmp_path / "evil.csv"
        p.write_text("<script>alert(1)</script>,age\nx,1\n", encoding="utf-8")
        client = TestClient(create_app(project=tmp_path))
        scan = client.post("/scans", json={"path": str(p)}).json()
        run_id = scan["run"]["id"]
        resp = client.get(f"/ui/scans/{run_id}")
        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestTrendsPage:
    def test_trends_empty(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/trends")
        assert resp.status_code == 200
        assert "No trend data yet" in resp.text

    def test_trends_after_two_scans(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        _scan(client, tmp_path)
        resp = client.get("/ui/trends")
        assert resp.status_code == 200
        assert "Trends" in resp.text
        assert "delta" in resp.text
        assert "completed scans" in resp.text

    def test_home_nav_links_to_trends(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert 'href="/ui/trends"' in resp.text
