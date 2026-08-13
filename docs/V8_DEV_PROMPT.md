# V8 开发计划书：MCP 数据面补全 + 报告本地化 + 调度报告推送（Step 68~70）

- 目标版本：v0.10.0（阶段收尾统一升版，参照 v0.9.0=4445bf1 惯例）
- 前置基线：V7（Step 65~67，ADR-065~067）已发布 v0.9.0（tag `v0.9.0`，
  PyPI + Pages + CI 全绿），候选清空
- 文档约定：ADR-068 起追加到 `docs/00-设计裁决记录-ADR.md` 末尾；
  `CHANGELOG.md` 顶部维护 `## [Unreleased]`；架构/坑位进
  `docs/DEVELOPMENT.md`
- 通信语言：中文（代码/提交信息英文，注释极简——仓库风格零注释或极简）

## 一、目标与边界

### 背景（勘察结论）

V5~V7 已建成数据面：CLI trend list（Step 65）、REST /trends + /profiles
（Step 66）、UI 趋势页（Step 67）、MCP 服务器（Step 43，现 10 工具：
scan/list_issues/quality_score/drift×2/detectors/contract/jobs×3）。
候选后续四项（V7 计划书 §三）中三项经勘察可行且零引擎改动：

1. **MCP 数据面缺口**：`build_trends` / `load_profile` / `build_comparison`
   均无 MCP 暴露，LLM 代理无法查趋势与画像。
2. **报告本地化缺口**：报告 26 章契约、UI 页、CLI 输出文案全为硬编码
   英文（`reporting/html.py` 567 行、`ui.py`、`cli.py`），无 `--lang`。
3. **调度报告推送缺口**：scheduler 已有 webhook 通知（Step 51/52，
   JSON 摘要，`WebhookNotifier`），但扫描后不自动导出 HTML 报告、
   webhook payload 不含报告内容/路径。
4. **大文件性能**（抽样扫描、增量画像）：涉引擎改动 + 采样语义变更，
   回归面最大，**不在 V8**（V8 零引擎改动；建议独立 V9 阶段，见 §四）。

### 目标

1. **Step 68（ADR-068）MCP 工具补全**：新增 `trends_list`（全数据集
   趋势，可选 dataset_id 过滤，输出与 CLI trend list / REST /trends
   同源 `DatasetTrend.to_report_dict()`）、`profiles_get`（画像 sidecar
   原样 JSON，缺失返回 `{"ok": false, "error": ...}` 而非崩溃）、
   `comparison_build`（`build_comparison` 同数据集历史对比）。
2. **Step 69（ADR-069）报告本地化 `--lang zh`**：`datasentry report
   export --lang zh` + REST `?lang=zh` + MCP 不加语言（MCP 面向代理，
   保持英文）；范围=HTML 报告（26 章标题/节标签/徽标文案）+ CLI
   `report export` 的 text 输出 + UI 页（首页/趋势/扫描详情/工作台
   导航与按钮文案）；报告正文（issue title/描述等检测器产出）不翻译
   ——只翻译框架文案。
3. **Step 70（ADR-070）调度报告推送**：job 新增 `export_report` 选项
   （扫描完成后自动导出 HTML 报告到 reports 目录）+ webhook payload
   增加报告信息（`report_path` 相对路径 + `report_size`），webhook
   URL 不变（JSON 摘要语义兼容扩展，不破坏现有消费者）。

### 边界

- **零引擎改动**：检测器/评分/报告 26 章契约/画像生成逻辑/扫描流程
  均不动；全部消费现有 `build_trends`/`build_comparison`/`load_profile`/
  `export_report`/scheduler store
- **零依赖**：本地化用内嵌字典（`i18n.py`，en/zh 两语言），无 gettext
  框架；MCP 工具沿用现有 JSON-RPC 自实现
- **webhook 兼容**：payload 只增字段不删改，`webhook_at` 记录不变；
  无 `export_report` 的旧 job 行为不变
- 大文件性能不在本阶段（见 §四）

## 二、步骤分解

### Step 68（ADR-068）：MCP 数据面工具补全

- **落点**：`src/datasentry/mcp_server.py`（`_register_tools` 追加 3 工具）
- **语义**：
  - `trends_list(dataset_id=None)` → `[DatasetTrend.to_report_dict()]`
    （含 latest/delta/direction，与 Step 65 CLI 完全一致）
  - `profiles_get(scan_run_id)` → 画像 sidecar 原样 JSON；缺失返回
    `{"ok": false, "error": "profile not found: <id>"}`（与
    job_create 的错误返回风格一致，不抛 -32603）
  - `comparison_build(dataset_id, reference_run_id=None)` →
    `build_comparison(client.list_scan_runs(), dataset_id, reference)`
    输出；数据不足时返回空对比结构（同 HTML 报告"不渲染"的语义，
    MCP 侧返回空字典 + ok 标记）
- **测试**：test_mcp_server.py 新增 3 例（trends 空/过滤、profiles
  存在/缺失、comparison 单 run 空/多 run 有 Δ）
- **影响**：mcp_server.py + 测试 + ADR-068 + CHANGELOG + V8 计划书

### Step 69（ADR-069）：报告本地化 --lang zh

- **落点**：
  - 新 `packages/core/src/datasentry_core/reporting/i18n.py`：`L10N =
    {"en": {...}, "zh": {...}}` 键为框架文案标识（章节标题/按钮/徽标/
    导航），`t(lang, key)`；lang 校验：未知语言回退 en（core 不能依赖
    app 层，故 i18n 落 core 的 reporting 包内）
  - `reporting/html.py`：`render_html(..., lang="en")`——26 章标题、
    节标签、评分条/徽标/导航/页脚文案走 `t()`；正文与 issue 标题不译
  - `reporting/markdown.py`：`render_markdown(report, lang)` 章节标题
    本地化
  - `cli.py`：`report export` 增加 `--lang`（默认 en）；text 输出中
    受影响的部分（`score`/`issues` 等命令的英文标签）**不在本步范围**
    （只做 report export 的 HTML/Markdown 面），避免 CLI 大面改动
  - `api.py`：`GET /scans/{run_id}/report` 不支持 lang（报告 JSON
    结构化面保持英文键）；`/ui/*` 页支持 `?lang=` 查询参数
  - `ui.py`：`render_home/render_trends/render_scan_detail/
    render_workbench` 增加 lang 参数（默认 en），导航/按钮/徽标文案
    本地化；页面标题 `· DataSentry` 不变
- **测试**：test_api.py 2 例（ui?lang=zh 含中文标题、无效 lang 回退
  en）；test_cli.py 1 例（report export --lang zh 产出含中文章节标题）；
  test_ui.py 1 例（zh 导航文案）
- **影响**：i18n.py（新）+ html.py + markdown.py + cli.py + api.py +
  ui.py + 测试 4 例 + ADR-069 + CHANGELOG + DEVELOPMENT.md

### Step 70（ADR-070）：调度报告推送

- **落点**：
  - `scheduler/models.py`：`ScheduledJob` 增 `export_report: bool =
    False`（表列 `export_report` INTEGER DEFAULT 0，store 迁移
    `ALTER TABLE ... ADD COLUMN` 幂等）
  - `scheduler/core.py`：`LocalScanExecutor` 扫描完成后若
    `export_report` 则调 `client.export_report(run_id)` 导出 HTML
    到 reports 目录（复用 `_report_output_path` 语义），失败仅记录
    日志不影响调度（与 webhook 尽力而为一致）
  - `scheduler/core.py` `_notify`：payload 增 `report_path`（相对
    project 的路径，如 `.datasentry/reports/<run_id>.html`，不存在则
    省略）与 `report_size`（字节）；错误路径不携带
  - `cli.py`：`job create` 增加 `--export-report` 旗标（默认为
    `export_report: False`），`job list` 视图含该字段（models.view）
- **测试**：test_api_jobs.py 1 例（job create --export-report 落库
  字段）；test_scheduler 相关 2 例（export 成功带 report_path、导出
  失败不影响 run 状态）；webhook payload 断言 1 例
- **影响**：models.py + store.py + core.py + cli.py + 测试 +
  ADR-070 + CHANGELOG + DEVELOPMENT.md

## 三、落地状态

（开工后逐 step 更新：ADR + 测试 + CHANGELOG + 本计划书；收尾统一
升版 v0.10.0：pyproject + 双 `__init__.__version__` + CHANGELOG 收口
+ tag + GitHub release，参照 v0.9.0=4445bf1 惯例）

## 四、候选后续（未排期，供下一阶段决策）

- 大文件性能（抽样扫描、增量画像）：涉引擎改动（`engine/` 检测器
  消费全量 handle、SamplingConfig 语义扩展）、采样一致性（seed 可复
  现）与回归面最大；建议独立 V9 阶段，先行勘察 duckdb 流式读 +
  采样边界
- 报告正文翻译（issue title/描述/建议）：依赖 LLM 或字典，成本高，
  本地化只做框架文案时的自然延伸
- CLI 全局 `--lang`（score/issues/scan 等 text 输出本地化）：Step 69
  之后可视反馈追加
- MCP 工具再扩展（scan 配置透传 detector_params 等）

## 五、约束（既有约定）

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
  （test_api.py 同款）；UI 测试 test_ui.py 同款；MCP 测试
  test_mcp_server.py 同款（stdio 回环）
- 数据面语义：trend/comparison 输出与现有 CLI/REST 完全一致；
  空数据合法（空列表/空对比结构），非错误
- 阶段收尾：统一升版 v0.10.0（pyproject + 双 `__init__.__version__` +
  CHANGELOG 收口 + tag `v0.10.0` 触发 PyPI + Pages + GitHub release，
  参照 v0.9.0=4445bf1 惯例）