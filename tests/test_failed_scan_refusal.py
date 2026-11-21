"""`O-2`：来源扫描不是 `completed` 的 issue 不得进入修复链路。

一个扫描有多个检测器；前面检测器成功入库的 issue 在后面检测器 crash 时并不会撤回，
于是 store 里挂着 `status="failed"` 的 scan run + 它的 issues。修复链路此前不看
scan run 的 status——issue 的 `scan_run_id` 指向失败的扫描照样能修。

覆盖四个修复入口（`repair_propose` / `repair_propose_ai` / `repair_preview` /
`repair_apply`）与 CLI 退出码：策略拒绝必须是 3，而不是参数错误的 2。
"""

from __future__ import annotations

import pathlib

import pytest

from datasentry import DataSentry
from datasentry.cli import main
from datasentry_core.errors import ErrorKind, PolicyRefusalError


@pytest.fixture()
def failed_scan_client(tmp_path: pathlib.Path) -> tuple[DataSentry, str]:
    """一个 store 里有 `status="failed"` 的 scan run + 挂在它底下的 issue 的 client。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    csv = tmp_path / "d.csv"
    csv.write_text("id,email\n1,a@b.com\n2,x@y.com\n", encoding="utf-8")
    client = DataSentry(project=ws)
    run, _runs, _issues = client.scan_file(str(csv))
    # 把 scan run 的 status 改成 failed，模拟"后面的检测器 crash 导致扫描整体失败"
    client._store._conn.execute("UPDATE scan_runs SET status = 'failed' WHERE id = ?", (run.id,))
    client._store._conn.commit()
    return client, run.id


class TestFailedScanRefusesRepair:
    """`O-2`（追加 3fi）：来源扫描不是 completed 的 issue 不能进修复链路。"""

    def test_repair_propose_raises_on_failed_scan(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        client, run_id = failed_scan_client
        issues = client._store.get_issues(run_id)
        assert issues, "fixture has no issues to test with"
        issue_id = issues[0].id
        csv = tmp_path / "d.csv"
        with pytest.raises(ValueError, match="status='failed'"):
            client.repair_propose(issue_id, str(csv))

    def test_repair_apply_raises_on_failed_scan(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        client, run_id = failed_scan_client
        issues = client._store.get_issues(run_id)
        issue_id = issues[0].id
        csv = tmp_path / "d.csv"
        with pytest.raises(ValueError, match="status='failed'"):
            client.repair_apply(issue_id, str(csv))

    def test_refusal_is_a_policy_refusal(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        """拒绝必须是 `PolicyRefusalError`：裸 `ValueError` 在 CLI `repair preview` 落到
        `EXIT_CONFIG`(2)，把"这次修复的前提不成立"说成"参数写错"。三面读数见追加 3fi 台账行。"""
        client, run_id = failed_scan_client
        issue_id = client._store.get_issues(run_id)[0].id
        with pytest.raises(PolicyRefusalError) as caught:
            client.repair_propose(issue_id, str(tmp_path / "d.csv"))
        assert caught.value.kind is ErrorKind.POLICY_REFUSAL
        assert caught.value.exit_code == 3
        assert caught.value.http_status == 422
        assert caught.value.mcp_code == -32602

    def test_completed_scan_still_works(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        """对照：把 status 改回 completed 后，修复链路照常工作。"""
        client, run_id = failed_scan_client
        client._store._conn.execute(
            "UPDATE scan_runs SET status = 'completed' WHERE id = ?", (run_id,)
        )
        client._store._conn.commit()
        issues = client._store.get_issues(run_id)
        issue_id = issues[0].id
        csv = tmp_path / "d.csv"
        # 不 raise——completed 扫描的 issue 可以修复
        client.repair_propose(issue_id, str(csv))


class TestFailedScanRefusesAiRepair:
    """AI 提案入口与其余三个入口共用同一道 `O-2` 校验。"""

    def test_repair_propose_ai_raises_on_failed_scan(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        """未配置 LLM 的环境下也必须先拒——校验在 `AIRepairService` 之前。

        若校验排到 service 之后，这里拿到的是 `LLMNotConfiguredError`，
        用户会以为"配个 key 就能修"，而不是"这次扫描本身不可用"。
        """
        client, run_id = failed_scan_client
        issue_id = client._store.get_issues(run_id)[0].id
        with pytest.raises(PolicyRefusalError, match="status='failed'"):
            client.repair_propose_ai(issue_id, str(tmp_path / "d.csv"))

    def test_repair_propose_ai_completed_scan_hits_llm_gate(
        self,
        failed_scan_client: tuple[DataSentry, str],
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """对照：completed 扫描不被误伤，照常走到 LLM 门。"""
        from datasentry import repair_ai
        from datasentry.llm_providers import NullProvider
        from datasentry_core.llm.provider import LLMNotConfiguredError

        monkeypatch.setattr(repair_ai, "create_provider", lambda: NullProvider())
        client, run_id = failed_scan_client
        client._store._conn.execute(
            "UPDATE scan_runs SET status = 'completed' WHERE id = ?", (run_id,)
        )
        client._store._conn.commit()
        issue_id = client._store.get_issues(run_id)[0].id
        with pytest.raises(LLMNotConfiguredError):
            client.repair_propose_ai(issue_id, str(tmp_path / "d.csv"))


class TestFailedScanRefusalCliExitCode:
    """CLI 面：`repair propose` 两条路径都是策略拒绝 exit 3，不是 EXIT_CONFIG(2)。"""

    def test_repair_propose_cli_exits_3(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        client, run_id = failed_scan_client
        ws = client.workspace
        issue_id = client._store.get_issues(run_id)[0].id
        client.close()
        code = main(
            [
                "--project",
                str(ws),
                "--format",
                "json",
                "repair",
                "propose",
                issue_id,
                "--file",
                str(tmp_path / "d.csv"),
            ]
        )
        assert code == 3

    def test_repair_propose_ai_cli_exits_3(
        self, failed_scan_client: tuple[DataSentry, str], tmp_path: pathlib.Path
    ) -> None:
        """`--ai` 路径：未配置 LLM 时也要先得到 3，而不是 LLM 未配置的 2。"""
        client, run_id = failed_scan_client
        ws = client.workspace
        issue_id = client._store.get_issues(run_id)[0].id
        client.close()
        code = main(
            [
                "--project",
                str(ws),
                "--format",
                "json",
                "repair",
                "propose",
                issue_id,
                "--file",
                str(tmp_path / "d.csv"),
                "--ai",
            ]
        )
        assert code == 3
