"""D5-04/D5-09 回归：网络面扫描路径 workspace 收束。

- 单元：`scan_paths.resolve_allowed_scan_path` 对区内/区外/相对逃逸/
  设备路径/DSN/`ALLOWED_ROOTS` 的判定；
- 集成：REST `POST /scans`、UI `POST /ui/scans`、MCP `scan_file` 对
  `/etc/hosts` 一律拒绝，且响应体内不得出现文件内容。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app
from datasentry.mcp_server import McpServer
from datasentry.scan_paths import ScanPathRejected, resolve_allowed_scan_path

OUTSIDE = "/etc/hosts"


def _ws_csv(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "inside.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    return p


class TestResolve:
    def test_absolute_inside_allowed(self, tmp_path: Path) -> None:
        p = _ws_csv(tmp_path)
        assert resolve_allowed_scan_path(str(p), workspace=tmp_path / "ws") == str(p)

    def test_absolute_outside_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ScanPathRejected):
            resolve_allowed_scan_path(OUTSIDE, workspace=tmp_path / "ws")

    def test_parent_escape_rejected(self, tmp_path: Path) -> None:
        tricky = str(tmp_path / "ws" / ".." / "outside.csv")
        with pytest.raises(ScanPathRejected):
            resolve_allowed_scan_path(tricky, workspace=tmp_path / "ws")

    def test_device_paths_rejected(self, tmp_path: Path) -> None:
        for bad in ("/dev/null", "/proc/self/environ", "/sys/kernel/hostname"):
            with pytest.raises(ScanPathRejected):
                resolve_allowed_scan_path(bad, workspace=tmp_path / "ws")

    def test_dsn_passthrough(self, tmp_path: Path) -> None:
        dsn = "postgresql://u:p@h:5432/db"
        assert resolve_allowed_scan_path(dsn, workspace=tmp_path / "ws") == dsn

    def test_allowed_roots_extension(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        other = tmp_path / "shared"
        other.mkdir()
        target = other / "data.csv"
        target.write_text("a\n1\n", encoding="utf-8")
        with pytest.raises(ScanPathRejected):
            resolve_allowed_scan_path(str(target), workspace=tmp_path / "ws")
        monkeypatch.setenv("DATASENTRY_ALLOWED_ROOTS", str(other))
        assert resolve_allowed_scan_path(str(target), workspace=tmp_path / "ws") == str(target)

    def test_relative_escape_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(tmp_path / "ws")
        with pytest.raises(ScanPathRejected):
            resolve_allowed_scan_path("../outside.csv", workspace=tmp_path / "ws")


class TestNetworkSurfaces:
    def test_rest_rejects_outside(self, tmp_path: Path) -> None:
        _ws_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post("/scans", json={"path": OUTSIDE})
        assert resp.status_code == 422
        assert "outside workspace" in resp.json()["detail"]
        assert "localhost" not in resp.text  # 回读通道无文件内容外带

    def test_rest_allows_inside(self, tmp_path: Path) -> None:
        p = _ws_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post("/scans", json={"path": str(p)})
        assert resp.status_code == 201

    def test_ui_rejects_outside(self, tmp_path: Path) -> None:
        _ws_csv(tmp_path)
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post("/ui/scans", data={"path": OUTSIDE})
        assert resp.status_code == 422

    def test_mcp_rejects_outside(self, tmp_path: Path) -> None:
        _ws_csv(tmp_path)
        server = McpServer(project=tmp_path / "ws")
        try:
            resp = server._handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "scan_file",
                        "arguments": {"path": OUTSIDE},
                    },
                }
            )
            assert resp is not None
            # -32602, not -32603: this assertion used to pin the bug. A workspace refusal is a
            # client error, and answering it as "internal error" tells an id-correlating agent to
            # retry something that will never succeed (A-8-mcp).
            assert resp["error"]["code"] == -32602
            assert "outside workspace" in resp["error"]["message"]
        finally:
            server.close()

    def test_env_roots_open_rest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        other = tmp_path / "shared"
        other.mkdir()
        (other / "data.csv").write_text("a\n1\n", encoding="utf-8")
        monkeypatch.setenv("DATASENTRY_ALLOWED_ROOTS", str(other))
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post("/scans", json={"path": str(other / "data.csv")})
        assert resp.status_code == 201


def test_no_allowlist_means_no_second_root(tmp_path: Path, monkeypatch) -> None:
    """The sibling-root case, decided by behaviour.

    The previous version of this test asserted `os.environ.get(...) is None or isinstance(..., str)`
    -- true for every possible value, including the one that would mean a leaked allowlist.
    """
    monkeypatch.delenv("DATASENTRY_ALLOWED_ROOTS", raising=False)
    other = tmp_path / "shared"
    other.mkdir()
    (other / "data.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(ScanPathRejected):
        resolve_allowed_scan_path(str(other / "data.csv"), workspace=tmp_path / "ws")


class TestSchemeSpelling:
    """A-10：`_is_remote()` 大小写不敏感，而每个消费方都按精确小写派发。

    于是 `S3://…` 这类拼法被闸门当成远程源**原样放行**（不 resolve、不比根、也不锚工作区），
    再由 `client.scan_file` 落回本地文件分支、相对 **CWD** 解析。服务进程站在项目祖先目录时
    （`Dockerfile` 的 `WORKDIR /app` + `DATASENTRY_PROJECT=/app/workspace` 正是这种关系），
    这就是 r5/A-1 那条"锚在 CWD 等于锚错地方"的同一种逃逸，只是换了个入口拼法。
    """

    @pytest.mark.parametrize(
        "spelling",
        [
            "S3://../../etc/passwd.csv",
            "Gs://../../etc/x.csv",
            "HTTP://../../etc/y.csv",
        ],
    )
    def test_an_uppercase_scheme_cannot_wave_an_escape_past_containment(
        self, tmp_path: Path, spelling: str
    ) -> None:
        with pytest.raises(ScanPathRejected) as caught:
            resolve_allowed_scan_path(spelling, workspace=tmp_path / "ws")
        assert "unsupported source scheme" in str(caught.value)

    @pytest.mark.parametrize(
        "spelling",
        ["FILE:///etc/passwd.csv", "https://example.com/x.csv", "webhook://svc/task"],
    )
    def test_a_scheme_shaped_string_is_rejected_not_anchored_into_the_workspace(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """复核 #4：`gcs://` 从表里删掉之后，它仍会被当成"工作区里的一个怪目录"。

        `FILE:///etc/passwd.csv` 被收下（而 `file:///etc/passwd.csv` 被拒）——大小写不对称
        只是换了个入口：闸门把 URL 报成"工作区内路径"，这比拒绝更误导。RFC 3986 里方案名
        本来就不分大小写，所以正确回答是"我不支持这个方案"，而不是"这是个工作区里的目录"。
        """
        with pytest.raises(ScanPathRejected):
            resolve_allowed_scan_path(spelling, workspace=tmp_path / "ws")

    @pytest.mark.parametrize(
        "spelling",
        [
            "s3://bucket/key.csv",
            "gs://b/o.csv",
            "az://c/d.csv",
            "postgresql://h/db/t",
            "postgres://h/db/t",
            "mysql://h/db/t",
        ],
    )
    def test_every_scheme_the_consumers_actually_dispatch_still_passes_through(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """对照集与派发集逐一对应：收紧方案表不能顺手关掉真实能力。"""
        assert resolve_allowed_scan_path(spelling, workspace=tmp_path / "ws") == spelling

    def test_the_gate_admits_exactly_what_the_consumer_dispatches(self) -> None:
        """一条集合等式看住两个面不再各自长表——这才是本行的根因判据。

        派发集从 `client.scan_file` 的源码里取（`path.startswith("x://")` 的字面量），不是我在
        测试里重抄一遍：重抄的话消费方改了派发、闸门没改，这里照样绿。
        """
        import ast
        import inspect
        import textwrap

        from datasentry.client import DataSentry
        from datasentry.scan_paths import _REMOTE_SCHEMES
        from datasentry_core.connectors.remote_file import _CLOUD_PREFIXES

        tree = ast.parse(textwrap.dedent(inspect.getsource(DataSentry.scan_file)))
        dispatched: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.endswith("://")
            ):
                dispatched.add(node.args[0].value)
        assert dispatched, "scan_file no longer dispatches by prefix; this guard needs rewriting"
        assert set(_REMOTE_SCHEMES) == dispatched | set(_CLOUD_PREFIXES), (
            f"gate admits {sorted(_REMOTE_SCHEMES)} but the consumer dispatches "
            f"{sorted(dispatched | set(_CLOUD_PREFIXES))}"
        )

    def test_the_rest_face_refuses_an_uppercase_escape(self, tmp_path: Path) -> None:
        """门面默认不 enforce（`client.py:70`），真正由调用方控制的是 REST 面。

        目标文件必须真的存在，否则先撞上"找不到文件"，测的就不是收束了。
        """
        _ws_csv(tmp_path)
        # 两级 `..`：`AZ:` 这一组件本身要被 `..` 吃掉一层才谈得上逃逸
        (tmp_path / "outside.csv").write_text("name,v\n payroll ,9\n", encoding="utf-8")
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post("/scans", json={"path": "AZ://../../outside.csv"})
        assert resp.status_code == 422
        assert "unsupported source scheme" in resp.json()["detail"]
        assert resp.status_code == 422  # refused at the gate; never opened against the CWD

    def test_the_mcp_face_refuses_an_uppercase_escape(self, tmp_path: Path) -> None:
        _ws_csv(tmp_path)
        (tmp_path / "outside.csv").write_text("name,v\n payroll ,9\n", encoding="utf-8")
        server = McpServer(project=tmp_path / "ws")
        try:
            resp = server._handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "scan_file",
                        "arguments": {"path": "S3://../../outside.csv"},
                    },
                }
            )
            assert resp["error"]["code"] == -32602
            assert "unsupported source scheme" in resp["error"]["message"]
        finally:
            server.close()


class TestFacadeOpensWhatItValidated:
    """复核 #2：门面调用闸门却**丢掉返回值**，于是校验一个文件、打开另一个文件。

    `client.py:483` 写成 `resolve_allowed_scan_path(str(path), ...)` 而不接返回值，
    下面仍用原始 `path` 打开。对相对路径而言，闸门判的是 `workspace/orders.csv`，
    真正打开的是 **CWD** 下的 `orders.csv`——正是 `scan_paths.py` 自己写着要防的那件事
    （r5/A-1 的同一形状）。REST/MCP/UI 都在调用前自己锚过，所以这条今天不被这些面触发；
    它的危险在于：这个旗标的全部意义就是收束，而它收束的不是被打开的那个文件。
    """

    def test_a_relative_path_opens_the_workspace_file_it_validated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datasentry.client import DataSentry

        base = tmp_path / "app"
        ws = base / "workspace"
        ws.mkdir(parents=True)
        (base / "orders.csv").write_text("name,v\n secret_ssn ,1\n", encoding="utf-8")
        (ws / "orders.csv").write_text("alpha,beta,gamma\n 1,2,3\n", encoding="utf-8")
        monkeypatch.chdir(base)

        facade = DataSentry(project=ws, enforce_scan_containment=True)
        try:
            relative = facade.scan_file("orders.csv")[0]
            absolute = facade.scan_file(str(ws / "orders.csv"))[0]
        finally:
            facade.close()

        assert Path(relative.source_path).resolve() == (ws / "orders.csv").resolve(), (
            f"persisted the unanchored {relative.source_path!r}"
        )
        # `created_at` differs between two scans by design; these four say *which file* was read.
        identity = ("file_sha256", "schema_hash", "row_count", "column_signature")
        rel_fp = {f: getattr(relative.fingerprint, f) for f in identity}
        abs_fp = {f: getattr(absolute.fingerprint, f) for f in identity}
        assert rel_fp == abs_fp, "the relative scan read a different file than the gate validated"
        assert rel_fp["column_signature"] and "alpha" in str(rel_fp["column_signature"]), (
            "neither file was the workspace one"
        )


class TestSchedulerFaceContained:
    """D5-13 (row r15): the job face is a scan the caller will not be watching."""

    def test_job_path_outside_the_workspace_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.csv").write_text("a\n1\n", encoding="utf-8")
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post(
            "/jobs",
            json={
                "name": "snoop",
                "path": str(outside / "secret.csv"),
                "cron": "5 * * * *",
            },
        )
        assert resp.status_code == 422
        assert "outside workspace" in resp.json()["detail"]

    def test_job_cannot_relocate_the_workspace(self, tmp_path: Path) -> None:
        """`project` used to be free text: the executor created a store wherever it pointed."""
        elsewhere = tmp_path / "victim"
        elsewhere.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "data.csv").write_text("a\n1\n", encoding="utf-8")
        client = TestClient(create_app(project=ws))
        resp = client.post(
            "/jobs",
            json={
                "name": "relocate",
                "path": str(ws / "data.csv"),
                "cron": "5 * * * *",
                "project": str(elsewhere),
            },
        )
        assert resp.status_code == 422
        assert "DATASENTRY_ALLOWED_ROOTS" in resp.json()["detail"]
        assert not (elsewhere / ".datasentry").exists()

    def test_allowed_roots_open_the_job_face_too(self, tmp_path: Path, monkeypatch) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "data.csv").write_text("a\n1\n", encoding="utf-8")
        monkeypatch.setenv("DATASENTRY_ALLOWED_ROOTS", str(shared))
        client = TestClient(create_app(project=tmp_path / "ws"))
        resp = client.post(
            "/jobs",
            json={"name": "ok", "path": str(shared / "data.csv"), "cron": "5 * * * *"},
        )
        assert resp.status_code == 201, resp.text

    def test_the_client_flag_confines_and_the_default_does_not(self, tmp_path: Path) -> None:
        """The containment is per face: the CLI keeps the operator's local trust boundary."""
        from datasentry.client import DataSentry

        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside.csv"
        outside.write_text("a\n1\n", encoding="utf-8")

        guarded = DataSentry(project=ws, enforce_scan_containment=True)
        with pytest.raises(ScanPathRejected):
            guarded.scan_file(str(outside))
        # Provisioning the workspace store is not the defect; recording an out-of-workspace
        # scan is. Nothing may have been scanned, and the target directory stays untouched.
        assert guarded.list_scan_runs() == []
        assert not (outside.parent / ".datasentry").exists()

        local = DataSentry(project=ws)
        run, _runs, _issues = local.scan_file(str(outside))
        assert run.status == "completed"


class TestSchedulerGuardIsStructural:
    """Every face must share one choke point, so a forgotten call site cannot re-open it."""

    def test_the_client_enforces_not_only_the_endpoints(self) -> None:
        """AST, not text: an enforcement line parked inside a docstring would satisfy a grep.

        That is not hypothetical -- the first version of this row's change landed exactly there
        and every text-based check still passed.
        """
        import ast
        import inspect
        import textwrap

        from datasentry.client import DataSentry

        assert "enforce_scan_containment" in inspect.signature(DataSentry.__init__).parameters
        tree = ast.parse(textwrap.dedent(inspect.getsource(DataSentry.scan_file)))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        guarded = {
            node.test.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Attribute)
        }
        assert "resolve_allowed_scan_path" in called, sorted(called)
        assert "_enforce_scan_containment" in guarded, sorted(guarded)


class TestRelativePathAnchor:
    """D5-04 (row r5): relative paths mean "relative to the project", not to the process CWD."""

    def test_relative_target_is_resolved_against_the_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path
        ws = parent / "ws"
        ws.mkdir()
        # The same relative name exists in both places, with different content, so the result
        # says which one was read instead of merely saying "it worked".
        (ws / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        (parent / "orders.csv").write_text("ssn\n999-99-9999\n", encoding="utf-8")
        monkeypatch.chdir(parent)
        client = TestClient(create_app(project=ws))
        resp = client.post("/scans", json={"path": "orders.csv"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["run"]["source_path"] == str(ws / "orders.csv")

    def test_relative_path_cannot_reach_a_sibling_of_the_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Dockerfile layout: WORKDIR /app with DATASENTRY_PROJECT=/app/workspace."""
        parent = tmp_path
        ws = parent / "workspace"
        ws.mkdir()
        (parent / "secret.csv").write_text("ssn\n999-99-9999\n", encoding="utf-8")
        monkeypatch.chdir(parent)
        client = TestClient(create_app(project=ws))
        for body in ("secret.csv", "workspace/../secret.csv", "../secret.csv"):
            resp = client.post("/scans", json={"path": body})
            assert resp.status_code in (404, 422), (
                f"{body!r} -> {resp.status_code} {resp.text[:80]}"
            )
            assert "999-99-9999" not in resp.text

    def test_the_validated_form_is_what_gets_returned(self) -> None:
        """Validating a stripped string and returning the raw one is a bypass with extra steps."""
        resolved = resolve_allowed_scan_path("  sub/a.csv  ", workspace="/srv/proj")
        assert resolved == "/srv/proj/sub/a.csv"


class TestRepairSourcePathConfinement:
    """D5-09 remainder: a repair reads *and writes beside* its `source_path`, so it is path input.

    Written against a real issue, because an unknown id reaches 422 with or without the gate -- the
    first version of these two tests did exactly that and stayed green with the gate removed.
    """

    @staticmethod
    def _scan_with_issue(client: TestClient, csv: Path) -> tuple[str, str]:
        run = client.post("/scans", json={"path": str(csv)}).json()["run"]["id"]
        issues = client.app.state.client.list_issues(scan_run_id=run)
        assert issues, "the fixture data must produce at least one repairable issue"
        return run, issues[0].id

    def test_rest_repair_faces_refuse_an_outside_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ALLOWED_ROOTS", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = ws / "orders.csv"
        csv.write_text("name,status\n alice ,Active\nbob,n/a\n", encoding="utf-8")
        outside = tmp_path / "elsewhere.csv"
        outside.write_text("name,status\n carol ,Active\ndave,n/a\n", encoding="utf-8")
        client = TestClient(create_app(project=ws))
        run, issue_id = self._scan_with_issue(client, csv)

        allowed = client.post(
            f"/scans/{run}/repairs/propose",
            json={"issue_id": issue_id, "source_path": str(csv)},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json() is not None, "the in-workspace source must still be repairable"

        refused = client.post(
            f"/scans/{run}/repairs/propose",
            json={"issue_id": issue_id, "source_path": str(outside)},
        )
        assert refused.status_code == 422, refused.text
        assert "outside workspace" in refused.json()["detail"]
        for face, url in (
            ("preview", f"/scans/{run}/repairs/preview"),
            ("apply", f"/scans/{run}/repairs/apply"),
        ):
            resp = client.post(url, json={"issue_id": issue_id, "source_path": str(outside)})
            assert resp.status_code == 422, f"{face}: {resp.status_code} {resp.text[:80]}"
        assert list((ws / ".datasentry" / "repairs").glob("*.csv")) == []

    def test_ui_repair_faces_refuse_in_html_not_json(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = ws / "orders.csv"
        csv.write_text("name,status\n alice ,Active\nbob,n/a\n", encoding="utf-8")
        outside = tmp_path / "elsewhere.csv"
        outside.write_text("name,status\n carol ,Active\n", encoding="utf-8")
        client = TestClient(create_app(project=ws))
        run, issue_id = self._scan_with_issue(client, csv)

        resp = client.post(
            f"/ui/scans/{run}/repairs/batch-propose",
            data={"issue_ids": issue_id, "source_path": str(outside)},
        )
        assert resp.status_code == 422, resp.text[:120]
        assert resp.headers["content-type"].startswith("text/html")
        assert "outside workspace" in resp.text
        assert str(outside) not in resp.text


class TestUiScanBatchFaceIsAnchored:
    """`POST /ui/scans` 的批量面必须与 REST 面同一基准（独立复核 A-1）。

    r5 在 `resolve_allowed_scan_path` 内部修好了"按工作区判、按 CWD 开"的错配，却在
    `_expand_scan_paths` 这个调用方把它重新引入：校验返回值被丢弃，交给下游的仍是原始相对串。
    """

    def test_relative_path_outside_the_workspace_is_not_scanned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        ws = tmp_path / "proj"
        ws.mkdir()
        (ws / "inside.csv").write_text("a,b\n1, 2 \n", encoding="utf-8")
        (tmp_path / "outside.csv").write_text("secret,value\n ssn ,1\n", encoding="utf-8")
        # The shipped container shape: WORKDIR is the workspace's parent.
        monkeypatch.chdir(tmp_path)
        client = TestClient(create_app(project=ws))
        resp = client.post("/ui/scans", data={"path": "outside.csv"}, follow_redirects=False)
        assert resp.status_code in (404, 422), (
            f"out-of-workspace file was accepted: {resp.status_code}"
        )
        assert client.get("/scans").json() == [], "the escape scanned and persisted a run"

    def test_workspace_relative_glob_still_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """收束不能把功能一起关掉：工作区内的 glob 模式要仍能匹配（改前按 CWD 展开，必然落空）。"""
        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        ws = tmp_path / "proj"
        ws.mkdir()
        (ws / "cust_a.csv").write_text("a,b\n1, 2 \n", encoding="utf-8")
        (ws / "cust_b.csv").write_text("a,b\n3,  \n", encoding="utf-8")
        (tmp_path / "payroll.csv").write_text("secret,value\n ssn ,1\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        client = TestClient(create_app(project=ws))
        resp = client.post("/ui/scans", data={"path": "cust*.csv"}, follow_redirects=False)
        assert resp.status_code == 303, resp.text[:200]
        runs = client.get("/scans").json()
        assert len(runs) == 2, runs
        for rid in runs:
            src = client.get(f"/scans/{rid}").json()["source_path"]
            assert Path(src).resolve().is_relative_to(ws.resolve()), src
