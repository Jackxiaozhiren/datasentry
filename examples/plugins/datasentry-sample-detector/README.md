# datasentry-sample-detector（示例插件包）

DataSentry 插件生态（Step 50，V2-C / ADR-050）的可安装示例：
一个通过 entry point 自动发现的检测器插件，无需任何配置即可被
`datasentry scan` 自动启用。

## 做什么

`NegativeValueDetector`（detector_id `plugin_negative_value`）：
在数值列中检出「负值」——本示例针对金额/价格类业务字段（如订单金额
不应为负），质量维度 `validity`。用于演示插件协议的最小区面：

- 实现 `Detector` Protocol（`supports` / `detect` / `metadata`）
- 声明类级 `detector_id` / `detector_version` / `quality_dimension`
- 在 `pyproject.toml` 注册 entry point：

```toml
[project.entry-points."datasentry.detectors"]
negative_value = "datasentry_sample_detector:NegativeValueDetector"
```

## 安装

```bash
uv pip install -e examples/plugins/datasentry-sample-detector
# 等价于：pip install -e examples/plugins/datasentry-sample-detector
```

## 验证

```bash
datasentry plugin list --format json
# → plugins 中出现 ("plugin_negative_value", "entrypoint")，errors 为空

datasentry scan orders.csv --format json
# → detector_runs 比内置多 1（39 内置 + 1 插件），负值被自动捕获
```

无需注册表、无需配置文件——安装即被发现（`importlib.metadata`
entry points 扫描，失败优雅降级，单个插件缺陷不影响整体扫描）。

## 开发新插件

1. 复制本目录结构（`pyproject.toml` + 单个模块即可）
2. 实现 Detector Protocol（参考 `datasentry_core.detectors.base.Detector`
   与 `DetectorMeta` 模型）
3. 在 `pyproject.toml` 声明 entry point 并 `pip install -e` 本地包
4. `datasentry plugin list` 确认加载，`scan` 确认自动启用

## 测试

```bash
uv run pytest tests/test_sample_plugin.py -q
```

验证包的形态契约：entry point 声明、协议实现、元数据维度正确。
