"""``scripts/detector_baseline.py`` 的门禁语义。

判据不是"脚本能跑"，而是四件事各自成立：写读一致、severity/计数变化能判红、
数据集被换掉时拒绝比对（而不是把差异算进检测器）、两次写出的字节完全相同。
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "demo-data" / "orders.csv"


@pytest.fixture(scope="module")
def module() -> ModuleType:
    path = ROOT / "scripts" / "detector_baseline.py"
    spec = importlib.util.spec_from_file_location("detector_baseline", path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory, module: ModuleType) -> Path:
    target = tmp_path_factory.mktemp("baseline") / "orders.json"
    assert module.main(["write", "--data", str(DATA), "--baseline", str(target)]) == 0
    return target


def _tampered(source: Path, destination: Path, mutate: Callable[[dict[str, object]], None]) -> Path:
    raw = json.loads(source.read_text(encoding="utf-8"))
    mutate(raw)
    destination.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def test_write_then_check_is_clean(
    module: ModuleType, baseline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert module.main(["check", "--data", str(DATA), "--baseline", str(baseline)]) == 0
    assert "ok: 11 issue signature(s)" in capsys.readouterr().out


def test_write_is_byte_identical(module: ModuleType, baseline: Path, tmp_path: Path) -> None:
    second = tmp_path / "again.json"
    assert module.main(["write", "--data", str(DATA), "--baseline", str(second)]) == 0
    assert second.read_bytes() == baseline.read_bytes()


def test_severity_change_is_reported_as_drift(
    module: ModuleType, baseline: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def mutate(raw: dict[str, object]) -> None:
        issues = raw["issues"]
        assert isinstance(issues, list)
        issues[0]["severity"] = "critical"

    tampered = _tampered(baseline, tmp_path / "severity.json", mutate)
    assert module.main(["check", "--data", str(DATA), "--baseline", str(tampered)]) == 1
    out = capsys.readouterr().out
    assert "~ changed:" in out and "severity critical->" in out


def test_removed_issue_is_reported(
    module: ModuleType, baseline: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def mutate(raw: dict[str, object]) -> None:
        issues = raw["issues"]
        assert isinstance(issues, list)
        issues.append(
            {
                "issue_type": "synthetic_probe",
                "table_name": None,
                "columns": ["ghost"],
                "detectors": ["synthetic_probe"],
                "severity": "low",
                "affected_count": 1,
            }
        )

    tampered = _tampered(baseline, tmp_path / "extra.json", mutate)
    assert module.main(["check", "--data", str(DATA), "--baseline", str(tampered)]) == 1
    assert "- removed: synthetic_probe" in capsys.readouterr().out


def test_changed_affected_count_is_reported(
    module: ModuleType, baseline: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def mutate(raw: dict[str, object]) -> None:
        issues = raw["issues"]
        assert isinstance(issues, list)
        issues[1]["affected_count"] = 999

    tampered = _tampered(baseline, tmp_path / "count.json", mutate)
    assert module.main(["check", "--data", str(DATA), "--baseline", str(tampered)]) == 1
    assert "affected 999->" in capsys.readouterr().out


def test_dataset_change_is_a_config_error_not_drift(
    module: ModuleType, baseline: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "orders.csv"
    other.write_text(DATA.read_text(encoding="utf-8") + "1200,frank,10.00,2026-01-01\n")
    assert module.main(["check", "--data", str(other), "--baseline", str(baseline)]) == 2
    assert "dataset changed since the baseline was written" in capsys.readouterr().err


def test_malformed_baseline_is_a_config_error(
    module: ModuleType, baseline: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def mutate(raw: dict[str, object]) -> None:
        issues = raw["issues"]
        assert isinstance(issues, list)
        issues[0]["affected_count"] = "nine"

    tampered = _tampered(baseline, tmp_path / "bad-type.json", mutate)
    assert module.main(["check", "--data", str(DATA), "--baseline", str(tampered)]) == 2
    assert "baseline is malformed" in capsys.readouterr().err
