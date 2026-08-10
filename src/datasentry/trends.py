"""跨扫描趋势数据层（Step 45，18.2 V1：质量分随历史扫描可视化）。

纯函数设计：`build_trends` 只消费 ScanRun 列表（quality_score 随
ScanRun 落库，历史保留原权重），与 Web UI / 未来 CLI 解耦。
趋势信号 = 质量总分序列 + issue 总量序列（漂移引擎的完整信号仍走
drift compare/latest；这里提供轻量概览面）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from datasentry_core.models.scan import ScanRun

_DELTA_UP = 0.5
_DELTA_DOWN = -0.5


@dataclass(frozen=True)
class ScanPoint:
    """单个历史扫描的质量点（趋势图的一个点）。"""

    run_id: str
    score: float
    issues_total: int
    finished_at: datetime


@dataclass(frozen=True)
class DatasetTrend:
    """一个数据集的跨扫描趋势。"""

    dataset_id: str
    points: list[ScanPoint]

    @property
    def delta(self) -> float:
        """最新 - 最老的质量分变化（不足两点视为 0）。"""
        if len(self.points) < 2:
            return 0.0
        return self.points[-1].score - self.points[0].score

    @property
    def direction(self) -> Literal["up", "down", "flat"]:
        if self.delta >= _DELTA_UP:
            return "up"
        if self.delta <= _DELTA_DOWN:
            return "down"
        return "flat"

    @property
    def latest_score(self) -> float | None:
        if not self.points:
            return None
        return self.points[-1].score

    @property
    def latest_issues(self) -> int:
        if not self.points:
            return 0
        return self.points[-1].issues_total

    def to_report_dict(self) -> dict[str, Any]:
        """JSON 可序列化视图（Step 49：HTML 报告迷你趋势图消费此结构）。"""
        return {
            "dataset_id": self.dataset_id,
            "points": [
                {
                    "run_id": p.run_id,
                    "score": p.score,
                    "issues_total": p.issues_total,
                    "finished_at": p.finished_at.isoformat(),
                }
                for p in self.points
            ],
        }


def build_trends(scans: list[ScanRun]) -> list[DatasetTrend]:
    """ScanRun 列表 → 每数据集的趋势（仅 completed + 有质量分的扫描）。

    输出按数据集最近扫描时间倒序（最新活动的数据集在前）。
    """
    by_dataset: dict[str, list[ScanPoint]] = {}
    for scan in scans:
        if scan.status != "completed" or scan.quality_score is None:
            continue
        finished = scan.finished_at or scan.started_at
        by_dataset.setdefault(scan.dataset_id, []).append(
            ScanPoint(
                run_id=scan.id,
                score=scan.quality_score.overall,
                issues_total=sum(int(v) for v in scan.issues_count.values()),
                finished_at=finished,
            )
        )
    trends = []
    for dataset_id, points in by_dataset.items():
        ordered = sorted(points, key=lambda p: p.finished_at)
        trends.append(DatasetTrend(dataset_id=dataset_id, points=ordered))
    trends.sort(key=lambda t: t.points[-1].finished_at, reverse=True)
    return trends
