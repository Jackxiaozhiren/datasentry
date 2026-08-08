"""电商订单域场景示例（Step 34）：多文件 + 质量门禁 + 修复闭环复扫。

用法：
    uv run python examples/ecommerce/run_showcase.py [--rows N] [--out DIR]

流程（无 LLM，完全离线）：
    1. 生成两个脏数据文件：orders.csv（负价/非法日期/状态变体）
       与 customers.csv（坏邮箱/坏手机号）
    2. 分别 scan_file：36 检测器 + 融合 + 评分 + 落库，打印质量分
    3. 质量门禁（22 章场景 C）：orders 脏数据以 `fail_on=high` 求值
       ——预期 FAIL；修复后复扫再求值（如实打印，不粉饰）
    4. 修复闭环：最高优先可修复 issue 走 propose → preview → apply
       → 复扫 orders → 对比质量分提升
    5. 导出两份 HTML 报告；预算 < 3 分钟（34 章）

数据固定种子可复现（--seed 42 默认）。
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from datasentry import DataSentry
from datasentry_core.models.contract import QualityGate
from datasentry_core.models.enums import Severity
from datasentry_core.reporting.html import render_html
from datasentry_core.scoring.gate import QualityGateEvaluator

STATUSES = ["active", "pending", "closed"]
STATUS_VARIANTS = ["Active", "actve", "DONE", "suspended"]
BAD_DATES = ["2024-02-30", "2024-13-01", "yesterday"]
BAD_EMAILS = ["not-an-email", "user@", "user@example", "user@@x.com"]
BAD_PHONES = ["12345", "abcdefg", "1381234567"]

BUDGET_SECONDS = 180
GATE = QualityGate(fail_on=[Severity.HIGH], maximum_failed_rows_ratio=0.01)


def generate_orders(path: Path, rows: int, seed: int) -> None:
    rng = random.Random(seed)
    today = date(2026, 8, 1)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("order_id,customer_id,price,status,order_date\n")
        for i in range(rows):
            price = rng.randint(-200, 500)  # 负价：约 1/3 行
            status = rng.choice(STATUSES)
            if rng.random() < 0.06:
                status = rng.choice(STATUS_VARIANTS)
            if rng.random() < 0.05:
                order_date = rng.choice(BAD_DATES)
            else:
                order_date = (today - timedelta(days=rng.randint(0, 700))).isoformat()
            customer_id = rng.randrange(1, int(rows * 0.6) + 1)  # 重复客户
            fh.write(f"{i + 1},{customer_id},{price},{status},{order_date}\n")


def generate_customers(path: Path, rows: int, seed: int) -> None:
    rng = random.Random(seed + 1)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("customer_id,name,email,phone\n")
        for i in range(1, rows + 1):
            email = f"user{i}@example.com"
            if rng.random() < 0.08:
                email = rng.choice(BAD_EMAILS)
            phone = f"138{rng.randrange(10000000, 99999999)}"
            if rng.random() < 0.06:
                phone = rng.choice(BAD_PHONES)
            fh.write(f"{i},customer_{i},{email},{phone}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=2000, help="每文件行数")
    parser.add_argument("--seed", type=int, default=42, help="random seed (reproducible)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out_dir = (
        Path(args.out).expanduser()
        if args.out
        else Path(tempfile.mkdtemp(prefix="datasentry-ecommerce-"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = out_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    orders_csv = out_dir / "orders.csv"
    customers_csv = out_dir / "customers.csv"

    start = time.monotonic()
    steps: list[tuple[str, float]] = []

    def scan_report(client: DataSentry, path: Path, label: str) -> tuple[list, float, str]:
        t0 = time.monotonic()
        scan, _, issues = client.scan_file(path)
        steps.append((f"scan {label}", time.monotonic() - t0))
        score = scan.quality_score.overall if scan.quality_score else 0.0
        print(
            f"  {label}: rows={scan.fingerprint.row_count} issues={len(issues)} quality={score:.1f}"
        )
        return issues, score, scan.id

    def gate_report(issues: list, label: str) -> bool:
        result = QualityGateEvaluator().evaluate(issues, GATE)
        print(
            f"  gate({label}) --fail-on high: passed={result.passed}"
            + (f" ({result.reasons[0]})" if result.reasons else "")
        )
        return result.passed

    try:
        t0 = time.monotonic()
        generate_orders(orders_csv, args.rows, args.seed)
        generate_customers(customers_csv, args.rows, args.seed)
        steps.append(("generate data", time.monotonic() - t0))

        client = DataSentry(project=workspace)
        try:
            cur = orders_csv
            orders_issues, orders_score, orders_run = scan_report(client, cur, "orders")
            _, _, customers_run = scan_report(client, customers_csv, "customers")
            gate_before = gate_report(orders_issues, "orders")

            # 修复闭环：扫描 → 修复（副本叠加）→ 复扫 → 再修，直至无可修复（上限 3 轮）
            t0 = time.monotonic()
            total_repaired = 0
            for round_no in range(1, 4):
                repairable: list = []
                for issue in sorted(orders_issues, key=lambda i: i.priority_score, reverse=True):
                    if client.repair_propose(issue.id, cur) is not None:
                        repairable.append(issue)
                if not repairable:
                    break
                last_run = None
                for issue in repairable:
                    proposal = client.repair_propose(issue.id, cur)
                    client.repair_preview(issue.id, cur)
                    run = client.repair_apply(issue.id, cur)
                    total_repaired += 1
                    last_run = run
                    print(
                        f"  repair round {round_no}: {issue.title} → {proposal.operation.value} "
                        f"(run={run.id}, status={run.status.value})"
                    )
                # 同轮修复互不叠加；轮末最后一个副本作为下一轮输入
                if last_run is not None:
                    fixed = workspace / ".datasentry" / "repairs" / f"{last_run.id}.csv"
                    if fixed.exists():
                        cur = fixed
                orders_issues, orders_score_now, _ = scan_report(
                    client, cur, f"orders-round-{round_no + 1}"
                )
                orders_score = orders_score_now
            steps.append(("repair cycles", time.monotonic() - t0))
            print(
                f"  repaired {total_repaired} issues"
                if total_repaired
                else "  no repairable issue found"
            )

            after_issues, orders_score_after, after_run = scan_report(client, cur, "orders-final")
            score_delta = orders_score_after - orders_score
            # set_null 修复把非法值转 NULL：消除错误但引入缺失，分数可能不升反降
            print(
                f"  quality: {orders_score:.1f} → {orders_score_after:.1f} "
                f"({'+' if score_delta >= 0 else ''}{score_delta:.1f}) — "
                "set_null: missingness replaces errors; the gate is the final arbiter"
            )
            gate_after = gate_report(after_issues, "orders-after-repair")

            # 报告导出
            t0 = time.monotonic()
            for _, run_id, out_name in (
                ("orders", orders_run, "report-orders.html"),
                ("customers", customers_run, "report-customers.html"),
                ("orders-after-repair", after_run, "report-orders-after-repair.html"),
            ):
                report = client.export_report(run_id)
                (out_dir / out_name).write_text(render_html(report), encoding="utf-8")
                print(f"  report {out_name}: {len(report)} report entries")
            steps.append(("reports", time.monotonic() - t0))

            total = time.monotonic() - start
            print(
                f"\nartifacts: {orders_csv.name} / {customers_csv.name} / "
                f"report-orders.html / report-customers.html / report-orders-after-repair.html · "
                f"workspace: {workspace}"
            )
            print("step timings: " + ", ".join(f"{n}={s:.2f}s" for n, s in steps))
            ok = (
                total < BUDGET_SECONDS
                and orders_issues
                and after_issues
                and gate_before is False  # 门禁拦截了脏数据
                and gate_after is True  # 修复闭环后门禁放行
                and total_repaired > 0
            )
            print(f"total: {total:.1f}s (budget {BUDGET_SECONDS}s) → " + ("PASS" if ok else "FAIL"))
            return 0 if ok else 1
        finally:
            client.close()
    except Exception as exc:  # 示例脚本：失败也要清晰退出
        print(f"showcase failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
