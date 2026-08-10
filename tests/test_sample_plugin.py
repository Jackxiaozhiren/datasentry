"""Step 50 示例插件包测试（V2-C / ADR-050：entry points 插件形态可安装、符合协议）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from datasentry_core.detectors.base import Detector

_ROOT = Path(__file__).resolve().parents[1]
_PKG_DIR = _ROOT / "examples" / "plugins" / "datasentry-sample-detector"
_SRC = _PKG_DIR / "src" / "datasentry_sample_detector" / "__init__.py"


def _sample_module() -> object:
    spec = importlib.util.spec_from_file_location("datasentry_sample_detector", _SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSamplePluginPackage:
    def test_pyproject_declares_entry_point(self) -> None:
        text = (_PKG_DIR / "pyproject.toml").read_text(encoding="utf-8")
        assert '[project.entry-points."datasentry.detectors"]' in text
        assert 'negative_value = "datasentry_sample_detector:NegativeValueDetector"' in text
        assert 'dependencies = ["datasentry-core>=0.1.0"]' in text

    def test_module_implements_detector_protocol(self) -> None:
        module = _sample_module()
        cls = module.NegativeValueDetector  # type: ignore[attr-defined]
        assert cls.detector_id == "plugin_negative_value"  # type: ignore[attr-defined]
        detector = cls()
        assert isinstance(detector, Detector)
        meta = detector.metadata()
        assert meta.quality_dimension.value == "validity"
        assert meta.detector_id == "plugin_negative_value"
