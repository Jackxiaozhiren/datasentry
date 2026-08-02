# DataSentry AI — Architecture Decision Records（ADR）

> 记录格式：编号 / 状态（Accepted·Proposed·Superseded）/ 日期 / 决策 / 理由 / 影响。
> 依据「36、开源要求」与「01 一致性检查」约定建立。

---

## ADR-001：质量总分维度口径（对应 C-01）

- **状态**：Accepted（2026-08-01）
- **决策**：质量总分维持 6 维度（Completeness/Validity/Uniqueness/Consistency/Integrity/Timeliness），
  Accuracy Proxy 类 Issue 归入 **Validity**，Distribution Stability 类归入 **Integrity**
  （漂移为 V1，届时再评）。
- **理由**：8 维度公式会破坏 27.2 已定权重与全部历史对齐；两个额外维度在 MVP 期检测器
  覆盖面不足，硬塞会稀释可解释性。
- **影响**：27.2 权重表不变；Issue 模型增加 `quality_dimensions` 字段（见 ADR-008）；
  9.9 映射矩阵加注归并规则。

## ADR-002：Priority Score 上限修正（对应 C-02）

- **状态**：Accepted（2026-08-01）
- **决策**：`criticality` 项归一化为 `10 × (criticality_weight − 0.6) / 1.0`，使该项取值
  ∈ [0, 10]；其余项按 12.8 原公式；最终结果 clamp 到 [0, 100]。
- **理由**：原公式在 critical 字段时理论上限 106 > 100，破坏「0–100 分制」契约。
- **影响**：12.8 公式表更新；评分引擎（Step 8）按此实现，附完整算例单测。

## ADR-003：Severity 权重单一来源（对应 C-03）

- **状态**：Accepted（2026-08-01）
- **决策**：统一使用 `SEVERITY_WEIGHTS = {info: 0.1, low: 0.25, medium: 0.5, high: 0.75, critical: 1.0}`，
  27.1 的 `severity_norm` 表废弃，维度得分公式引用同一常量。
- **理由**：两套取值并存必然漂移。
- **影响**：`datasentry_core/scoring/weights.py` 作为唯一来源。

## ADR-004：跨表检测器与契约引擎归入 V1（对应 C-04）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 仅支持单表/单文件；跨表检测器（11.11）、数据契约 DSL 全功能（16 章）为 V1；
  MVP 仅提供 `contract validate`（YAML 格式校验命令）。场景 F 标注为 V1 场景。
- **理由**：跨表检测器依赖契约声明（外键/对账表达式），契约引擎本身依赖版本机制，链条过长，
  与 7.2 原意一致。
- **影响**：12 周计划 W13+ 排期；模型层 Contract 模型仍先定义（V1 用），不做迁移。

## ADR-005：执行引擎收敛为 DuckDB（对应 R-OD-02）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 执行层只集成 DuckDB（SQL pushdown + dataframe 调用面双形态），不引入 Polars。
- **理由**：双引擎增加抽象成本与行为不一致风险；DuckDB 单文件嵌入式、零运维、覆盖分析需求。
- **影响**：17.1 推荐清单修订；Polars 列为 V1 性能优化选项。

## ADR-006：AI 能力 MVP 边界（MVP 范围差异）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 的 AI 相关仅含 Provider 抽象（OpenAI-compatible + Mock + None）、PII 脱敏管线、
  五分区模板、结构化输出校验、Token 预算与降级链；AI 解释/规则生成/语义 Phase B/契约推断
  全部移入 V1（W10 相应变为性能打磨与回归）。
- **理由**：42 章 MVP 验收不要求 AI；R-OD-03 防止研究目标挤占产品闭环。
- **影响**：12 周计划 W9 范围收窄、W10 内容调整；DQBench 消融 E5/E6 顺延。

## ADR-007：性能预算双档口径（对应 C-05）

- **状态**：Accepted（2026-08-01）
- **决策**：20.4 为优化目标档，3.1/42.1（≤60s @1e6 画像）为验收下限档；
  `benchmarks/` 脚本同时输出两档判定。
- **理由**：三处数值不一致，须固化语义。

## ADR-008：Issue 增加 quality_dimensions 字段（对应 C-16）

- **状态**：Accepted（2026-08-01）
- **决策**：`Issue.quality_dimensions: list[QualityDimension]`（枚举：8 个维度），
  由融合引擎在 Step 7 填充，评分引擎（Step 8）按 ADR-001 归并。
- **理由**：兑现 9.9「Issue 可追溯维度」承诺。

## ADR-009：表达式安全校验（对应 C-07）

- **状态**：Accepted（2026-08-01）
- **决策**：规则表达式以 AST 白名单为唯一强制校验（11.10），14.3 黑名单仅作快速预筛
  （命中即提前拒绝并提示），不做安全断言。
- **理由**：黑名单可被编码绕过；白名单才构成安全边界。
- **影响**：Step 6/Step 13 实现时引用本 ADR。

## ADR-010：存储布局二元化（对应 C-09）

- **状态**：Accepted（2026-08-01）
- **决策**：全局配置/缓存（凭据、全局设置、LLM 缓存）→ `~/.local/share/datasentry/`
  （macOS: `~/Library/Application Support/datasentry/`）；项目数据（数据集元数据、扫描、
  报告、契约、审计）→ 项目工作区 `.datasentry/`。
- **理由**：SDK 工作区（23.1）与全局初始化（51）两套表述必须归一。

## ADR-011：执行引擎与检测器的可扩展约束（对应 4.8 裁决规则）

- **状态**：Accepted（2026-08-01）
- **决策**：确认 4.8 冲突裁决规则直接作为工程约束：隐私 > 效果；证据 > 速度；确认 > 自动；
  统计 > LLM；可复现 > 完整性。
- **理由**：冲突场景在实现期必然出现，预先定序避免逐案争论。

## ADR-012：元数据迁移策略与 fingerprint 冗余列（对应 docs/04 草案）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. MVP 迁移以 `PRAGMA user_version` 递增 + 幂等 DDL（`CREATE TABLE IF NOT EXISTS`），
     旧代码打开新库（version 超限）抛错；Alembic 基线归 V1。
  2. `scan_runs` 增加 `fingerprint` 冗余 JSON 列（草案原经 `dataset_versions` 关联，
     MVP 无版本写路径，无法回读 `ScanRun.fingerprint` 必填字段）。
- **理由**：草案说明 4「Alembic 基线在 Step 后续建立」= V1 事项；MVP 避免引入迁移框架依赖。
  fingerprint 为只读冗余（同数据集同内容时 schema_hash 恒定，无漂移风险）。
- **影响**：docs/04 草案按本 ADR 修订两处；`storage/schema.py` 的 `SCHEMA_VERSION` 为唯一版本号；
  字段兼容由模型单测守护（草案说明 4）。

---

## 待定（Proposed）

| 编号 | 议题 | 状态 |
|------|------|------|
| — | Ollama Provider 的具体接入版本（V1 实现） | Proposed，W13 前置 |
| — | 插件 API 版本 1 的稳定性承诺 | Proposed，V1 前置 |
