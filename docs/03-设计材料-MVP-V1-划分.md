# DataSentry AI — 设计材料 03：功能优先级划分（MVP / V1 / Future）

> 本划分以 7 章（项目边界）、42 章（验收标准）、44 章（12 周计划）为基础，
> 按 01 一致性检查的裁决建议（C-01~C-13）修正，并落实 02 风险识别的范围熔断（R-OD-01）。

---

## 0. 划分原则

1. **闭环优先**：MVP = 「导入 → 扫描 → Issue → 评分 → 报告 → CLI/SDK」全链路可跑通
   （对应 42.1 Phase 0~1 DoD）；
2. **无 LLM 独立价值**：MVP 不依赖任何 LLM（Phase 1 原则）；
3. **每项 V1 能力必须能在 MVP 上增量叠加，不推翻 MVP 抽象**；
4. 标注了【必选】= MVP、【可选】= V1 的原文条目，与本文冲突时以本文为准。

---

## 1. 能力总览（按功能域）

### 1.1 数据源连接器（对应 7.1/49.3）

| 连接器 | 归属 | 说明 |
|--------|------|------|
| CSV | **MVP** | 编码探测（charset-normalizer）、公式注入标记、BOM 处理 |
| Parquet | **MVP** | Arrow 原生读取 |
| JSON / JSONL | **MVP** | 含大 JSONL 流式读取 |
| XLSX | **MVP** | openpyxl 只读、多 sheet 选择、禁宏 |
| SQLite | **MVP** | 只读 URI |
| PostgreSQL | **MVP** | 只读连接 + 查询护栏（49.2） |
| MySQL / DuckDB 文件 | V1 | 复用 PG 护栏 |
| BigQuery / Snowflake | Future | 需凭据与成本评估（7.4） |

### 1.2 交互方式（对应 7.1/7.2）

| 方式 | 归属 | 说明 |
|------|------|------|
| CLI | **MVP** | 全部命令（22 章），退出码 0~4 |
| Python SDK | **MVP** | 同步 API + 文档示例 |
| REST API | **MVP** | FastAPI + 异步 Job 机制 |
| Web UI | **MVP** | 五个核心页：首页 / Dataset Overview / Column Explorer / Issue Center / 修复工作台 |
| MCP Server | V1 | 只读优先工具集（25 章） |
| GitHub Action | V1 | quality gate 封装 |

### 1.3 核心引擎（对应 8~12、15 章）

| 能力 | 归属 | 说明 |
|------|------|------|
| Schema 推断 / 数据画像 | **MVP** | DuckDB 执行层 |
| 字段语义推断（Phase A 确定性） | **MVP** | 名称字典 + 正则库 + 决策表（10.2~10.4） |
| 字段语义推断（Phase B LLM） | V1 | 10.1 融合规则 |
| 检测器注册表 + 能力声明 | **MVP** | 11.1/11.2 |
| Schema 检测器族 | **MVP** | 11.3（dtype/重复列名/混合类型等 6 个） |
| Missingness 检测器族 | **MVP** | 11.4 中 6 个核心（excessive/sudden/conditional/group/token/correlated） |
| 数值异常（稳健组） | **MVP** | IQR、Modified Z（MAD）、Percentile、Histogram rarity、Tail probability、Group-conditional |
| 数值异常（模型组） | V1 | Isolation Forest、LOF（抽样路径） |
| 日期时间检测器族 | **MVP**（P0 化，C-13） | 11.8 中 6 个核心 |
| 字符串格式检测器族 | **MVP**（P0 化，C-13） | email/phone/URL/IP/长度/控制字符/空白/公式注入 |
| 类别异常检测器 | **MVP**（P0 化，C-13） | 大小写/拼写/placeholder/explosion 子集 |
| 重复检测 Level 1+2 | **MVP** | 完全重复 + 键重复 |
| 重复检测 Level 3（模糊） | V1 | blocking + 相似度 |
| 跨字段规则检测器 | **MVP** | 11.10 安全表达式求值（读操作子集） |
| 跨表检测器（11.11） | V1 | 依赖契约引擎（C-04 裁决） |
| 漂移检测器（11.12/11.13） | V1 | 需参考版本机制 |
| 证据融合引擎 | **MVP** | 12.7 聚类合并 |
| Priority Score 评分引擎 | **MVP** | 12.8 公式（按 C-02 修正） |
| 修复引擎（DSL/预览/审批/应用/回滚/验证） | **MVP** | 15 章全链路（Phase 3，W11） |
| 修复类型数量 | **MVP** 先 8 类 | trim/normalize_case/cast_type/set_null/clip_value/map_category/replace_missing_token/impute（median/mode）；其余 V1 |
| 数据契约 DSL + 契约引擎 | V1 | 16 章（C-04 裁决）；但 `contract validate` 的**最小校验命令** MVP 提供（读 YAML 校验格式） |
| 数据指纹 + 版本管理 | **MVP** | full/sampled 指纹（19 章） |
| 历史版本比较 / 漂移 | V1 | 27 章权重之外的能力 |

### 1.4 AI Copilot（13/14 章）

| 能力 | 归属 | 说明 |
|------|------|------|
| LLMProvider 抽象（OpenAI-compatible + Mock + None） | **MVP** | 13.5/13.6，Ollama 实现 V1（C-10） |
| 脱敏管线（13.3/13.4） | **MVP** | 全部 8 类算法 |
| 五分区 Prompt 模板 + 注入防御 | **MVP** | 13.8/13.10 |
| Structured Output 校验 | **MVP** | 13.7 |
| Token 预算 / 缓存 / 降级链 | **MVP** | 13.9/53 |
| AI 解释（explain_issue） | V1 | 13.1 列表项（依赖结构化输出，W10 排期） |
| AI 规则生成 | V1 | 14 章全链路（预运行 + 批准） |
| 语义推断 Phase B | V1 | 10.1 |
| 自然语言 → 契约 | V1 | — |
| AI 修复候选生成 | V1 | — |

> 说明：原文 44 章把「LLM Provider + 安全」排在 W9（AI 能力 W10），但 **MVP 交付时点（42 章）不要求 AI 能力**，
> 因此 AI 相关全部以「V1（W9~W10）」呈现；Provider 抽象与脱敏提前到 MVP 期的唯一理由是
> Phase 1 的降级路径要求（R-TECH-02）。**最终裁决：Provider 抽象 + 脱敏 + Mock/None 进 MVP；其余 AI 能力 V1。**

### 1.5 报告与门禁（26/27 章）

| 能力 | 归属 | 说明 |
|------|------|------|
| HTML / JSON / Markdown 报告 | **MVP** | HTML 单文件自包含 |
| JUnit XML / SARIF | V1 | CI 生态 |
| 质量总分（6 维度 + 权重可配） | **MVP** | 27 章，按 C-01 裁决映射 Accuracy Proxy |
| 质量门禁 CLI（--fail-on / 退出码） | **MVP** | 22 章 + 场景 C |
| 契约导出器（Pandera/GE/SQL 草稿） | V1 | 16.2 |

### 1.6 工程与生态（17/28/29/31/32/50/51 章）

| 能力 | 归属 | 说明 |
|------|------|------|
| Monorepo + uv + CI 十阶段 | **MVP** | 37 章 |
| SQLite 元数据 + Alembic + 审计 | **MVP** | 18/47 章 |
| 安全基线（上传/表达式/PG 护栏） | **MVP** | 28 章 MVP 相关项 |
| 测试矩阵（单测/属性/集成/安全） | **MVP** | 29 章 Phase 1 子集 |
| 结构化日志 + Metric 目录 | **MVP** | 38 章 |
| 插件系统 | V1+ | 32 章 |
| 用户反馈学习 | V1+ | 31 章 |
| i18n（中英） | V1 | 50 章；MVP UI 单语言，资源文件结构预留 |
| Docker 一键启动 | **MVP** | 51 章 |
| 错误码规范 / SDK 异常体系 | **MVP** | 47 章 |
| 远程 Token 认证 | **MVP** | 24.5 |

### 1.7 明确不做（Future / Anti-Goals，7.3/7.4）

- 企业级 RBAC、多租户 SaaS 计费
- 实时流处理、Kafka/Redis/Elasticsearch 依赖
- Kubernetes 分布式执行、自建向量库
- 数据血缘平台、数据仓库/湖
- 托管云服务、SSO 集成、Snowflake/BigQuery（Future 评估）
- 自动修改生产数据库（永远不自动，必须审批）

---

## 2. MVP 交付物清单（对应 12 周计划 W1~W12 与 42 章验收）

```
MVP = Phase 0~1（W1~W6）+ Phase 2 安全子集（W9 Provider/脱敏）+ Phase 3（W11 修复闭环）
      + Phase 6 最小集（W12 demo + DQBench v0.1 骨架）
```

### MVP 硬性验收（源自 3.1 KPI + 42 章）

| # | 验收项 | 口径 |
|---|--------|------|
| M1 | 完全离线可用 | 无 LLM 配置跑通全流程（Mock 网络环境） |
| M2 | 4+ 种文件格式导入 | CSV/Parquet/JSONL/XLSX |
| M3 | 1e6 行画像 ≤ 60s | 20.4 为优化目标（C-05） |
| M4 | ≥ 20 种数据质量检查 | P0 检测器清单见 1.3（C-13） |
| M5 | Issue 结构化证据 + Severity/Confidence 分离 | 12 章 |
| M6 | 修复预览 + 回滚 + 指纹一致 | 15 章属性测试 |
| M7 | HTML/JSON 报告 + CLI/SDK + 门禁退出码 | 22/26 章 |
| M8 | 核心包覆盖率 ≥ 85% | 37.5 Stage 4 |
| M9 | Demo 数据集完整走通 < 3 分钟 | 34 章 |

### V1 交付物清单（W13~W16 + 后续）

- MCP Server、GitHub Action、JUnit/SARIF
- 数据契约 DSL 全功能 + 跨表检测器 + 多表工作区
- 漂移引擎 + 历史版本比较 + 趋势 UI
- AI 解释 / 规则生成 / 语义 Phase B / AI 修复候选
- 模糊重复（Level 3）、Isolation Forest / LOF
- 契约导出器（Pandera/GE）、插件系统、反馈学习
- Ollama Provider、i18n 双语、MySQL/DuckDB 文件连接器

### Future

- 大数据云连接器、SaaS 化、企业级 RBAC、流式处理、分布式

---

## 3. MVP 与 44 章 12 周计划的对齐表

| 周 | 原计划内容 | 调整说明 |
|----|-----------|----------|
| W1 | Monorepo + 领域模型 | 按 C-08 补全 4 个缺失模型定义 |
| W2 | 数据加载抽象 | 执行引擎确定：仅 DuckDB（R-OD-02） |
| W3 | 画像引擎 | — |
| W4 | 确定性检测器（上） | 按 C-13 将日期/字符串/类别族并入 P0 排期，确保 W6 ≥ 20 种 |
| W5 | 检测器（下）+ 融合 | 模糊重复降级为 V1（1.3 裁决） |
| W6 | 评分 + 报告 + CLI | 评分公式按 C-02 修正 |
| W7 | REST API + Job | — |
| W8 | Web UI 核心页 | — |
| W9 | LLM Provider + 安全 | 仅 Provider 抽象/脱敏/降级链进 MVP 范围（1.4 裁决） |
| W10 | AI 能力 | 整体滑入 V1（W10 并入 W13+ 计划） |
| W11 | 修复闭环 | — |
| W12 | 打磨 + 演示 + 基准 | DQBench v0.1 最小集（R-OD-03） |

> ⚠️ 与原文差异：W10 的 AI 能力（语义 Phase B、AI 解释、规则生成、契约推断）
> 依据 42 章 MVP 验收（不要求 AI）与 R-OD-03 移至 V1 计划（W13~W16），
> W10 剩余时间为性能打磨与回归。此差异请用户确认。
