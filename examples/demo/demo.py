"""M9 Demo：一键走通 DataSentry 全流程（34 章 Demo < 3 分钟）。

用法：
    uv run python examples/demo/demo.py [--rows N] [--out DIR]

流程（无 LLM，完全离线）：
    1. 生成脏数据 CSV（含前后空白/大小写混用/缺失占位/离群价/非法日期）
    2. scan_file：36 检测器全量扫描 + 融合 + 评分 + 落库
    3. 导出 26 章 JSON 报告 + HTML 报告
    4. 修复闭环：propose → preview → apply → rollback（15 章，ADR-020）
    5. 打印各阶段耗时，验证 < 3 分钟预算（34 章）

演示数据默认 5000 行（M9 预算远低于 1e6 行基准），随机种子固定可复现。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from datasentry import DataSentry
from datasentry_core.reporting.html import render_html

CATEGORIES = [f"cat_{i:02d}" for i in range(50)]
STATUSES = ["active", "pending", "inactive"]
MISSING_TOKENS = ["n/a", "N/A", "-", "unknown", "null"]

BUDGET_SECONDS = 180  # 34 章：Demo 数据集完整走通 < 3 分钟


def generate_csv(path: Path, rows: int, seed: int = 42) -> None:
    """生成带多种脏数据的演示 CSV（与 Step 20 基准数据同构）。"""
    rng = random.Random(seed)
    today = date(2026, 8, 1)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("id,price,category,event_date,name,status\n")
        for i in range(rows):
            price = (
                rng.choice([10, 25, 50, 100, 250, 500, 9999])
                if rng.random() < 0.03
                else rng.randint(10, 500)
            )
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            if rng.random() < 0.04:
                category = "  " + category + " "
            if rng.random() < 0.05:
                event_date = rng.choice(["2024-02-30", "2024-13-01", "not-a-date"])
            else:
                event_date = (today - timedelta(days=rng.randint(0, 700))).isoformat()
            name = f"user_{i}"
            if rng.random() < 0.04:
                name = " " + name + " "
            if rng.random() < 0.06:
                name = name.upper()
            status = rng.choice(STATUSES)
            if rng.random() < 0.05:
                status = rng.choice(MISSING_TOKENS)
            fh.write(f"{i},{price},{category},{event_date},{name},{status}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5000, help="demo 数据行数")
    parser.add_argument("--out", type=Path, default=None, help="输出目录（默认临时目录）")
    args = parser.parse_args(argv)

    out_dir = (
        Path(args.out).expanduser()
        if args.out
        else Path(tempfile.mkdtemp(prefix="datasentry-demo-"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = out_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    data_csv = out_dir / "customers.csv"
    start = time.monotonic()
    steps: list[tuple[str, float]] = []

    try:
        # 1. 生成数据
        t0 = time.monotonic()
        generate_csv(data_csv, args.rows)
        steps.append(("generate data", time.monotonic() - t0))

        # 2. 扫描 + 评分 + 落库
        client = DataSentry(project=workspace)
        try:
            t0 = time.monotonic()
            scan, runs, issues = client.scan_file(data_csv)
            steps.append(("scan+score", time.monotonic() - t0))
            elapsed = time.monotonic() - start
            quality = f"{scan.quality_score.overall:.1f}" if scan.quality_score else "—"
            print(
                f"scan run: {scan.id} · rows={scan.fingerprint.row_count} "
                f"cols={scan.fingerprint.column_count} · issues={len(issues)} "
                f"quality={quality} · elapsed={elapsed:.1f}s"
            )
            completed = sum(1 for r in runs if r.status == "completed")
            print(f"  detectors completed: {completed}/{len(runs)}")
            for issue in sorted(issues, key=lambda i: i.priority_score, reverse=True)[:5]:
                print(
                    f"  top: [{issue.severity.value}] {issue.title} "
                    f"(priority={issue.priority_score:.1f})"
                )

            # 3. 报告
            t0 = time.monotonic()
            report = client.export_report(scan.id)
            json_path = out_dir / "report.json"
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            html_path = out_dir / "report.html"
            html_path.write_text(render_html(report), encoding="utf-8")
            steps.append(("reports", time.monotonic() - t0))

            # 4. 修复闭环（找第一个可修复的 Issue）
            t0 = time.monotonic()
            repaired: str | None = None
            for issue in sorted(issues, key=lambda i: i.priority_score, reverse=True):
                proposal = client.repair_propose(issue.id, data_csv)
                if proposal is None:
                    continue
                preview = client.repair_preview(issue.id, data_csv)
                run = client.repair_apply(issue.id, data_csv)
                print(
                    f"  repair: {issue.title} → {proposal.operation.value} "
                    f"(run={run.id}, status={run.status.value})"
                )
                if preview is not None:
                    before = preview[1].rule_failures_before
                    after = preview[1].rule_failures_after
                    print(f"    rule_failures {before} → {after}")
                rolled = client.repair_rollback(run.id)
                print(f"    rollback → {rolled.status.value}")
                repaired = run.id
                break
            steps.append(("repair cycle", time.monotonic() - t0))
            if repaired is None:
                print("  warning: no repairable issue found in demo data")

            total = time.monotonic() - start
            print(
                f"\nartifacts: {data_csv.name} / report.json / report.html · workspace: {workspace}"
            )
            print("step timings: " + ", ".join(f"{n}={s:.2f}s" for n, s in steps))
            passed = total < BUDGET_SECONDS
            print(
                f"total: {total:.1f}s (budget {BUDGET_SECONDS}s) → "
                + ("PASS" if passed else "FAIL")
            )
            return 0 if passed else 1
        finally:
            client.close()
    except Exception as exc:  # demo 脚本：失败也要清晰退出
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
