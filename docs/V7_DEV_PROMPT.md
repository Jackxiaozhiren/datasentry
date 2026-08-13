# V7 开发计划书：趋势/画像数据面补全（Step 65~67）

- 目标版本：未定（阶段收尾按仓库既有惯例统一升版 v0.9.0）
- 前置基线：V6（Step 60~64，ADR-060~064）已发布 v0.8.0（tag `v0.8.0`，
  PyPI + Pages + CI 全绿），候选清空
- 文档约定：ADR-065 起追加到 `docs/00-设计裁决记录-ADR.md` 末尾；
  `CHANGELOG.md` 顶部维护 `## [Unreleased]`；架构/坑位进
  `docs/DEVELOPMENT.md`
- 通信语言：中文（代码/提交信息英文，注释极简——仓库风格零注释或极简）

## 一、目标与边界

### 背景（勘察结论）

V5/V6 已建成的数据资产只有 HTML/UI 消费面：
- `trends.build_trends()`（Step 45）→ 仅 HTML 报告迷你 SVG + `/ui/trends` 页
- `trends.build_comparison()`（Step 64）→ 仅 HTML 报告 Run Comparison 节
- 画像 sidecar `profiles/<run_id>.json`（Step 61）→ 仅 HTML 报告画像节；
  `client.load_profile` 存在但 CLI/REST 均无暴露

缺口：CLI 无 `trend` 子命令、REST 无 `/trends` JSON 端点、画像无
REST/CLI 数据面、UI 趋势页无折线可视化（只有条状图）。

### 目标

1. **Step 65（ADR-065）CLI `trend list`**：`datasentry trend list
   [--dataset-id DS]` → envelope 数据面（trends = `DatasetTrend.
   to_report_dict()` 列表），空数据退出 0 合法空列表；`--dataset-id`
   过滤单数据集。
2. **Step 66（ADR-066）REST 数据端点**：`GET /trends`（全数据集趋势
   JSON）+ `GET /scans/{run_id}/profiles`（画像 sidecar 原样 JSON，
   缺失 404）。
3. **Step 67（ADR-067）UI 趋势页可视化增强**：每数据集行内 Sparkline
   折线 SVG（内联零依赖，复刻报告 `.trend-line` 类风格）+ run 行 Δ
   badge（对前一 run，首行 —）。

### 边界

- **零引擎改动**：检测器/评分/报告 26 章契约/画像生成逻辑均不动；
  全部消费现有 `build_trends`/`build_comparison`/`load_profile`
- **零依赖**：UI 增强只用内联 SVG + 现有内嵌 CSS（无图表库/无前端
  框架，延续 Step 45/49 风格）
- 不改 MCP（MCP 工具补全不在本阶段，见候选）

### 落地状态

- Step 65（ADR-065）：CLI trend list 已完成
- Step 66（ADR-066）：REST /trends + /profiles 已完成
- Step 67（ADR-067）：UI 趋势页 Sparkline + Δ 已完成（阶段收尾统一
  升版 v0.9.0：pyproject + 双 `__init__.__version__` + CHANGELOG 收口
  + tag + GitHub release，参照 v0.8.0=1dcd8fd 惯例）

### 约束（既有约定）

- 门禁：`uv run ruff check .` → `uv run ruff format --check .` →
  `uv run mypy packages/core/src/datasentry_core src/datasentry` →
  `uv run pytest --cov=datasentry_core --cov-fail-under=85`；提交
  Conventional Commits（英文 subject），推 main 后
  `gh run list --workflow CI --limit 1 --json conclusion --jq '.[0].conclusion'`
  观察 CI + Pages
- envelope：`_envelope(command, data)` → `{"command","data","ok"}`，
  `_emit` 按 `--format`（text/json）输出；全局旗标 `--project/--format/
  --seed` 在子命令前
- API 测试：`fastapi.testclient.TestClient(create_app(project=tmp_path))`
  （test_api.py 同款）；UI 测试 test_ui.py 同款
- 数据面语义：trend 输出与 `DatasetTrend.to_report_dict()` 完全一致
  （run_id/score/issues_total/finished_at ISO），外加 latest/delta/
  direction 摘要；空列表非错误
- 阶段收尾：统一升版 v0.9.0（pyproject + 双 `__init__.__version__` +
  CHANGELOG 收口 + tag `v0.9.0` 触发 PyPI + Pages + GitHub release，
  参照 v0.8.0=1dcd8fd 惯例）

## 三、候选后续（未排期，供下一阶段决策）

- MCP 工具补全（trend/profile/comparison 入 MCP 服务器，现有 7 工具）
- 报告本地化（--lang zh 中文/双语报告）
- 调度报告推送（扫描后自动导出 + webhook 推送 HTML 报告）
- 大文件性能（抽样扫描、增量画像）
