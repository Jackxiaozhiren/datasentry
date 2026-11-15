"""`D7-06`: the benchmark's acceptance-table label must state the workload it actually ran.

The `Performance budgets` section of `docs/BENCHMARKS.md` names `benchmarks/bench_scan.py` as
the single source of truth for the budget table, so a hardcoded detector count in that table
*is* the definition of the acceptance tier. It said "36 检测器" while the registry enables 39
and the bench dataset actually executes 32 (the rest are skipped by `supports()`), so every
reproduction record was off -- and off by a number that was not even the registry's.

`benchmarks` is not an importable package (it is a script directory), so the seam is loaded from
its file path; the loaded module's own `__file__` is asserted to be the same file the source-text
judgements read.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from collections.abc import Callable

BENCH = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "bench_scan.py"


def _scan_label_seam() -> Callable[[int, int], str]:
    """Load `bench_scan.scan_label` by path, proving the object under test is that very file."""
    spec = importlib.util.spec_from_file_location("bench_scan_under_test", BENCH)
    assert spec is not None, f"cannot load {BENCH}"
    assert spec.loader is not None, f"cannot load {BENCH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert pathlib.Path(str(module.__file__)).resolve() == BENCH, (
        f"loaded {module.__file__}, not {BENCH}"
    )
    return module.scan_label


def test_scan_label_interpolates_both_denominators() -> None:
    scan_label = _scan_label_seam()

    assert scan_label(32, 39) == "全量扫描(32/39 检测器+融合+评分)"
    assert scan_label(12, 12) == "全量扫描(12/12 检测器+融合+评分)"
    assert "36" not in scan_label(32, 39)


def test_acceptance_table_label_is_not_hardcoded() -> None:
    """The printed row takes its numbers from the run; no literal count survives in the label."""
    source = BENCH.read_text()
    assert re.search(r"judge\(\s*scan_label\(", source), "the acceptance row must build its label"
    # A label literal that opens with digits is the defect; prose about the old number is allowed.
    assert re.search(r'"全量扫描\(\d', source) is None, "a hardcoded detector count came back"
    assert "len(per_detector)" in source, "the executed count must come from the timing loop"
    assert "len(active_detectors)" in source, "the registry count must come from the registry"
    assert re.search(
        r"scan_label\(\s*len\(per_detector\)\s*,\s*len\(active_detectors\)\s*\)",
        source,
    ), "the two counts must reach the label in (executed, registry) order"


def test_the_label_takes_the_counts_the_product_itself_reports(tmp_path: pathlib.Path) -> None:
    """Feeding the seam with the live registry produces numbers the app reports, not 36."""
    from datasentry.client import DataSentry

    scan_label = _scan_label_seam()
    client = DataSentry(project=tmp_path)
    try:
        registered = client.list_detectors()
    finally:
        client.close()
    assert registered, "an empty registry would make this judgement a tautology"
    label = scan_label(len(registered), len(registered))
    assert f"({len(registered)}/{len(registered)} 检测器" in label, label
    assert label != scan_label(36, 36), "a label that cannot differ from 36 is derived from nothing"
