"""PRD R-21：示例不许腐烂——每个可运行示例都必须被登记、跑通、并在自己的预算内。

三层判据：

1. **注册表完整性**：`examples/` 下每个带 `if __name__ == "__main__"` 的脚本都必须在
   `_EXAMPLES` 里。新增示例却忘了登记 ⇒ 红，这是真正防腐烂的那一层。
2. **跑通**：每个示例以 scratch 目录为输出跑一次，rc 必须为 0。
3. **预算**：实际墙钟必须低于该示例自己的预算；示例若自报预算行（`run_showcase.py` 的
   `budget 180s`），该数字必须与登记值一致，避免登记值与示例内声明各说各话。

**刻意不钉 issue 数以外的输出**：`examples/expected_counts.json` 是唯一的声明层，只钉每个示例
第一次扫描的 issue 数（`--rows` 固定、seed 默认 42，实测两次运行逐字一致）。质量分、`32/39
detectors completed` 这类计数不钉——它们会随检测器族变动而合理漂移，钉住只会制造噪声。

预算的用途是**抓挂死**，不是抓性能回归：本机同一份代码 `bench` 两次读数 12.7s / 18.1s
（约 40% 波动），把预算收紧到实测值附近只会变成 flaky。性能天花板归 `benchmarks/`。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DECLARED = REPO / "examples" / "expected_counts.json"

# 各示例打印计数的形态（示例输出是面向人的，格式稳定但不统一，故按脚本收）
_ISSUE_PATTERNS = {
    "examples/demo/demo.py": re.compile(r"Scan complete:\s*(\d+)\s+issues"),
    "examples/ecommerce/run_showcase.py": re.compile(r"rows=\d+\s+issues=(\d+)"),
}


@dataclass(frozen=True)
class Example:
    argv: tuple[str, ...]
    budget_seconds: int
    # 示例自己打印的预算声明（有则必须与 budget_seconds 一致）
    self_budget_marker: str | None = None


# 实测基线（2026-11-07，本机）：demo.py --rows 500 ≈ 4s；run_showcase.py --rows 300 ≈ 7s。
# 预算留足余量，理由见模块 docstring。
_EXAMPLES: dict[str, Example] = {
    "examples/demo/demo.py": Example(argv=("--rows", "500"), budget_seconds=120),
    "examples/ecommerce/run_showcase.py": Example(
        argv=("--rows", "300"),
        budget_seconds=180,
        self_budget_marker="budget 180s",
    ),
}


def _runnable_examples() -> set[str]:
    return {
        path.relative_to(REPO).as_posix()
        for path in (REPO / "examples").rglob("*.py")
        if "__main__" in path.read_text(encoding="utf-8")
    }


def _declared() -> dict[str, dict[str, int]]:
    raw = json.loads(DECLARED.read_text(encoding="utf-8"))
    return {
        script: {k: int(v) for k, v in entry.items()}
        for script, entry in raw.items()
        if not script.startswith("_")
    }


def _issue_count(script: str, output: str) -> int | None:
    match = _ISSUE_PATTERNS[script].search(output)
    return int(match.group(1)) if match else None


def _run(spec: Example, script: str, out_dir: Path) -> tuple[int, str, float]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "core" / "src"), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    cmd = [sys.executable, str(REPO / script), *spec.argv, "--out", str(out_dir)]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=spec.budget_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            f"{script} 超过自己的预算 {spec.budget_seconds}s 仍未结束(按 R-21 判红)"
        ) from None
    return proc.returncode, proc.stdout + proc.stderr, time.monotonic() - started


class TestExamplesRunnable:
    def test_registry_covers_every_runnable_example(self) -> None:
        missing = _runnable_examples() - set(_EXAMPLES)
        assert missing == set(), (
            f"这些示例带 __main__ 却未登记到 _EXAMPLES: {sorted(missing)} —— 未登记就等于没有门禁"
        )
        assert set(_EXAMPLES) <= _runnable_examples(), (
            f"登记了不存在的示例: {sorted(set(_EXAMPLES) - _runnable_examples())}"
        )

    def test_registry_gate_detects_an_unregistered_example(self) -> None:
        """反证：模拟「新增示例忘登记」，覆盖检查必须能报出来。"""
        runnable = _runnable_examples() | {"examples/new/not_registered.py"}
        assert runnable - set(_EXAMPLES) == {"examples/new/not_registered.py"}

    def test_budget_gate_actually_fires(self, tmp_path: Path) -> None:
        """反证：把预算压到 0，超时必须真的判红，而不是静默通过。"""
        spec = Example(argv=("--rows", "10"), budget_seconds=0)
        with pytest.raises(AssertionError, match="超过自己的预算"):
            _run(spec, "examples/demo/demo.py", tmp_path / "budget")

    def test_registry_has_a_live_denominator(self) -> None:
        """分母守卫：一个都没登记时上面的覆盖检查会恒真。"""
        assert len(_EXAMPLES) >= 2, f"登记的示例过少({len(_EXAMPLES)}),门禁在空转"

    def test_declaration_covers_every_registered_example(self) -> None:
        assert set(_declared()) == set(_EXAMPLES), (
            "声明层与注册表不一致: "
            f"缺声明={sorted(set(_EXAMPLES) - set(_declared()))}, "
            f"多声明={sorted(set(_declared()) - set(_EXAMPLES))}"
        )

    def test_declared_rows_match_the_argv(self) -> None:
        for script, entry in _declared().items():
            argv = _EXAMPLES[script].argv
            rows = int(argv[argv.index("--rows") + 1])
            assert entry["rows"] == rows, (
                f"{script} 声明的 rows={entry['rows']} 与 argv 的 {rows} 不符"
            )

    def test_each_example_runs_within_budget_and_matches_declared_counts(
        self, tmp_path: Path
    ) -> None:
        declared = _declared()
        for script, spec in sorted(_EXAMPLES.items()):
            out_dir = tmp_path / script.replace("/", "_").removesuffix(".py")
            code, output, elapsed = _run(spec, script, out_dir)
            tail = "\n".join(output.strip().splitlines()[-12:])
            assert code == 0, f"{script} rc={code}\n{tail}"
            assert elapsed < spec.budget_seconds, (
                f"{script} 用了 {elapsed:.1f}s,超过预算 {spec.budget_seconds}s"
            )
            if spec.self_budget_marker is not None:
                assert spec.self_budget_marker in output, (
                    f"{script} 自报的预算与登记值 {spec.budget_seconds}s 不一致"
                    f"(找不到 {spec.self_budget_marker!r})\n{tail}"
                )
                assert "→ PASS" in output, f"{script} 自报预算未 PASS\n{tail}"
            actual = _issue_count(script, output)
            assert actual is not None, (
                f"{script} 的输出里找不到 issue 计数(解析规则落后于示例输出格式)\n{tail}"
            )
            expected = declared[script]["issues"]
            assert actual == expected, (
                f"{script} 的 issue 数漂了: 声明 {expected}, 实跑 {actual}。\n"
                f"若变更是有意的(检测器族改动等),重跑示例并更新 "
                f"{DECLARED.relative_to(REPO)}。\n{tail}"
            )

    def test_declared_counts_are_load_bearing(self, tmp_path: Path) -> None:
        """反证：换个行数，issue 数必须变——否则这个声明根本没在被比。"""
        small = Example(argv=("--rows", "120"), budget_seconds=120)
        code, output, _ = _run(small, "examples/demo/demo.py", tmp_path / "small")
        assert code == 0
        actual = _issue_count("examples/demo/demo.py", output)
        assert actual is not None
        assert actual != _declared()["examples/demo/demo.py"]["issues"], (
            "换行数后 issue 数没变,说明声明与实跑没有真正联动"
        )
