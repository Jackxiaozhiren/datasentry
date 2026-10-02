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
        # D5-06：health 只暴露目录名，不回显服务端绝对路径。
        assert body["workspace"] == tmp_path.name
        assert str(tmp_path) not in resp.text

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

    def test_scan_progress_latest(self, tmp_path: Path) -> None:
        """V25：GET /scans/progress/latest 返回最近更新快照（含 path）。"""
        from datasentry.api import _SCAN_PROGRESS

        _SCAN_PROGRESS.clear()
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        assert client.get("/scans/progress/latest").status_code == 404
        resp = client.post("/scans", json={"path": str(csv)})
        assert resp.status_code == 201
        latest = client.get("/scans/progress/latest")
        assert latest.status_code == 200
        body = latest.json()
        assert body["path"] == str(csv)
        assert body["scanning"] is False
        assert body["done"] == 39

    def test_ui_scan_batch_multi_file(self, tmp_path: Path) -> None:
        """V25：/ui/scans 逗号分隔多文件 → 批量扫描 → 跳列表页。"""
        csv1 = _sample_csv(tmp_path)
        csv2 = tmp_path / "b.csv"
        csv2.write_text(
            "name,status,price\nx,Active,10\ny,n/a,9999\nz,inactive,250\n",
            encoding="utf-8",
        )
        client = TestClient(create_app(project=tmp_path), follow_redirects=False)
        resp = client.post("/ui/scans", data={"path": f"{csv1}, {csv2}"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/scans", "批量完成跳列表页"
        assert len(client.get("/scans").json()) == 2

    def test_ui_scan_glob_expands(self, tmp_path: Path) -> None:
        """V25：/ui/scans 支持 * 通配展开。"""
        _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path), follow_redirects=False)
        resp = client.post("/ui/scans", data={"path": f"{tmp_path / '*.csv'}"})
        assert resp.status_code == 303
        assert len(client.get("/scans").json()) == 1

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

    def test_repair_verify_and_diff_endpoints(self, tmp_path: Path) -> None:
        """V41/V46：REST verify（重扫报告）+ diff（变更行 JSON）。"""
        csv = _sample_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path))
        scan = client.post("/scans", json={"path": str(csv)}).json()
        run_id = scan["run"]["id"]
        issues = scan["issues"]
        target = next(i for i in issues if i["issue_type"] == "string_format")
        rep = client.post(
            f"/scans/{run_id}/repairs/apply",
            json={"issue_id": target["id"], "source_path": str(csv)},
        )
        assert rep.status_code == 200
        repair_run_id = rep.json()["id"]

        verify = client.post(f"/repairs/{repair_run_id}/verify")
        assert verify.status_code == 200
        v = verify.json()
        assert v["verify_scan_run_id"].startswith("scan_")
        assert v["verify_issue_count"] < v["source_issue_count"]

        diff = client.get(f"/repairs/{repair_run_id}/diff")
        assert diff.status_code == 200
        d = diff.json()
        assert d["run_id"] == repair_run_id
        assert d["columns"]
        assert d["changed_rows"], "expected at least one changed row"
        row = d["changed_rows"][0]
        assert "before" in row and "after" in row and row["line"] >= 2
        assert row["before"] != row["after"]

        missing = client.post("/repairs/no_such/verify")
        assert missing.status_code == 404

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


class TestFourxxCarriesNoServerPaths:
    """D2-02 / D5-06（阶段 2 行 r13）：4xx 保留原因、去掉服务端路径；真故障不伪装成 404。"""

    def test_unknown_repair_run_does_not_echo_a_path(self, tmp_path: Path) -> None:
        client = TestClient(create_app(project=tmp_path))
        resp = client.post("/repairs/rep_missing/verify")
        assert resp.status_code == 404
        assert str(tmp_path) not in resp.text

    def test_rollback_failure_is_500_not_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken backend used to surface as 404 with the raw exception text, which reads to a
        client as "your id is wrong" and to an attacker as a filesystem map."""
        app = create_app(project=tmp_path)

        def explode(run_id: str) -> None:
            raise RuntimeError("warehouse exploded while opening /var/lib/datasentry/main.db")

        monkeypatch.setattr(app.state.client, "repair_rollback", explode)
        resp = TestClient(app, raise_server_exceptions=False).post("/repairs/rep_1/rollback")
        assert resp.status_code == 500
        assert "warehouse exploded" not in resp.text
        assert "/var/lib/datasentry" not in resp.text

    def test_reason_survives_redaction(self, tmp_path: Path) -> None:
        """The message must stay actionable: only the path is replaced, not the whole detail."""
        from datasentry.redact import safe_detail

        raw = "[Errno 2] No such file or directory: '/srv/ds/workspace/orders.csv'"
        assert safe_detail(FileNotFoundError(raw)) == (
            "[Errno 2] No such file or directory: '<path>'"
        )
        assert safe_detail(KeyError("repair run rep_9 not found")) == (
            "'repair run rep_9 not found'"
        )
        assert safe_detail(ValueError("")) == "ValueError"

    def test_spaced_and_apostrophised_paths_are_fully_redacted(self) -> None:
        """A-8 第一半：字符类排除空白与撇号，于是"遮到第一个空格为止"，尾段照旧外泄。

        macOS 与挂载点里带空格的路径是常态（`Application Support`、`My Documents`），
        而这条控制声称的正是"不外泄服务端文件系统地图"。
        """
        from datasentry.redact import safe_detail

        cases = {
            "data source not found: /srv/My Data/orders.csv": "data source not found: <path>",
            'open file "/srv/prod data/orders.csv": permission denied': (
                'open file "<path>": permission denied'
            ),
            "[Errno 2] No such file or directory: '/home/O'Brien/vault.key'": (
                "[Errno 2] No such file or directory: '<path>'"
            ),
            "cannot read C:\\Users\\Zhi Ren\\orders.csv": "cannot read <path>",
            "read /srv/My\tData/orders.csv then failed": "read <path> then failed",
            "cannot open \\\\nas\\share\\fin.xlsx": "cannot open <path>",
        }
        for raw, want in cases.items():
            assert safe_detail(ValueError(raw)) == want, raw

    def test_prose_after_a_path_is_not_swallowed(self) -> None:
        """对照：脱敏不能顺手吃掉原因本身。"""
        from datasentry.redact import safe_detail

        assert safe_detail(ValueError("failed to read /tmp/x.csv but continued")) == (
            "failed to read <path> but continued"
        )
        assert safe_detail(ValueError("no repair proposal available for this issue")) == (
            "no repair proposal available for this issue"
        )
        assert safe_detail(ValueError("GET /v1/health returned 500")) == ("GET <path> returned 500")
        # 一个比例/日期/文档引用都不该把整条原因吃掉（复核 #5：旧规则"后面还有斜杠就一直吃"）
        assert safe_detail(ValueError("failed to read /srv/a/b.csv after 3/5 attempts")) == (
            "failed to read <path> after 3/5 attempts"
        )
        assert safe_detail(ValueError("expected /srv/a/b but got 12 of 20 rows/limit")) == (
            "expected <path> but got 12 of 20 rows/limit"
        )

    def test_documented_residual_a_stop_char_leaves_only_a_relative_fragment(self) -> None:
        """把边界钉在文案上：绝对前缀一定消失，剩下的只是文件名，不是文件系统地图。

        这条不是"顺手放宽"——它断言的是 `/srv` 与 `My Reports` 之前那段一定被遮掉，
        所以若有人把锚点改坏，这条会红。
        """
        from datasentry.redact import safe_detail

        out = safe_detail(ValueError("/srv/My Reports (2024)/orders.csv unreadable"))
        assert "/srv" not in out
        assert out.startswith("<path>")
        out2 = safe_detail(ValueError("cannot open /srv/Smith, John/x.csv"))
        assert "/srv" not in out2 and "Smith" not in out2

    def test_redaction_stays_linear_on_a_long_message(self) -> None:
        """复核 #1：逐字符再全文找分隔符 = 二次方，而 200 字截断发生在脱敏**之后**，拦不住它。

        边界放得极宽（旧实现 64 KB 输入实测 11.2 s，新实现 0.7 ms），只用来让"重新引入全文扫描"
        这件事一定响，不用来报性能数字。
        """
        import time

        from datasentry.redact import redact_paths

        text = "/a/b" + " x" * 32000 + " y/z"
        started = time.perf_counter()
        out = redact_paths(text)
        elapsed = time.perf_counter() - started
        assert "<path>" in out
        assert elapsed < 1.0, f"redaction went quadratic again: {elapsed:.2f}s on {len(text)} chars"

    def test_no_ui_face_interpolates_a_raw_exception(self) -> None:
        """A-8 第二半：脱敏函数一直在，十四个面只是从没调用它——而裸 `{exc}` 是第十五种形态。

        AST 而非 grep：`str(exc)` 出现在 f-string、dict 字面量、`HTMLResponse` 实参里形态各异，
        逐行文本匹配既漏也假阳性。两种形态都要抓：`str/repr(exc)`，以及 f-string 里裸插值的
        `{exc}`。唯一豁免是按内容分类的比较（`"no repair proposal" in str(exc)`）。
        """
        import ast
        import inspect

        import datasentry.api as mod

        tree = ast.parse(inspect.getsource(mod.create_app))
        parent: dict[int, ast.AST] = {}
        for holder in ast.walk(tree):
            for child in ast.iter_child_nodes(holder):
                parent[id(child)] = holder

        def is_exc(node: ast.AST | None) -> bool:
            return isinstance(node, ast.Name) and node.id == "exc"

        def flagged(node: ast.AST) -> bool:
            bare_in_fstring = is_exc(node) and isinstance(parent.get(id(node)), ast.FormattedValue)
            wrapped = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"str", "repr"}
                and bool(node.args)
                and is_exc(node.args[0])
            )
            classified = isinstance(parent.get(id(node)), ast.Compare)
            return bare_in_fstring or (wrapped and not classified)

        raw = sorted(node.lineno for node in ast.walk(tree) if flagged(node))
        assert raw == [], (
            f"api.py lines {raw} interpolate a raw exception into a response; route it through "
            "`safe_detail(exc)`. Only a content test like `... in str(exc)` may read it raw."
        )

    def test_ui_batch_rollback_page_does_not_echo_a_spaced_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = create_app(project=tmp_path)
        secret = "/srv/ds/My Documents/rep_9.before.csv"

        def explode(run_id: str) -> None:
            raise RuntimeError(f"cannot restore {secret}")

        monkeypatch.setattr(app.state.client, "repair_rollback", explode)
        resp = TestClient(app).post(
            "/ui/scans/scan_1/repairs/batch-rollback", data={"repair_run_ids": "rep_9"}
        )
        assert resp.status_code == 200
        assert "cannot restore" in resp.text
        assert secret not in resp.text
        assert "My Documents" not in resp.text

    def test_repair_verify_path_error_is_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Row r13's own delta: a 400 whose message carries a server path keeps the reason and
        loses the path. On HEAD this detail reached the client verbatim."""
        app = create_app(project=tmp_path)
        secret = "/var/lib/datasentry/workspace/.datasentry/repairs/rep_7.csv"

        def explode(repair_run_id: str) -> tuple[object, object]:
            raise FileNotFoundError(2, "No such file or directory", secret)

        monkeypatch.setattr(app.state.client, "repair_verify", explode)
        resp = TestClient(app).post("/repairs/rep_7/verify")
        assert resp.status_code == 400
        assert "No such file or directory" in resp.text
        assert secret not in resp.text
