"""Score replay（O-D5-01 第一块砖）：用**当前**评分代码重算已存 issue 集，量化与存档分数的差。

为什么需要它：存档里的 ``quality_score`` 是「产出它的那份代码」的断言。评分公式或维度权重
一改，所有历史分数的含义就静默改变，而来自不同代码状态的两份 run 会被当成同一个数来比较。
``R-08`` 的数字口径纪律要求分数能回指到具体代码状态；本模块提供这个回指手段。

设计约束（三条，均有实测依据，见 ``AUDIT/EVOLUTION_ANALYSIS_2026-11-10.md`` §8.6）：

1. **重放必须用当前默认口径，不能把存档权重回灌成输入。** 存档的 ``weights`` 是引擎输出的
   「归一化生效权重」（只覆盖实际评分维度），而 ``score(weights=...)`` 需要的是「可配置输入
   权重」。把结果当输入回灌会锁死口径，使本工具恰好检不出它要检的东西（探针：把默认权重
   0.20 改成 0.01，新引擎 90.6 vs 存档 88.4，回灌式重放却得到 delta=0.0）。
2. **只重放评分，不重放检测。** store 存的是 issue 快照，不是源数据行；检测器集变更只能
   重扫保留下来的 fixture，不能重放。把「重算分数」说成「重算检测」会使工具本身变成一条
   不可验证的声明。
3. ``run_id`` 由调用方显式传入。``QualityScore`` 是纯分数模型，不含 run 标识；标识在
   ``ScanRun.id`` / ``Issue.scan_run_id`` 上。用 ``getattr`` 猜字段只会得到空串。

**接线范围**：本模块提供纯函数 ``replay_score`` 与批量 ``replay_runs``；CLI 经
``datasentry replay`` 读取 store、输出差异表并归档 JSON。它仍**不是**发布流水线的自动闸门
（CI 定义不在本仓库可改范围）, 也不能重放检测器变更（见约束 2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import ScanRun
from datasentry_core.scoring import quality as _quality

#: 分数落库为 REAL，重算走同一公式，正常情况下应逐位相等；极小容差只挡浮点表示误差，
#: 不用于掩盖真实漂移。
DEFAULT_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class ScoreDrift:
    """一次重放的结论：存档分数 vs 当前代码重算分数。"""

    run_id: str
    recorded_overall: float
    replayed_overall: float | None
    delta: float | None
    changed_dimensions: tuple[str, ...]
    recorded_score_version: str
    current_score_version: str
    note: str = ""

    @property
    def replayable(self) -> bool:
        """是否成功重算；False 时 ``delta`` 恒为 None，不得当作 0 分差。"""
        return self.replayed_overall is not None

    @property
    def version_changed(self) -> bool:
        """口径版本号不同：无需猜测、可直接归因的口径变更信号。

        版本号相同而 delta 非零，说明公式/权重改了却没 bump 版本——这正是影响预览要抓的
        隐性变更；版本号不同而 delta 为零，说明只改了标记。
        """
        return self.recorded_score_version != self.current_score_version

    def exceeds(self, tolerance: float = DEFAULT_TOLERANCE) -> bool:
        """当前代码是否会把这条 run 的分数改到容差之外。"""
        return self.delta is not None and abs(self.delta) > tolerance


def _ran_dimensions(recorded: QualityScore) -> tuple[set[QualityDimension], tuple[str, ...]]:
    """存档中非 None 的维度 = 当时实际跑过检测器的维度；无法识别的键单独返回。"""
    ran: set[QualityDimension] = set()
    unknown: list[str] = []
    for name, value in recorded.dimensions.items():
        if value is None:
            continue
        try:
            ran.add(QualityDimension(name))
        except ValueError:
            unknown.append(name)
    return ran, tuple(sorted(unknown))


def _not_replayable(recorded: QualityScore, run_id: str, reason: str) -> ScoreDrift:
    return ScoreDrift(
        run_id=run_id,
        recorded_overall=recorded.overall,
        replayed_overall=None,
        delta=None,
        changed_dimensions=(),
        recorded_score_version=recorded.score_version,
        current_score_version=_quality.SCORE_VERSION,
        note=reason,
    )


def replay_score(
    issues: list[Issue],
    recorded: QualityScore,
    *,
    run_id: str,
    engine: _quality.QualityScoreEngine | None = None,
    weights: dict[QualityDimension, float] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ScoreDrift:
    """用当前引擎重算 ``issues``，与 ``recorded`` 对比。

    ``run_id`` 必填且由调用方提供（见模块约束 3）。``engine`` 仅用于测试注入；生产路径用
    默认引擎，以保证重放反映的是**当前代码口径**。``ran_dimensions`` 从存档还原——它是
    「当时哪些检测器跑了」这一历史事实，不是口径，必须沿用，否则未运行的维度会被算成 100
    分而制造虚假差异。

    ``weights`` 语义是「该 run 当时的**输入配置权重**（若可从 store 取得）」，默认 None 表示
    用当前默认口径，用于检出代码口径变更。**绝不可把 ``recorded.weights`` 传进来**：那是
    引擎输出的归一化生效权重（约束 1）。历史 run 若确曾用自定义配置且调用方未传 ``weights``，
    delta 会同时含配置差异，属已知假阳性，见 note。
    """
    ran, unknown = _ran_dimensions(recorded)
    if not ran:
        return _not_replayable(
            recorded,
            run_id,
            "存档中没有任何可评分维度, 重放会抛 ValueError; 标为不可重算而非记 0 分差",
        )

    try:
        replayed = (engine or _quality.QualityScoreEngine()).score(
            issues, ran_dimensions=ran, weights=weights
        )
    except ValueError as exc:
        return _not_replayable(recorded, run_id, f"当前口径无法重放该 run: {exc}")

    changed: list[str] = []
    for name, before in recorded.dimensions.items():
        after = replayed.dimensions.get(name)
        if after is None or before is None:
            if after != before:
                changed.append(name)
            continue
        if abs(after - before) > tolerance:
            changed.append(name)

    notes: list[str] = []
    if unknown:
        notes.append(f"存档含当前枚举无法识别的维度: {', '.join(unknown)} (已跳过)")
    if weights is None:
        notes.append("按当前默认权重重放; 若该 run 曾使用自定义配置, delta 含配置差异")
    else:
        notes.append("按调用方提供的配置权重重放; 权重差异已从口径变更中剥离")

    return ScoreDrift(
        run_id=run_id,
        recorded_overall=recorded.overall,
        replayed_overall=replayed.overall,
        delta=replayed.overall - recorded.overall,
        changed_dimensions=tuple(sorted(changed)),
        recorded_score_version=recorded.score_version,
        current_score_version=_quality.SCORE_VERSION,
        note="; ".join(notes),
    )


class ReplaySource(Protocol):
    """``replay_runs`` 需要的最小存储接口。``MetadataStore`` 结构上满足, 无需继承。"""

    def list_scan_runs(self) -> list[ScanRun]: ...

    def get_scan_run(self, scan_run_id: str) -> ScanRun | None: ...

    def get_issues(self, scan_run_id: str) -> list[Issue]: ...


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """一批 run 的重放结论。未评分 run 进 ``skipped``, 不用静默省略。"""

    rows: tuple[ScoreDrift, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    tolerance: float = DEFAULT_TOLERANCE

    @property
    def checked(self) -> int:
        """实际查看过的 run 数（含跳过项）。"""
        return len(self.rows) + len(self.skipped)

    @property
    def replayable(self) -> int:
        return sum(1 for row in self.rows if row.replayable)

    @property
    def drifted(self) -> int:
        """当前代码会把分数改到容差之外的 run 数; 发布门禁的判据。"""
        return sum(1 for row in self.rows if row.exceeds(self.tolerance))

    def to_dict(self) -> dict[str, object]:
        """机器可读差异表（JSON 可序列化）。字段名即归档契约, 改动需同步测试。"""
        return {
            "score_version": _quality.SCORE_VERSION,
            "tolerance": self.tolerance,
            "checked": self.checked,
            "replayable": self.replayable,
            "drifted": self.drifted,
            "skipped": [{"run_id": run_id, "reason": reason} for run_id, reason in self.skipped],
            "rows": [
                {
                    "run_id": row.run_id,
                    "recorded_overall": row.recorded_overall,
                    "replayed_overall": row.replayed_overall,
                    "delta": row.delta,
                    "drifted": row.exceeds(self.tolerance),
                    "replayable": row.replayable,
                    "version_changed": row.version_changed,
                    "recorded_score_version": row.recorded_score_version,
                    "current_score_version": row.current_score_version,
                    "changed_dimensions": list(row.changed_dimensions),
                    "note": row.note,
                }
                for row in self.rows
            ],
        }

    def to_markdown(self) -> str:
        """人读差异表（CLI 文本输出）。空批次也返回表头, 证明命令真的执行了。"""
        lines = [
            "| run_id | recorded | replayed | delta | score_version | changed_dimensions |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
        for row in self.rows:
            replayed = "-" if row.replayed_overall is None else f"{row.replayed_overall:.2f}"
            delta = "-" if row.delta is None else f"{row.delta:+.2f}"
            version = row.recorded_score_version
            if row.version_changed:
                version = f"{version} -> {row.current_score_version}"
            changed = ", ".join(row.changed_dimensions) or "-"
            lines.append(
                f"| {row.run_id} | {row.recorded_overall:.2f} | {replayed} | {delta} "
                f"| {version} | {changed} |"
            )
        if self.skipped:
            lines.append("")
            lines.append("Skipped:")
            lines.extend(f"- {run_id}: {reason}" for run_id, reason in self.skipped)
        return "\n".join(lines)


def replay_runs(
    source: ReplaySource,
    *,
    run_ids: Sequence[str] | None = None,
    limit: int | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ReplayReport:
    """从 store 读一批 run, 逐条 ``replay_score``。

    两种选择方式：给 ``run_ids`` 时按给定顺序重放（缺 id 抛 ``KeyError``, 不静默跳过）;
    否则取 ``list_scan_runs()`` 的前 ``limit`` 条（store 已按 started_at 降序）。
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    if run_ids is not None:
        runs: list[ScanRun] = []
        for run_id in run_ids:
            run = source.get_scan_run(run_id)
            if run is None:
                raise KeyError(f"scan run not found: {run_id}")
            runs.append(run)
    else:
        runs = source.list_scan_runs()
        if limit is not None:
            runs = runs[:limit]

    rows: list[ScoreDrift] = []
    skipped: list[tuple[str, str]] = []
    for run in runs:
        if run.quality_score is None:
            skipped.append((run.id, f"run 未存档质量分 (status={run.status}), 无可比对象"))
            continue
        rows.append(
            replay_score(
                source.get_issues(run.id),
                run.quality_score,
                run_id=run.id,
                tolerance=tolerance,
            )
        )
    return ReplayReport(rows=tuple(rows), skipped=tuple(skipped), tolerance=tolerance)


__all__ = [
    "DEFAULT_TOLERANCE",
    "ReplayReport",
    "ReplaySource",
    "ScoreDrift",
    "replay_runs",
    "replay_score",
]
