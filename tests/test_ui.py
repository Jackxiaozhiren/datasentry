"""Step 24 Web UI 测试（服务端渲染核心页，fastapi TestClient）。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

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

    def test_trends_drift_parallel_cells(self, tmp_path: Path) -> None:
        """V51：issues Δ 列 + 最新两 run 漂移对比深链。"""
        client = TestClient(create_app(project=tmp_path))
        r1 = _scan(client, tmp_path)
        r2 = _scan(client, tmp_path)
        resp = client.get("/ui/trends")
        assert "Issues Δ" in resp.text
        assert "drift vs previous scan" in resp.text
        assert f"/ui/compare?runs={r1}&runs={r2}" in resp.text
        assert "→" in resp.text

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

    def test_repair_artifact_page(self, tmp_path: Path) -> None:
        """V43：工件页——before/after diff 行、变更高亮、无变更提示。"""
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
        m = re.search(r"/ui/repairs/(rep_[0-9a-f]+)/artifact", apply.text)
        artifact_url = f"/ui/repairs/{m.group(1)}/artifact"
        page = client.get(artifact_url)
        assert page.status_code == 200
        assert "Repair artifact" in page.text
        assert "before" in page.text and "after" in page.text
        assert "diff-del" in page.text and "diff-add" in page.text
        assert "/verify" in page.text and "/rollback" in page.text
        history = client.get("/ui/repairs")
        assert artifact_url in history.text

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
        assert "/artifact" in resp.text and "rep_" in resp.text
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

    def test_batch_rollback_failure_reasons(self, tmp_path: Path) -> None:
        """V48：批量回滚失败细分——缺失 run 与缺失快照各自展示原因。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        ids = [i["id"] for i in issues if i["issue_type"] == "string_format"][:1]
        apply = client.post(
            f"/ui/scans/{run_id}/repairs/batch-apply",
            data={"source_path": str(csv), "issue_ids": ids},
        )
        import re as _re

        real_run = _re.findall(r'name="repair_run_ids" value="(rep_[0-9a-f]+)"', apply.text)
        assert len(real_run) >= 1
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-rollback",
            data={"repair_run_ids": [real_run[0], "rep_deadbeef0001"]},
        )
        assert resp.status_code == 200
        assert "rep_deadbeef0001" in resp.text
        assert "repair run not found" in resp.text
        assert "rolled back" in resp.text

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


SELECT_RE = re.compile(r'<select[^>]+name="source_path"')
TEXT_INPUT_RE = re.compile(r'<input[^>]+type="text"[^>]+name="source_path"')
COPY_EN = "never overwrites the original file"
COPY_ZH = "绝不覆盖原文件"


class TestRepairSourcePicker:
    """UI-04（r6）：两处修复面把 source_path 从自由文本改成已登记数据集下拉，并回显只写副本承诺。

    判据来自 `AUDIT/tools/probe_ui_repair_faces.py --face C`（改前实测
    `text_input=True / select=False / copy_stated=False`）。
    """

    def test_detail_page_renders_a_select_not_a_free_text_path(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        assert SELECT_RE.search(page)
        assert not TEXT_INPUT_RE.search(page)
        assert f'<option value="{csv}"' in page

    def test_detail_page_preselects_the_scanned_source(self, tmp_path: Path) -> None:
        """该次扫描自己的文件必须是默认选中项，否则用户不改就修错数据集。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        assert f'<option value="{csv}" selected>' in page

    def test_picker_offers_every_registered_source(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        first = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        second = tmp_path / "orders.csv"
        second.write_text("order_id,amount\n1,3.0\n2,\n", encoding="utf-8")
        assert client.post("/scans", json={"path": str(second)}).status_code == 201
        page = client.get(f"/ui/scans/{run_id}").text
        assert f'<option value="{first}"' in page
        assert f'<option value="{second}"' in page

    def test_detail_page_states_the_copy_only_promise(self, tmp_path: Path) -> None:
        """文案必须同时说出「不覆盖原文件」和**真实落盘目录**。

        首稿写的是「在源文件旁」，与 `repair/engine.py:258-259` 的实际产物位置
        （`<workspace>/.datasentry/repairs/`）不符——安全承诺说错地点比不说更糟。
        """
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        assert COPY_EN in page
        assert ".datasentry/repairs/" in page

    def test_detail_page_states_the_promise_in_chinese(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}", params={"lang": "zh"}).text
        assert COPY_ZH in page
        assert "ui." not in page

    def test_workbench_page_carries_the_same_picker_and_promise(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        whitespace = next(
            i for i in issues if "leading_or_trailing_whitespace" in i["detector_ids"]
        )
        page = client.get(f"/ui/scans/{run_id}/issues/{whitespace['id']}").text
        assert SELECT_RE.search(page)
        assert not TEXT_INPUT_RE.search(page)
        assert f'<option value="{csv}" selected>' in page
        assert COPY_EN in page

    def test_picker_survives_a_propose_round_trip(self, tmp_path: Path) -> None:
        """POST 回来的页面仍是下拉：否则一次提案就把用户退回自由文本框。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        whitespace = next(
            i for i in issues if "leading_or_trailing_whitespace" in i["detector_ids"]
        )
        page = client.post(
            f"/ui/scans/{run_id}/issues/{whitespace['id']}",
            data={"source_path": str(csv), "action": "propose"},
        ).text
        assert SELECT_RE.search(page)
        assert not TEXT_INPUT_RE.search(page)
        assert COPY_EN in page


class TestRepairSourcePickerEmptyWorkspace:
    """空态：没有扫描过任何文件时，页面说人话而不是漏出 i18n 键名。"""

    def test_empty_picker_explains_and_stays_submittable(self) -> None:
        from datasentry.ui import _source_path_field

        html = _source_path_field([], None, html_id="source_path", label="L", lang="en")
        assert "<select" in html
        # `disabled` was the first draft and is wrong: a disabled control is never submitted, so
        # the POST arrives without the field and FastAPI answers in JSON (review B-6).
        assert "disabled" not in html
        assert "No source file has been scanned" in html
        assert "ui.no_registered_sources" not in html

    def test_empty_picker_explains_in_chinese(self) -> None:
        from datasentry.ui import _source_path_field

        html = _source_path_field([], None, html_id="source_path", label="L", lang="zh")
        assert "还没有扫描过任何源文件" in html
        assert "ui.no_registered_sources" not in html


class _Control(NamedTuple):
    name: str
    type: str
    value: str
    in_form: bool
    form_attr: str | None


class _FormParser(HTMLParser):
    """Which controls a browser would attach to `form_id`, and the value each would submit.

    A control belongs if it is inside the `<form>` element or carries `form="<form_id>"`. This is
    the predicate `UI-07` failed: every server-side test posted `issue_ids` explicitly, so a green
    suite coexisted with a button that could never submit a ticked issue. A `<select>` submits its
    selected option (the first when none is marked), not a `value` attribute on the tag.
    """

    def __init__(self, form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.controls: list[_Control] = []
        self._depth = 0
        self._select: tuple[str, list[str], str | None] | None = None

    def _emit(self, tag: str, a: dict[str, str | None]) -> None:
        self.controls.append(
            _Control(
                name=a.get("name") or "",
                type=(a.get("type") or "text").lower() if tag == "input" else tag,
                value=a.get("value") or "",
                in_form=self._depth > 0,
                form_attr=a.get("form"),
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            if a.get("id") == self.form_id:
                self._depth += 1
            return
        if tag == "select":
            self._select = (a.get("name") or "", [], None)
            return
        if self._select is not None:
            if tag == "option":
                self._select[1].append(a.get("value") or "")
                if a.get("selected") is not None and self._select[2] is None:
                    self._select = (*self._select[:2], a.get("value") or "")
            return
        if tag not in ("input", "textarea"):
            return
        if tag == "input" and (a.get("type") or "").lower() in ("submit", "button", "image"):
            return
        self._emit(tag, a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._depth > 0:
            self._depth -= 1
        elif tag == "select" and self._select is not None:
            name, options, selected = self._select
            self.controls.append(
                _Control(
                    name=name,
                    type="select",
                    value=selected if selected is not None else (options[0] if options else ""),
                    in_form=self._depth > 0,
                    form_attr=None,
                )
            )
            self._select = None

    def belonging(self) -> list[_Control]:
        return [c for c in self.controls if c.in_form or c.form_attr == self.form_id]


def _submittable(html: str, form_id: str) -> list[_Control]:
    p = _FormParser(form_id)
    p.feed(html)
    return p.belonging()


class TestBatchRepairFormSemantics:
    """UI-07（r14）：勾选的 issue 必须真的属于批量表单，浏览器提交才带得上。"""

    def test_every_issue_checkbox_belongs_to_the_form(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        issues = client.get(f"/scans/{run_id}/issues").json()
        belonging = _submittable(page, "batch-repair-form")
        ticks = [c for c in belonging if c.name == "issue_ids"]
        assert len(ticks) == len(issues), (
            f"{len(ticks)} of {len(issues)} issue checkboxes belong to the form; the rest cannot "
            "be submitted no matter what the user ticks"
        )
        assert {c.value for c in ticks} == {i["id"] for i in issues}

    def test_a_form_semantics_submit_reaches_the_server(self, tmp_path: Path) -> None:
        """按浏览器归属规则拼载荷提交，而不是手写 issue_ids。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        belonging = _submittable(page, "batch-repair-form")
        ticks = [c for c in belonging if c.name == "issue_ids"]
        source = next(c for c in belonging if c.name == "source_path")
        assert ticks and source.value == str(csv)
        picked = [c.value for c in ticks if "whitespace" in _detectors(client, run_id, c.value)]
        assert picked, "fixture lost the whitespace issue the batch face is meant to repair"
        resp = client.post(
            f"/ui/scans/{run_id}/repairs/batch-propose",
            data={"source_path": source.value, "issue_ids": picked},
        )
        assert resp.status_code == 200
        assert f"1 / {len(picked)}" in resp.text
        assert "trim_whitespace" in resp.text

    def test_no_selection_refuses_actionably_and_offers_the_way_back(self, tmp_path: Path) -> None:
        """兜底文案：说清该做什么，并给回原扫描页的链接（UI-07 fix_sketch 的第二半）。"""
        client = TestClient(create_app(project=tmp_path))
        csv = _sample_csv(tmp_path)
        run_id = _scan(client, tmp_path)
        for path in (
            f"/ui/scans/{run_id}/repairs/batch-propose",
            f"/ui/scans/{run_id}/repairs/batch-apply",
        ):
            resp = client.post(path, data={"source_path": str(csv)})
            assert resp.status_code == 400, path
            assert "Tick at least one issue" in resp.text, path
            assert "no issues selected" not in resp.text, path
            assert f'href="/ui/scans/{run_id}"' in resp.text, path

    def test_the_form_id_and_the_association_share_one_constant(self, tmp_path: Path) -> None:
        from datasentry.ui import BATCH_REPAIR_FORM_ID

        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = client.get(f"/ui/scans/{run_id}").text
        assert f'id="{BATCH_REPAIR_FORM_ID}"' in page
        assert f'form="{BATCH_REPAIR_FORM_ID}"' in page


def _detectors(client: TestClient, run_id: str, issue_id: str) -> str:
    return next(
        ",".join(i["detector_ids"])
        for i in client.get(f"/scans/{run_id}/issues").json()
        if i["id"] == issue_id
    )


class TestEmptyPickerCannotDeadEnd:
    """复核 B-6：空态控件不能把用户送进裸 JSON 422。

    `disabled` 的控制不参与约束校验也不会被提交，于是 `required` 形同虚设，POST 到达服务端时
    根本没有 `source_path` 字段，FastAPI 回的是 `application/json` 422——正是 UI-06 那类死端。
    """

    def test_empty_picker_is_not_disabled(self) -> None:
        from datasentry.ui import _source_path_field

        html = _source_path_field([], None, html_id="source_path", label="L", lang="en")
        assert "disabled" not in html
        assert 'name="source_path" required' in html
        assert "No source file has been scanned" in html

    def test_a_source_path_free_submit_is_refused_in_html_not_json(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        issues = client.get(f"/scans/{run_id}/issues").json()
        resp = client.post(
            f"/ui/scans/{run_id}/issues/{issues[0]['id']}",
            data={"source_path": "", "action": "propose"},
        )
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("text/html")


def _page_body(html: str) -> str:
    """Page markup with the ``<style>`` block removed.

    ``.diff-del`` is declared in the CSS shipped on *every* page, so a plain substring check
    would "prove" a diff exists on a page that renders none at all.
    """
    return re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)


def _diff_cells(html: str) -> list[str]:
    return re.findall(
        r'<td class="diff-(?:del|add)"><code>(.*?)</code></td>',
        _page_body(html),
        flags=re.DOTALL,
    )


class TestApplyResponseCarriesEvidence:
    """P31-A / D2-06 第二症状：单条 issue 的 apply 响应页只有 "Repair applied"，
    要看行级证据必须再点一次工件页——写入数据后却看不到写了什么。"""

    def _whitespace_issue(self, client: TestClient, run_id: str) -> str:
        return next(
            i["id"]
            for i in client.get(f"/scans/{run_id}/issues").json()
            if "leading_or_trailing_whitespace" in i["detector_ids"]
        )

    def _apply(
        self,
        client: TestClient,
        tmp_path: Path,
        run_id: str,
        issue_id: str,
        source: Path | None = None,
    ):
        csv = source or _sample_csv(tmp_path)
        client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "propose"},
        )
        return client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "apply"},
            follow_redirects=True,
        )

    def test_applied_page_shows_the_changed_cells(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = self._apply(client, tmp_path, run_id, self._whitespace_issue(client, run_id))
        assert page.status_code == 200
        assert "Repair applied" in page.text
        assert "Row-level changes applied" in page.text
        cells = _diff_cells(page.text)
        assert cells, "apply response renders no changed cell"
        assert 'class="diff-row"' in _page_body(page.text)

    def test_changed_blanks_survive_the_render(self, tmp_path: Path) -> None:
        """行级证据的主角就是那几个空格。

        普通流里 HTML 会折叠首尾空白，裸 `<td>` 会把 ` alice ` 和 `alice` 渲染成一模一样的
        "alice"——写了数据却看不出写了什么。
        """
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = self._apply(client, tmp_path, run_id, self._whitespace_issue(client, run_id))
        assert _diff_cells(page.text) == [" alice ", "alice"]
        assert re.search(r"\.diff-del \{[^}]*pre-wrap", page.text), "diff cell CSS drops pre-wrap"

    def test_applied_page_links_the_full_artifact(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        page = self._apply(client, tmp_path, run_id, self._whitespace_issue(client, run_id))
        href = re.search(r'href="/ui/repairs/([^"]+)/artifact"', _page_body(page.text))
        assert href is not None, "apply response does not link its own repair artifact"

    def test_applied_page_and_artifact_page_agree_on_every_cell(self, tmp_path: Path) -> None:
        """共享 `_diff_table` 的意义是两个面不能把同一份证据渲染成两样。"""
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        issue_id = self._whitespace_issue(client, run_id)
        page = self._apply(client, tmp_path, run_id, issue_id)
        repair_run_id = re.search(r"/ui/repairs/([^\"]+)/artifact", _page_body(page.text))
        assert repair_run_id is not None
        artifact = client.get(f"/ui/repairs/{repair_run_id.group(1)}/artifact")
        assert artifact.status_code == 200
        applied_cells = _diff_cells(page.text)
        assert applied_cells
        assert applied_cells == _diff_cells(artifact.text)

    def test_unreadable_artifact_says_so_instead_of_going_silent(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        app = client.app
        run_id = _scan(client, tmp_path)
        issue_id = self._whitespace_issue(client, run_id)
        original = app.state.client.repair_diff

        def refuse(_run_id: str):
            raise FileNotFoundError("repaired copy missing")

        app.state.client.repair_diff = refuse
        try:
            page = self._apply(client, tmp_path, run_id, issue_id)
        finally:
            app.state.client.repair_diff = original
        body = _page_body(page.text)
        assert "Repair applied" in page.text
        assert "could not be read from this repair" in body
        assert "/artifact" in body
        assert not _diff_cells(page.text)

    def test_propose_response_does_not_pretend_to_have_written(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        run_id = _scan(client, tmp_path)
        csv = _sample_csv(tmp_path)
        proposed = client.post(
            f"/ui/scans/{run_id}/issues/{self._whitespace_issue(client, run_id)}",
            data={"source_path": str(csv), "action": "propose"},
        )
        assert "trim_whitespace" in proposed.text
        assert "Row-level changes applied" not in proposed.text
        assert not _diff_cells(proposed.text)

    def test_ragged_rows_still_render_every_column(self) -> None:
        """一侧缺行时，缺的那侧仍要逐列渲染并高亮，不能画成一排没有对照的空行。"""
        from datasentry.ui import _diff_table

        html = _diff_table(["a", "b"], [[1, 2], [3, 4]], [[1, 2]], [1])
        assert '<td class="diff-del"><code>3</code></td>' in html
        assert '<td class="diff-del"><code>4</code></td>' in html
        assert html.count("∅") == 2, "the missing row must still render every column"
        assert '<td class="diff-add"><code>∅</code></td>' in html

    def test_a_large_repair_is_capped_and_says_so(self, tmp_path: Path) -> None:
        """apply 响应在写入路径上：百万行文件的一次修复不能回一整个 diff。"""
        client = TestClient(create_app(project=tmp_path))
        csv = tmp_path / "wide.csv"
        rows = "\n".join(f" n{i} ,{i}" for i in range(60))
        csv.write_text(f"name,v\n{rows}\n", encoding="utf-8")
        run_id = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        issue_id = self._whitespace_issue(client, run_id)
        page = self._apply(client, tmp_path, run_id, issue_id, source=csv)
        assert page.status_code == 200
        shown = _page_body(page.text).count('class="diff-row"')
        assert shown == 50, f"apply page rendered {shown} diff rows, cap is 50"
        assert "10 more changed row" in page.text
        repair_run_id = re.search(r'href="/ui/repairs/([^"]+)/artifact"', _page_body(page.text))
        assert repair_run_id is not None
        artifact = client.get(f"/ui/repairs/{repair_run_id.group(1)}/artifact")
        assert artifact.text.count('class="diff-row"') == 60, (
            "the cap must be page-level policy, not lost evidence"
        )

    def test_artefact_failure_does_not_print_server_paths(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        app = client.app
        run_id = _scan(client, tmp_path)
        issue_id = self._whitespace_issue(client, run_id)
        original = app.state.client.repair_diff
        secret = str(tmp_path / ".datasentry" / "repairs" / "rep_deadbeef.before.csv")

        def refuse(_run_id: str):
            raise FileNotFoundError(f"repaired copy missing: {secret}")

        app.state.client.repair_diff = refuse
        try:
            page = self._apply(client, tmp_path, run_id, issue_id)
        finally:
            app.state.client.repair_diff = original
        assert secret not in page.text, "the apply page echoed an absolute server path"
        assert "repaired copy missing" in page.text


class TestApplyPageTellsTheTruthForOtherDialects:
    """簇判据的跨面一半：`table_diff` 说真话之后，apply 页画出来的也得是真话。

    读侧修好之前，一份 `.tsv` 的 before 全是 `None`，页面把每个格子都涂成"从空值改起"——
    假证据现在离用户只有一步（P31-A 把它搬到了 apply 页上）。
    """

    def test_a_tsv_repair_shows_its_real_before_value_not_an_empty_cell(
        self, tmp_path: Path
    ) -> None:
        client = TestClient(create_app(project=tmp_path))
        csv = tmp_path / "wide.tsv"
        csv.write_text("id\tname\n1\t  Alice \n2\tBob\n", encoding="utf-8")
        run_id = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        issue_id = next(
            i["id"]
            for i in client.get(f"/scans/{run_id}/issues").json()
            if "leading_or_trailing_whitespace" in i["detector_ids"]
        )
        client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "propose"},
        )
        page = client.post(
            f"/ui/scans/{run_id}/issues/{issue_id}",
            data={"source_path": str(csv), "action": "apply"},
            follow_redirects=True,
        )
        cells = _diff_cells(page.text)
        assert cells == ["  Alice ", "Alice"], f"the page rendered {cells}"
        assert "\u2205" not in _page_body(page.text), "an empty cell was rendered as evidence"


class TestDiffHighlightAgreesWithTheChange:
    """`_diff_table` 高亮哪些格子，必须和 `table_diff` 判定"这行变了"用同一个谓词。

    两处各写一遍 `!=` 的话，NaN 那一格会在整行被判"有变更"时被涂成红/绿，而它两侧其实是
    同一个值——审计页面上凭空多出一条没发生的改写。
    """

    def test_a_nan_cell_is_not_highlighted_when_another_cell_moved(self) -> None:
        from datasentry.ui import _diff_table

        nan = float("nan")
        html = _diff_table(
            ["score", "name"],
            [[nan, "  Alice "], [nan, "Bob"]],
            [[nan, "Alice"], [nan, "Bob"]],
            [0],
        )
        assert '<td class="diff-del"><code>  Alice </code></td>' in html
        assert '<td class="diff-add"><code>Alice</code></td>' in html
        assert html.count("diff-del") == 1, "the unchanged NaN cell was highlighted too"
        assert html.count("diff-add") == 1
