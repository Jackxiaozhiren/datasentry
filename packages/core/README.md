# datasentry-core

DataSentry AI 核心领域模型与引擎：连接器、检测器、融合、评分、
修复、规则引擎与隐私脱敏。纯离线、零网络依赖，可独立测试。

```
pip install datasentry-core
```

## 模块

- `connectors/` — 多格式数据源（CSV / Parquet / JSONL / XLSX）
- `detectors/` — 检测器注册表与 36+ 内置检测器（`Detector` 协议）
- `engine/` — 画像、融合、评分
- `repair/` — 修复引擎（propose → preview → apply → rollback）
- `rules/` — 规则模型与预运行引擎（期望语义取反）
- `privacy/` — 确定性 PII 脱敏（mask_profile / mask_rows）
- `llm/` — LLM Provider 协议与数据结构（零网络）
- `plugins.py` — 检测器插件加载（插件 API v1，ADR-031）

## 使用

```python
from datasentry_core.connectors import DataSourceSpec, default_registry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors import DetectorRegistry
from datasentry_core.detectors.runner import ScanRunner

registry = DetectorRegistry()
register_default_detectors(registry)
runner = ScanRunner(registry)

handle = default_registry().open(DataSourceSpec(source_type="csv", path="data.csv"))
runs = runner.run_all(handle, config)
```

## 许可

Apache-2.0。完整产品（CLI / API / UI）见 `datasentry` 包。
