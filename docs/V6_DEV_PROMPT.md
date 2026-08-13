# V6 开发计划书：报告 HTML 交互增强（Step 60~）

- 目标版本：未定（阶段收尾按仓库既有惯例统一升版）
- 前置基线：V5（Step 55~59，ADR-055~059）已发布 v0.7.0，CI 全绿
- 文档约定：ADR-060 起追加到 `docs/00-设计裁决记录-ADR.md` 末尾；
  `CHANGELOG.md` 顶部维护 `## [Unreleased]`；架构/坑位进 `docs/DEVELOPMENT.md`
- 通信语言：中文（代码/提交信息英文，注释极简——仓库风格零注释或极简）

## 一、目标与边界

### 目标

1. **报告内部联动**：质量评分条 → 维度筛选钻取；Critical Findings →
   定位并高亮对应问题行。
2. **导航增强**：粘性章节导航 + scrollspy 当前章节高亮 + 回到顶部。
3. **表格工具**：Issue Breakdown 一键全部展开/收起详情。
4. **列画像节**（Step 61，ADR-061）：扫描期画像 sidecar + HTML 交互节
   （可排序画像表、迷你空值条、语义/PII 徽标、top 类别 chips）。
5. **修复建议预览**（Step 62，ADR-062）：Issue 详情行内联确定性修复
   建议（无 server/LLM 依赖，rationale 掩码，未知检测器诚实降级）。

### 落地状态

- Step 60（ADR-060）：联动 + 导航 + 表格工具 已完成（commit 2522f76）
- Step 61（ADR-061）：Column Profiles 画像节 已完成（commit c08a9a0）
- Step 62（ADR-062）：修复建议预览内联展开 已完成

### 约束（勘察结论 + 既有约定）

- Step 49（ADR-049）已实现：severity/维度筛选、列排序、搜索、详情折叠、
  分页、迷你趋势 SVG、工作台链接——本阶段**不重复**，只做跨区块联动与
  导航补全。
- `HTML_SECTIONS` = 7 节（不含 quality_trends），导航目录锚点不会 404。
- 风格铁律：原生 JS 内联零依赖、事件委托（脚本顺序无关）、无 JS 降级为
  锚点跳转、Python 纯函数作为 JS 行为语义参照（可测）；动态数据经
  `json_script` 转义、JS 只写 `textContent`（PII 已掩码双保险）。
- 质量门禁：`uv run ruff check .` → `uv run ruff format --check .` →
  `uv run mypy packages/core/src/datasentry_core src/datasentry` →
  `uv run pytest --cov=datasentry_core --cov-fail-under=85` 全绿。

## 二、Step 60：报告内部联动与导航增强（ADR-060）

- 评分条维度段：`class="score-dim" role="button" tabindex="0"
  data-dim-link="<dim>"` + 悬停扣分提示保留 → 点击/Enter/空格应用维度筛选
  并滚动到 `#issue_breakdown`。
- Critical Findings：条目包 `<a class="finding-link" href="#issue_breakdown"
  data-issue-id="<id>">` → 清空筛选 → 重绘 → 展开详情 → 高亮 4s → 居中滚动。
- 交互表侧：行级 `data-issue-id`、`#issues._render` 导出、expand/collapse
  all 按钮；`find_issue_by_id` 纯函数（JS 行定位语义参照）。
- 导航：`#report-nav` 粘性目录 + scrollspy + `#back-to-top`。
- 验收：新增测试 ≥10 例（纯函数 + 渲染标记 + 导航/联动断言）；全量门禁绿；
  单文件自包含不变（无外链/无外部脚本）；ADR-060 + DEVELOPMENT + CHANGELOG
  [Unreleased] + 本计划书落地。

## 三、候选后续（未排期，供下一阶段决策）

- 报告间对比（同数据集多 run 评分/问题数并列）
- 深色模式（`prefers-color-scheme`）
