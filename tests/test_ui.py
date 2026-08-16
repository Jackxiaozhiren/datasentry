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

    def test_home_lang_zh_nav(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/", params={"lang": "zh"})
        assert resp.status_code == 200
        assert "首页" in resp.text  # zh 导航文案（ADR-069）
        assert "新扫描" in resp.text
        assert "工作区概览" in resp.text

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

    def test_trends_sparkline_and_delta_cells(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        _scan(client, tmp_path)
        resp = client.get("/ui/trends")
        assert 'class="trend-spark"' in resp.text
        assert "<polyline" in resp.text
        assert "<th>Δ</th>" in resp.text
        assert 'class="meta">—</td>' in resp.text  # 首行无前一 run
        assert "delta-up" in resp.text or "delta-down" in resp.text or "0.0" in resp.text

    def test_trends_dimension_lines(self, tmp_path: Path) -> None:
        """V25：六维折线 SVG 随趋势页渲染（含图例）。"""
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        _scan(client, tmp_path)
        resp = client.get("/ui/trends")
        assert 'class="dim-lines"' in resp.text
        assert "completeness" in resp.text and "validity" in resp.text
        assert 'aria-label="quality dimensions over time"' in resp.text

    def test_trends_dimension_table(self, tmp_path: Path) -> None:
        """V26：维度数值表（行=run，列=维度分）。"""
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        _scan(client, tmp_path)
        resp = client.get("/ui/trends")
        assert 'class="dim-table"' in resp.text
        assert "<th>completeness</th>" in resp.text
        assert 'href="/ui/scans/' in resp.text

    def test_scans_list_dim_strip(self, tmp_path: Path) -> None:
        """V27：扫描列表每行渲染六维迷你条。"""
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        resp = client.get("/ui/scans")
        assert 'class="dim-strip"' in resp.text
        assert 'title="completeness' in resp.text

    def test_ui_scan_batch_banner(self, tmp_path: Path) -> None:
        """V27：批量扫描完成 → 汇总横幅（消费式，一次渲染后清除）。"""
        client = TestClient(create_app(project=tmp_path), follow_redirects=False)
        csv = _sample_csv(tmp_path)
        second = tmp_path / "orders2.csv"
        second.write_text(csv.read_text(encoding="utf-8"), encoding="utf-8")
        resp = client.post("/ui/scans", data={"path": f"{csv}, {second}"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/scans"
        page = client.get("/ui/scans")
        assert "Batch scan complete" in page.text
        assert "2 files" in page.text
        after = client.get("/ui/scans")
        assert 'class="batch-banner' not in after.text

    def test_ui_scan_batch_partial_failure(self, tmp_path: Path) -> None:
        """V27：批量部分失败 → 横幅含失败文件与原因，成功 run 照常落库。"""
        client = TestClient(create_app(project=tmp_path), follow_redirects=False)
        csv = _sample_csv(tmp_path)
        resp = client.post("/ui/scans", data={"path": f"{csv}, {tmp_path / 'nope.csv'}"})
        assert resp.status_code == 303
        page = client.get("/ui/scans")
        assert "1 failed" in page.text
        assert "nope.csv" in page.text
        assert 'class="batch-banner warn"' in page.text
        assert '<td><a href="/ui/scans/' in page.text

    def test_home_nav_links_to_trends(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert 'href="/ui/trends"' in resp.text
