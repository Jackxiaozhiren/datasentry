"""Detector re-scan diff（O-D5-01 第二块砖）：重扫 fixture 后比对 issue 集，量化检测器变更的影响。

为什么需要它：``replay`` 只能重算分数，不能重算检测——store 存的是 issue 快照，不是源数据行。
检测器阈值、规则或集合一变，历史 run 的 issue 集就会与当前代码产出的不同，而两份结果都叫
"这次扫描的问题数"。本模块提供可归档的集合差异，使"哪一份检测器代码"这个问题有答案。

设计约束（三条，前两条与 ``scoring.replay`` 同源）：

1. **只比对，不做检测。** 本模块不打开文件、不跑检测器；它接收两侧的 ``Issue`` 集合并返回差异。
   触发重扫、决定可比性是调用方（``datasentry.client.DataSentry.rescan_runs``）的职责。
   把 I/O 混进纯比对会让"差异"与"读取失败"在类型上无法区分。
2. **不可比不等于无差异。** 源文件缺失、源已变更、远程源、重扫失败——这些行 ``status`` 非
   ``compared``，``drifted`` 恒为 False，同时计入 ``uncovered``。把不可比渲染成"通过"是这类
   工具最常见的谎报方式（AC-11b 明确禁止用 AC-11a 的归档冒充证据）。
3. **身份键含检测器集。** 键是 ``(issue_type, table_name, columns, detector_ids)``：检测器
   增删会改变融合结果的键，因而落到 ``added`` / ``removed``，而不是被误判成"同一问题数值变了"。
   键不含 ``issue.id``（每次扫描新建）也不含 ``affected_row_ids``（行集变动会把一条问题拆成
   多条噪声）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from datasentry_core.models.issue import Issue

#: ``compared`` 以外的状态都表示"这一行没有完成比对"，不是"没有差异"。
RescanStatus = Literal[
    "compared",
    "run_incomplete",
    "source_missing",
    "source_unsupported",
    "source_changed",
    "scan_failed",
]

#: 本行比的是哪一份字节：``fixture`` = 留存副本，``origin`` = run 记录的原始路径。
SourceKind = Literal["fixture", "origin", "none"]

#: 状态到人读原因的默认解释；调用方可以补充更具体的 ``note``。
STATUS_LABELS: dict[str, str] = {
    "compared": "已比对",
    "run_incomplete": "历史 run 不是 completed 状态, 其 issue 集不是完整基线",
    "source_missing": "fixture 不存在, 无法重扫",
    "source_unsupported": "源类型不支持重扫 (远程源 / 缺 table_name / 未知格式)",
    "source_changed": "源文件内容与扫描时不同, 差异无法归因到检测器",
    "scan_failed": "重扫过程抛错, 未产出可比 issue 集",
}


@dataclass(frozen=True, slots=True)
class IssueSignature:
    """一条 issue 的可比投影：只保留跨 run 稳定的字段。"""

    issue_type: str
    table_name: str | None
    columns: tuple[str, ...]
    detectors: tuple[str, ...]
    severity: str
    affected_count: int

    def key(self) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
        """身份键。列与检测器排序后比较，避免同一条问题因顺序不同被判成两条。"""
        return (self.issue_type, self.table_name or "", self.columns, self.detectors)

    def render(self) -> str:
        """单行摘要，用于 markdown 明细。"""
        cols = ",".join(self.columns) or "-"
        detectors = ",".join(self.detectors) or "-"
        where = f"{self.table_name}." if self.table_name else ""
        return (
            f"{where}{self.issue_type}[{cols}] "
            f"({detectors}; {self.severity}; {self.affected_count} rows)"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "issue_type": self.issue_type,
            "table_name": self.table_name,
            "columns": list(self.columns),
            "detectors": list(self.detectors),
            "severity": self.severity,
            "affected_count": self.affected_count,
        }


@dataclass(frozen=True, slots=True)
class IssueChange:
    """同一身份键下数值变了：影响的字段名 + 前后投影。"""

    before: IssueSignature
    after: IssueSignature
    fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.before.render(),
            "fields": list(self.fields),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


def signature(issue: Issue) -> IssueSignature:
    """``Issue`` → 可比投影。``severity`` 取枚举值，避免输出里出现 ``Severity.MEDIUM``。"""
    severity = getattr(issue.severity, "value", issue.severity)
    return IssueSignature(
        issue_type=issue.issue_type,
        table_name=issue.table_name,
        columns=tuple(sorted(issue.columns)),
        detectors=tuple(sorted(issue.detector_ids)),
        severity=str(severity),
        affected_count=issue.affected_count,
    )


def _index(
    issues: list[Issue],
) -> tuple[dict[tuple[str, str, tuple[str, ...], tuple[str, ...]], IssueSignature], int]:
    """按身份键建索引。键重复时保留后者并计数——静默覆盖会让重复本身不可见。"""
    indexed: dict[tuple[str, str, tuple[str, ...], tuple[str, ...]], IssueSignature] = {}
    duplicates = 0
    for issue in issues:
        sig = signature(issue)
        if sig.key() in indexed:
            duplicates += 1
        indexed[sig.key()] = sig
    return indexed, duplicates


def diff_issue_sets(
    run_id: str,
    stored: list[Issue],
    rescanned: list[Issue],
    *,
    status: RescanStatus = "compared",
    note: str = "",
    source_kind: SourceKind = "none",
) -> IssueSetDiff:
    """比对存档 issue 集与重扫 issue 集。

    ``status`` 非 ``compared`` 时调用方通常传空 ``rescanned``；此时不计算增删，只保留原因，
    以免把"没扫到"展示成"问题消失了"。``source_kind`` 记录这一行比的是留存副本还是原始文件，
    归档里必须能分辨——副本与原始文件的差异含义不同。
    """
    if status != "compared":
        return IssueSetDiff(
            run_id=run_id,
            status=status,
            note=note or STATUS_LABELS.get(status, status),
            source_kind="none",
        )

    before, before_dupes = _index(stored)
    after, after_dupes = _index(rescanned)

    added = tuple(after[key] for key in sorted(after.keys() - before.keys()))
    removed = tuple(before[key] for key in sorted(before.keys() - after.keys()))

    changed: list[IssueChange] = []
    for key in sorted(before.keys() & after.keys()):
        old = before[key]
        new = after[key]
        fields: list[str] = []
        if old.severity != new.severity:
            fields.append("severity")
        if old.affected_count != new.affected_count:
            fields.append("affected_count")
        if fields:
            changed.append(IssueChange(before=old, after=new, fields=tuple(fields)))

    unchanged = len(before.keys() & after.keys()) - len(changed)
    return IssueSetDiff(
        run_id=run_id,
        status="compared",
        source_kind=source_kind,
        added=added,
        removed=removed,
        changed=tuple(changed),
        unchanged=unchanged,
        duplicate_keys=before_dupes + after_dupes,
        note=note,
    )


@dataclass(frozen=True, slots=True)
class IssueSetDiff:
    """一条 run 的重扫结论。``drifted`` 只在 ``compared`` 时可能为真。"""

    run_id: str
    status: RescanStatus = "compared"
    source_kind: SourceKind = "none"
    added: tuple[IssueSignature, ...] = ()
    removed: tuple[IssueSignature, ...] = ()
    changed: tuple[IssueChange, ...] = ()
    unchanged: int = 0
    duplicate_keys: int = 0
    note: str = ""

    @property
    def comparable(self) -> bool:
        """是否真的完成了比对；False 时不得当作"无差异"。"""
        return self.status == "compared"

    @property
    def drifted(self) -> bool:
        """当前检测器代码是否改变了这条 run 的 issue 集。"""
        return self.comparable and bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "source": self.source_kind,
            "comparable": self.comparable,
            "drifted": self.drifted,
            "added": [sig.to_dict() for sig in self.added],
            "removed": [sig.to_dict() for sig in self.removed],
            "changed": [change.to_dict() for change in self.changed],
            "unchanged": self.unchanged,
            "duplicate_keys": self.duplicate_keys,
            "note": self.note or STATUS_LABELS.get(self.status, ""),
        }


@dataclass(frozen=True, slots=True)
class RescanReport:
    """一批 run 的重扫结论。不可比的行留在 ``rows`` 里并计入 ``uncovered``。"""

    rows: tuple[IssueSetDiff, ...] = ()

    @property
    def checked(self) -> int:
        """查看过的 run 数。"""
        return len(self.rows)

    @property
    def comparable(self) -> int:
        return sum(1 for row in self.rows if row.comparable)

    @property
    def drifted(self) -> int:
        """issue 集被当前检测器代码改动的 run 数；发布门禁的判据。"""
        return sum(1 for row in self.rows if row.drifted)

    @property
    def uncovered(self) -> int:
        """未能比对的 run 数。AC-11b 的分母缺口，必须单独可见。"""
        return self.checked - self.comparable

    @property
    def from_fixture(self) -> int:
        """比对的是留存副本的 run 数。它是"能脱离原始文件复核"的证据，不是执行覆盖率本身。"""
        return sum(1 for row in self.rows if row.source_kind == "fixture")

    def to_dict(self) -> dict[str, object]:
        """机器可读差异表（JSON 可序列化）。字段名即归档契约, 改动需同步测试。"""
        return {
            "checked": self.checked,
            "comparable": self.comparable,
            "uncovered": self.uncovered,
            "from_fixture": self.from_fixture,
            "drifted": self.drifted,
            "rows": [row.to_dict() for row in self.rows],
        }

    def to_markdown(self) -> str:
        """人读差异表（CLI 文本输出）。空批次也返回表头, 证明命令真的执行了。"""
        lines = [
            "| run_id | status | added | removed | changed | unchanged |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for row in self.rows:
            status = row.status if row.comparable else f"{row.status} (uncovered)"
            lines.append(
                f"| {row.run_id} | {status} | {len(row.added)} | {len(row.removed)} "
                f"| {len(row.changed)} | {row.unchanged} |"
            )
        details: list[str] = []
        for row in self.rows:
            entries = [f"+ {sig.render()}" for sig in row.added]
            entries += [f"- {sig.render()}" for sig in row.removed]
            entries += [
                f"~ {change.before.render()} → {', '.join(change.fields)}" for change in row.changed
            ]
            if entries:
                details.append("")
                details.append(f"{row.run_id}:")
                details.extend(entries)
        if self.uncovered:
            details.append("")
            details.append("Uncovered:")
            details.extend(
                f"- {row.run_id}: {row.status} - {row.note or STATUS_LABELS.get(row.status, '')}"
                for row in self.rows
                if not row.comparable
            )
        return "\n".join(lines + details)


__all__ = [
    "STATUS_LABELS",
    "IssueChange",
    "IssueSetDiff",
    "IssueSignature",
    "RescanReport",
    "RescanStatus",
    "SourceKind",
    "diff_issue_sets",
    "signature",
]
