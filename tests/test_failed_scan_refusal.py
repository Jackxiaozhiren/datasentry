"""`repair_propose` / `repair_apply` 对 `status != "completed"` 的来源扫描的拒绝（`O-2`）。

一个扫描有多个检测器；前面检测器成功入库的 issue 在后面检测器 crash 时并不会撤回，
于是 store 里挂着 `status="failed"` 的 scan run + 它的 issues。修复链路此前不看
scan run 的 status——issue 的 `scan_run_id` 指向失败的扫描照样能修。
"""

from __future__ import annotations

import pathlib

import pytest

from datasentry import DataSentry
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
