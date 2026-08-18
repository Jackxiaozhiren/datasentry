"""Step 24 Web UI 测试（服务端渲染核心页，fastapi TestClient）。"""

from __future__ import annotations

import re
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

    def test_scans_list_compare_checkboxes(self, tmp_path: Path) -> None:
        """V28：列表页含勾选控件 + 对比按钮（表单 GET /ui/compare）。"""
        client = TestClient(create_app(project=tmp_path))
        _scan(client, tmp_path)
        resp = client.get("/ui/scans")
        assert 'name="runs"' in resp.text
        assert 'action="/ui/compare"' in resp.text
        assert "compare-btn" in resp.text

    def test_compare_page(self, tmp_path: Path) -> None:
        """V28：/ui/compare?runs=a,b 渲染维度差值/severity/漂移表。"""
        client = TestClient(create_app(project=tmp_path))
        run_a = _scan(client, tmp_path)
        run_b = _scan(client, tmp_path)
        resp = client.get(f"/ui/compare?runs={run_a}&runs={run_b}")
        assert resp.status_code == 200
        assert "Dimension deltas" in resp.text
        assert "completeness" in resp.text
        assert "Severity counts" in resp.text
        assert "Column drifts" in resp.text
        assert "delta neg" in resp.text or "delta pos" in resp.text or "delta flat" in resp.text

    def test_compare_page_unknown_run(self, tmp_path: Path) -> None:
        """V28：未知 run id → 404 错误页。"""
        client = TestClient(create_app(project=tmp_path))
        run_a = _scan(client, tmp_path)
        resp = client.get(f"/ui/compare?runs={run_a}&runs=scan_nope")
        assert resp.status_code == 404

    def test_compare_page_issue_diff(self, tmp_path: Path) -> None:
        """V29：问题级 diff——新数据引入新问题 → NEW 分组渲染。"""
        client = TestClient(create_app(project=tmp_path))
        run_a = _scan(client, tmp_path)
        csv = tmp_path / "dirty.csv"
        csv.write_text(
            "name,status,price\nx,Active,10\ny,n/a,9999\n,,\nz,Active,-5\n",
            encoding="utf-8",
        )
        resp = client.post("/scans", json={"path": str(csv)})
        run_b = resp.json()["run"]["id"]
        page = client.get(f"/ui/compare?runs={run_a}&runs={run_b}").text
        assert "Issue-level diff" in page
        assert "NEW" in page
        assert "FIXED" in page
        assert "Persistent issues" in page

    def test_scan_detail_batch_propose_form(self, tmp_path: Path) -> None:
        """V30：详情页含批量提案表单（source_path + issue 勾选 + 按钮）。"""
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        assert 'action="/ui/scans/' in page
        assert "batch-propose" in page
        assert 'name="issue_ids"' in page
        assert "batch-propose-btn" in page

    def test_scan_detail_batch_form_default_path(self, tmp_path: Path) -> None:
        """V33：扫描详情批量表单的源路径默认值 = 该次扫描的 source_path（可改）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        resp = client.get(f"/ui/scans/{run_id}")
        assert f'value="{csv}"' in resp.text

    def test_compare_new_group_propose(self, tmp_path: Path) -> None:
        """V33：对比页 NEW 组行内一键批量提案（hidden issue_ids + source_path 预填）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        ref_id = _scan(client, tmp_path)
        csv.write_text(
            "name,status,price,email\n alice ,Active,10,a@b.com\n"
            "bob,n/a,9999,\ncarol,inactive,250,c@d.com\n",
            encoding="utf-8",
        )
        cur_resp = client.post("/scans", json={"path": str(csv)})
        assert cur_resp.status_code == 201
        cur_id = cur_resp.json()["run"]["id"]
        resp = client.get(f"/ui/compare?runs={ref_id}&runs={cur_id}")
        assert resp.status_code == 200
        assert "NEW" in resp.text
        assert "batch-propose" in resp.text
        assert f'value="{csv}"' in resp.text

    def test_batch_propose_flow(self, tmp_path: Path) -> None:
        """V30：批量提案端点 → 结果页（proposed/unsupported 状态、apply 入口）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-propose",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert resp.status_code == 200
        assert "Batch repair proposals" in resp.text
        assert "proposed" in resp.text or "unsupported" in resp.text
        assert "Apply repair" in resp.text

    def test_batch_propose_no_selection(self, tmp_path: Path) -> None:
        """V30：未选 issue → 400。"""
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-propose",
            data={"source_path": "orders.csv"},
        )
        assert resp.status_code == 400

    def test_repairs_page_flow(self, tmp_path: Path) -> None:
        """V32：apply 后修复历史页列出 run（applied + 回滚入口）；回滚后状态翻转。"""
        client = TestClient(create_app(project=tmp_path), follow_redirects=False)
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        resp = client.get("/ui/repairs")
        assert resp.status_code == 200
        assert "Repair history" in resp.text
        assert "applied" in resp.text
        assert "/rollback" in resp.text
        m = re.search(r"/ui/repairs/(rep_[0-9a-f]+)/rollback", resp.text)
        assert m is not None
        repair_run_id = m.group(1)
        back = client.post(f"/ui/repairs/{repair_run_id}/rollback")
        assert back.status_code == 303
        assert back.headers["location"] == "/ui/repairs"
        after = client.get("/ui/repairs")
        assert "rolled back" in after.text
        assert "/rollback" not in after.text

    def test_repairs_page_empty(self, tmp_path: Path) -> None:
        """V32：无修复记录 → 空态文案。"""
        client = TestClient(create_app(project=tmp_path))
        resp = client.get("/ui/repairs")
        assert resp.status_code == 200
        assert "No repairs yet" in resp.text

    def test_compare_fixed_group_repair_context(self, tmp_path: Path) -> None:
        """V37：对比页 FIXED 组显示关联 applied 修复（锚点链接 + 历史页锚）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        ref_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{ref_id}/issues").json()
        ids = [i["id"] for i in issues if "string_format" in i["issue_type"]]
        assert ids
        apply = client.post(
            f"/ui/scans/{ref_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids[:1]},
        )
        assert apply.status_code == 200
        csv.write_text(
            "name,status,price\nalice,Active,10\nbob,Active,250\ncarol,Inactive,300\n",
            encoding="utf-8",
        )
        cur_resp = client.post("/scans", json={"path": str(csv)})
        assert cur_resp.status_code == 201
        cur_id = cur_resp.json()["run"]["id"]
        resp = client.get(f"/ui/compare?runs={ref_id}&runs={cur_id}")
        assert resp.status_code == 200
        assert "FIXED" in resp.text
        assert "fixed by" in resp.text
        assert "#rep_" in resp.text
        history = client.get("/ui/repairs")
        assert 'id="rep_' in history.text

    def test_batch_rollback_flow(self, tmp_path: Path) -> None:
        """V34：批量 apply 后勾选 → 批量回滚 → 结果页 + 历史页全部 rolled back。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        apply = client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert apply.status_code == 200
        assert "batch-rollback-form" in apply.text
        assert "batch-rollback-btn" in apply.text
        import re as _re

        run_ids = _re.findall(r'name="repair_run_ids" value="(rep_[0-9a-f]+)"', apply.text)
        assert len(run_ids) >= 1
        copies_before = list((tmp_path / ".datasentry" / "repairs").glob("rep_*.csv"))
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-rollback",
            data={"repair_run_ids": run_ids},
        )
        assert resp.status_code == 200
        assert "Batch rollback" in resp.text
        assert "rolled back" in resp.text
        history = client.get("/ui/repairs")
        assert "rolled back" in history.text
        copies_after = list((tmp_path / ".datasentry" / "repairs").glob("rep_*.csv"))
        assert len(copies_after) > len(copies_before), "rollback snapshots expected"

    def test_batch_propose_select_all(self, tmp_path: Path) -> None:
        """V34：提案页表头全选 checkbox。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-propose",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert resp.status_code == 200
        assert 'id="select-all"' in resp.text

    def test_batch_apply_flow(self, tmp_path: Path) -> None:
        """V31：提案页勾选 → 批量 apply → 结果页（applied + 回滚链接 + 文件已修复）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        propose = client.post(
            f"/ui/scans/{run_id}/repairs/batch-propose",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert propose.status_code == 200
        assert "batch-apply-form" in propose.text
        assert "batch-apply-btn" in propose.text
        before = csv.read_text()
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert resp.status_code == 200
        assert "Batch repair — applied" in resp.text
        assert "applied" in resp.text
        assert "rollback" in resp.text
        assert csv.read_text() == before
        copies = list((tmp_path / ".datasentry" / "repairs").glob("rep_*.csv"))
        assert len(copies) >= 1
        fixed = [c for c in copies if ".before." not in c.name]
        assert fixed, "repaired copy missing"
        assert " alice " not in fixed[0].read_text()

    def test_batch_apply_verify_flow(self, tmp_path: Path) -> None:
        """V41：Verify 闭环——重扫修复副本 → 303 对比页（原 run vs 验证 run）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues[:2]]
        apply = client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        assert apply.status_code == 200
        assert "verify" in apply.text
        m = re.search(r"/ui/repairs/(rep_[0-9a-f]+)/verify", apply.text)
        assert m, "verify button missing on batch apply results"
        repair_run_id = m.group(1)
        verify = client.post(f"/ui/repairs/{repair_run_id}/verify", follow_redirects=False)
        assert verify.status_code == 303
        location = verify.headers["location"]
        assert location.startswith(f"/ui/compare?runs={run_id}&runs=")
        compare = client.get(location)
        assert compare.status_code == 200
        assert "New issues" in compare.text
        assert run_id in compare.text

    def test_batch_apply_no_selection(self, tmp_path: Path) -> None:
        """V31：未选 issue → 400，且不写文件。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        before = csv.read_text()
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv)},
        )
        assert resp.status_code == 400
        assert csv.read_text() == before

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
