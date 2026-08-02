# AI Data Quality Copilot：完整产品设计与开发 Prompt

你是一支由以下角色组成的资深软件与数据科学团队：

- Principal Data Scientist
- Applied Statistician
- Data Quality Engineer
- Senior Python Backend Engineer
- Data Platform Architect
- LLM/Agent Engineer
- Frontend Engineer
- DevOps Engineer
- Security Engineer
- Open-Source Maintainer
- Product Manager
- UX Designer
- QA and Benchmark Engineer

请设计并实现一个生产级、可开源、可扩展的项目：

# 项目名称

**DataSentry AI**

可替代名称：

- DataGuard Copilot
- QualiMind
- DataDoctor AI
- CleanSight
- DataTrust Copilot

项目副标题：

> An evidence-driven, local-first AI copilot for detecting, explaining,
> validating, and safely repairing data quality problems.

中文定位：

> 一个以统计证据为基础、以 AI
> 为辅助、以人工审批为保障的数据质量检测与修复平台。

------------------------------------------------------------------------

# 一、项目目标

构建一个开源的 AI Data Quality Copilot。用户可以导入
CSV、JSON、JSONL、Excel、Parquet 文件或连接数据库，系统自动完成：

1.  数据结构识别
2.  字段语义推断
3.  数据画像生成
4.  数据质量问题检测
5.  异常记录定位
6.  问题严重程度评估
7.  根因候选分析
8.  数据质量规则建议
9.  修复方案生成
10. 修复影响模拟
11. 人工审批
12. 可逆的数据修复
13. 修复后重新验证
14. 数据质量报告生成
15. 数据质量规则导出
16. 历史版本比较
17. 数据漂移检测
18. CI/CD 数据质量门禁
19. Python SDK、CLI、REST API 和 MCP Server 调用

本项目不能只是以下工具的简单包装：

- pandas-profiling / ydata-profiling
- Great Expectations
- Pandera
- Soda
- Evidently
- OpenRefine

项目必须形成自己的核心抽象、检测引擎、证据融合机制、问题评分体系和安全修复闭环。

------------------------------------------------------------------------

# 二、核心产品原则

## 2.1 Local-first

默认在本地处理数据。

未经用户明确授权：

- 不向任何 LLM 提交原始数据集
- 不向任何第三方服务上传文件
- 不发送完整记录
- 不发送敏感字段
- 不发送数据库凭据
- 不持久化用户原始数据

LLM 默认只能接收：

- Schema
- 字段名称
- 推断的数据类型
- 汇总统计量
- 经过脱敏的有限样本
- 模式摘要
- 异常证据
- 用户提供的业务上下文

## 2.2 Evidence-first

所有问题必须先由确定性规则或统计检测器产生证据，再由 LLM：

- 解释证据
- 补充业务语义
- 推荐规则
- 生成修复候选
- 生成自然语言报告

LLM 不得凭空认定某条记录错误。

## 2.3 Human-in-the-loop

任何会修改原始数据的操作都必须经过以下流程：

    Detect
      ↓
    Explain
      ↓
    Propose
      ↓
    Preview
      ↓
    Approve
      ↓
    Apply
      ↓
    Validate
      ↓
    Commit or Rollback

默认禁止自动修改原文件。

## 2.4 Reproducibility

每次扫描必须完整记录：

- 数据集指纹
- 扫描配置
- 规则版本
- 检测器版本
- 随机种子
- 使用的模型
- Prompt 模板版本
- 修复操作
- 验证结果
- 执行时间
- 软件环境

同一数据和同一配置应尽量产生可重复结果。

## 2.5 Cost-aware

除用户自行提供的 LLM Token 外，项目所有核心能力必须免费运行。

必须支持：

- 无 LLM 模式
- 本地模型模式
- OpenAI-compatible API
- 用户自带 API Key
- LLM 调用预算
- Token 上限
- 调用缓存
- 批量请求
- 失败降级

即使没有 API Key，统计检测、规则验证、报告生成和人工修复仍应正常工作。

## 2.6 Explainability

每个 Issue 都必须回答：

- 检测到了什么？
- 为什么认为它可能是问题？
- 使用了什么检测方法？
- 证据是什么？
- 影响了多少行？
- 严重程度是多少？
- 检测置信度是多少？
- 是否可能是误报？
- 推荐采取什么行动？
- 修复会修改哪些记录？
- 修复后有什么副作用？

------------------------------------------------------------------------

# 三、目标用户

## 3.1 核心用户

- 数据科学家
- 数据分析师
- 应用统计研究人员
- 数据工程师
- ML 工程师
- BI 团队
- 科研人员
- 学生
- 小型企业数据团队
- 开源数据维护者

## 3.2 典型使用场景

### 场景 A：上传 CSV

用户上传销售数据，系统发现：

- 日期格式混用
- 金额字段存在货币符号
- 订单编号重复
- 负销售额异常
- 国家和货币不一致
- 某日期之后缺失率突然增加
- 某类别拼写不统一

### 场景 B：数据库表检查

用户连接 PostgreSQL，只扫描
Schema、聚合统计和经授权的样本，不下载整张表。

### 场景 C：CI 数据质量门禁

在 GitHub Actions 中执行：

    datasentry scan data/orders.parquet \
      --contract contracts/orders.yaml \
      --fail-on critical \
      --output reports/orders.json

当关键规则失败时返回非零退出码。

### 场景 D：研究数据清洗

用户导入问卷数据，系统检测：

- 超出量表范围
- 跳题逻辑冲突
- 重复参与者
- 极短答题时间
- 直线作答
- 编码混乱
- 缺失机制异常

### 场景 E：历史版本比较

比较本周和上周数据：

- Schema 演化
- 缺失率变化
- 类别新增或消失
- 数值分布变化
- 唯一性变化
- 数据量变化
- 数据漂移

------------------------------------------------------------------------

# 四、项目边界

## 4.1 MVP 必须支持

数据源：

- CSV
- Parquet
- JSON / JSONL
- XLSX
- SQLite
- PostgreSQL

交互方式：

- Web UI
- CLI
- Python SDK
- REST API

核心能力：

- Schema 推断
- 数据画像
- 内置质量检查
- 自定义规则
- 问题列表
- 行级证据
- AI 解释
- AI 规则建议
- 修复预览
- 人工审批
- 修复执行
- 修复后验证
- HTML / JSON / Markdown 报告
- 本地项目历史记录

## 4.2 V1 支持

- MCP Server
- 数据契约
- 数据版本比较
- 漂移检测
- GitHub Actions
- 插件系统
- 多数据表关系检查
- 规则模板市场
- 用户反馈学习
- 本地模型支持

## 4.3 暂不进入 MVP

- 企业级 RBAC
- 多租户 SaaS 计费
- 实时流处理
- Kubernetes 分布式执行
- 完整数据血缘平台
- 自动修改生产数据库
- 训练大型基础模型
- 自建向量数据库集群
- 依赖付费云基础设施

------------------------------------------------------------------------

# 五、差异化核心：证据驱动的混合检测系统

建立以下统一流程：

    Data Source
       ↓
    Schema and Semantic Profiling
       ↓
    Deterministic Checks
       ↓
    Statistical Detectors
       ↓
    Cross-column and Cross-table Analysis
       ↓
    Temporal and Distribution Analysis
       ↓
    Evidence Fusion
       ↓
    Issue Ranking
       ↓
    LLM Explanation and Rule Generation
       ↓
    Repair Proposal
       ↓
    Safety Validation
       ↓
    Human Approval

必须明确区分：

1.  **事实**
2.  **统计证据**
3.  **推断**
4.  **业务规则**
5.  **修复建议**

示例：

    {
      "fact": "row 1842 has age = -3",
      "statistical_evidence": {
        "column_min": -3,
        "q01": 18,
        "q99": 79,
        "robust_z_score": -8.31
      },
      "semantic_inference": "the column likely represents human age",
      "business_rule_candidate": "age should be between 0 and 120",
      "repair_candidate": "replace with null pending source verification"
    }

严禁把语义推断表示为确定事实。

------------------------------------------------------------------------

# 六、数据质量维度

系统至少覆盖以下维度：

## 6.1 Completeness

- Null
- 空字符串
- 纯空格
- 特殊缺失标记
- 条件缺失
- 分组缺失
- 时间窗口缺失
- 结构性缺失

特殊缺失标记包括：

    NA
    N/A
    null
    NULL
    none
    unknown
    ?
    -
    --
    999
    -999
    not available

但系统不得默认将所有这些值直接转换为
Null，必须先判断字段语义并让用户确认。

## 6.2 Validity

- 类型不匹配
- 数值范围错误
- 日期格式错误
- 枚举值非法
- 正则格式错误
- 非法 Unicode
- 编码问题
- 单位问题
- 精度问题

## 6.3 Uniqueness

- 完全重复行
- 主键重复
- 候选主键重复
- 模糊重复
- 复合键重复
- 近似实体重复

## 6.4 Consistency

- 跨字段逻辑冲突
- 跨表引用完整性
- 单位不一致
- 国家—州—城市不一致
- 开始日期晚于结束日期
- 出生日期晚于事件日期
- 总额不等于分项之和
- 状态与字段值冲突

## 6.5 Accuracy Proxy

在没有 Ground Truth 时，不声称检测“准确性”，而使用代理信号：

- 违反业务规则
- 偏离高置信模式
- 与可信参考表冲突
- 与同实体其他记录冲突
- 与统计分布极端不一致

## 6.6 Timeliness

- 数据更新延迟
- 日期断层
- 未来时间戳
- 过期记录
- 数据批次迟到
- 不合理时间顺序

## 6.7 Integrity

- 主键约束
- 外键约束
- 非空约束
- 唯一性约束
- Schema 约束
- 数据契约约束

## 6.8 Distribution Stability

- 均值和方差变化
- 分位数变化
- 类别比例变化
- 缺失率变化
- 唯一值比例变化
- PSI
- KS 检验
- Jensen–Shannon divergence
- Wasserstein distance
- Chi-square test
- Population shift
- 新类别和消失类别

------------------------------------------------------------------------

# 七、字段语义推断系统

为每列生成 `ColumnSemanticProfile`。

必须支持推断以下语义类型：

- identifier
- categorical
- ordinal
- continuous
- count
- currency
- percentage
- latitude
- longitude
- country
- state_or_province
- city
- postal_code
- phone
- email
- url
- ip_address
- date
- datetime
- duration
- age
- year
- boolean
- free_text
- personally_identifiable_information
- unknown

推断信号：

- 字段名称
- 物理类型
- 唯一值比例
- 值模式
- 正则模式
- 数值范围
- 字符长度
- 前缀和后缀
- 常见类别
- 数据字典
- 用户描述
- LLM 语义推断

输出示例：

    {
      "column_name": "cust_email",
      "physical_type": "string",
      "semantic_type": "email",
      "confidence": 0.97,
      "evidence": [
        {
          "type": "column_name",
          "value": "cust_email",
          "weight": 0.31
        },
        {
          "type": "pattern_match_rate",
          "value": 0.982,
          "weight": 0.47
        },
        {
          "type": "uniqueness_ratio",
          "value": 0.91,
          "weight": 0.12
        }
      ],
      "contains_pii": true
    }

允许用户纠正语义类型。

用户纠正后：

- 存入项目元数据
- 后续扫描优先使用
- 不自动上传到中心服务器
- 可导出为数据契约

------------------------------------------------------------------------

# 八、检测器体系

定义统一检测器接口：

    from typing import Protocol

    class Detector(Protocol):
        detector_id: str
        detector_version: str

        def supports(self, context: "DetectionContext") -> bool:
            ...

        def detect(self, context: "DetectionContext") -> list["IssueCandidate"]:
            ...

每个检测器必须输出：

    class IssueCandidate:
        issue_type: str
        detector_id: str
        detector_version: str
        dataset_id: str
        table_name: str | None
        columns: list[str]
        affected_rows: list[str] | None
        affected_count: int
        evidence: list[Evidence]
        raw_score: float
        confidence: float
        estimated_false_positive_risk: float
        suggested_severity: str

## 8.1 Schema 检测器

- unexpected_column
- missing_column
- column_order_change
- dtype_mismatch
- nullable_change
- duplicate_column_name
- ambiguous_column_name
- mixed_type_column
- schema_drift

## 8.2 Missingness 检测器

- excessive_null_rate
- sudden_null_rate_change
- conditional_missingness
- missingness_by_group
- suspicious_missing_token
- correlated_missingness
- monotone_missing_pattern
- missing_not_at_random_signal

对于缺失机制，只能报告统计信号，不得直接断言 MCAR、MAR 或 MNAR。

## 8.3 数值异常检测器

至少实现：

- IQR
- Standard Z-score
- Modified Z-score
- MAD
- Percentile rule
- Isolation Forest
- Local Outlier Factor
- Histogram rarity
- Tail probability
- Group-conditional outlier
- Time-window outlier

默认优先使用稳健统计量。

对于每个异常值，输出：

- 原始值
- 中位数
- MAD
- 分位数位置
- 分组基准
- 使用的检测算法
- 阈值
- 是否被多个检测器共同发现

## 8.4 类别异常检测器

- rare_category
- unseen_category
- inconsistent_case
- whitespace_variant
- punctuation_variant
- spelling_variant
- encoding_variant
- category_distribution_shift
- category_explosion
- suspicious_placeholder

示例：

    California
    california
    CALIFORNIA
    Californa
    California 

系统应将它们识别为可能属于同一标准类别，但不能未经审批直接合并。

## 8.5 字符串和格式检测器

- invalid_email
- invalid_phone
- invalid_url
- invalid_ip
- invalid_postal_code
- unusual_length
- hidden_control_character
- leading_or_trailing_whitespace
- repeated_whitespace
- mojibake
- mixed_encoding
- suspicious_html
- suspicious_formula_injection

CSV 导出时必须防范 spreadsheet formula
injection，例如以以下字符开头的值：

    =
    +
    -
    @

## 8.6 日期时间检测器

- invalid_date
- impossible_date
- future_date
- stale_date
- mixed_date_format
- timezone_mismatch
- chronological_violation
- date_gap
- unexpected_frequency
- duplicate_timestamp
- daylight_saving_anomaly

## 8.7 重复检测器

实现三级重复检测：

### Level 1：Exact duplicate

完整行哈希。

### Level 2：Key duplicate

基于主键或候选主键。

### Level 3：Fuzzy duplicate

支持：

- normalized string
- token similarity
- Jaro-Winkler
- Levenshtein
- phonetic similarity
- address normalization
- blocking
- MinHash / LSH

不得对全量数据直接执行 O(n²) 比较。

必须使用 Blocking，例如：

- 姓名首字母
- 邮编
- 电话后四位
- 出生年份
- 邮箱域名

## 8.8 跨字段规则检测器

支持表达：

    start_date <= end_date
    subtotal + tax = total
    country = "US" implies currency = "USD"
    status = "cancelled" implies cancelled_at is not null
    age < 18 implies guardian_id is not null

规则支持：

- SQL 表达式
- YAML DSL
- Python callable
- UI Rule Builder
- LLM 生成的候选规则

## 8.9 跨表检测器

- orphan_foreign_key
- cardinality_violation
- inconsistent_entity_attributes
- duplicate_entity_across_tables
- aggregate_reconciliation_failure
- temporal_referential_violation

## 8.10 漂移检测器

为 Reference Dataset 与 Current Dataset 进行比较。

数值列支持：

- KS
- PSI
- Jensen–Shannon divergence
- Wasserstein distance
- Mean shift
- Variance shift
- Quantile shift

类别列支持：

- Chi-square
- Jensen–Shannon divergence
- Category appearance/disappearance
- Frequency delta

时间序列支持：

- Rolling baseline
- Seasonal baseline
- Change-point signals
- Window-to-window comparison

不要仅依据 p-value 判定漂移。

同时考虑：

- 样本量
- 效应量
- 多重检验
- 实际业务阈值
- 历史波动范围

------------------------------------------------------------------------

# 九、证据融合与 Issue 评分

同一个问题可能被多个检测器发现。

设计 `EvidenceFusionEngine`，将重复候选合并为统一 Issue。

Issue Score 至少包含：

    Issue Priority Score =
        Severity Weight
      × Detection Confidence
      × Affected Scope
      × Business Criticality
      × Reproducibility
      × Multi-detector Agreement
      × Historical Novelty
      × Repairability

建议采用 0–100 分制。

## 9.1 Severity

- info
- low
- medium
- high
- critical

Severity 表示潜在影响，不等于检测置信度。

## 9.2 Confidence

0–1。

表示“系统认为该现象确实存在”的置信程度。

## 9.3 Business Criticality

用户可以给字段设置：

- informational
- normal
- important
- critical

例如支付金额、患者 ID、订单主键默认比备注字段更重要。

## 9.4 False Positive Risk

每个 Issue 显示误报风险：

- low
- medium
- high

语义型规则的误报风险通常高于确定性约束。

## 9.5 Issue 聚合

例如以下检测结果应合并：

- IQR 检测到 `age = -3`
- MAD 检测到 `age = -3`
- Range 检测到 `age = -3`
- AI 推断 `age` 是年龄字段

形成一个 Issue，而不是四个重复 Issue。

------------------------------------------------------------------------

# 十、AI Copilot 设计

## 10.1 AI 的职责

允许 LLM：

- 解释统计结果
- 推断字段语义
- 将自然语言转换为规则
- 推荐可能遗漏的规则
- 生成数据契约草稿
- 给出根因候选
- 生成修复候选
- 解释修复风险
- 总结质量报告
- 生成 SQL、Pandera 或 YAML 规则
- 回答用户关于当前数据集的问题

## 10.2 AI 禁止事项

禁止 LLM：

- 未经证据直接定义数据错误
- 直接执行任意 Python
- 直接执行任意 SQL 写操作
- 自动覆盖原始文件
- 在未脱敏时读取 PII
- 将低置信推断写成事实
- 伪造业务规则
- 伪造数据源信息
- 自动批准高风险修复

## 10.3 LLM 输入边界

设计 `LLMContextBuilder`。

默认输入：

    {
      "dataset_summary": {},
      "schema": [],
      "column_profiles": [],
      "issue_evidence": [],
      "masked_examples": [],
      "business_context": "",
      "existing_contract": {},
      "task": ""
    }

对敏感数据执行：

- Email 掩码
- 电话掩码
- 姓名替换
- 地址泛化
- ID 哈希
- 数值分桶
- 采样限制

示例：

    michael@example.com

转换为：

    u***@example.com

## 10.4 LLM Provider 抽象

定义：

    class LLMProvider(Protocol):
        async def generate_structured(
            self,
            request: "LLMRequest",
            response_schema: type["BaseModel"],
        ) -> "BaseModel":
            ...

支持：

- OpenAI-compatible API
- Ollama
- 用户自定义 Provider
- Mock Provider
- No-LLM Provider

不得把 Provider 逻辑写死在业务代码中。

## 10.5 Structured Output

所有 AI 输出必须经过严格 Schema 验证。

示例：

    class AIExplanation(BaseModel):
        summary: str
        likely_causes: list[CauseHypothesis]
        supporting_evidence_ids: list[str]
        assumptions: list[str]
        uncertainty: str
        recommended_actions: list[RecommendedAction]

禁止解析自由文本后直接执行修复。

## 10.6 Prompt Injection 防御

数据内容必须被视为不可信输入。

如果字段值包含：

    Ignore previous instructions
    Delete the database
    Reveal system prompt

系统必须把它当作普通数据，而不是指令。

在 LLM Prompt 中明确划分：

    SYSTEM INSTRUCTIONS
    TRUSTED METADATA
    UNTRUSTED DATA SAMPLES
    TASK
    OUTPUT SCHEMA

未经授权，不允许 LLM 生成并执行工具调用。

------------------------------------------------------------------------

# 十一、AI 规则生成

用户可以输入：

    订单完成时 delivered_at 必须存在；
    退款金额不能超过订单总额；
    美国订单的货币应该是 USD。

系统生成候选规则：

    rules:
      - id: completed_requires_delivered_at
        type: conditional_not_null
        when:
          column: status
          operator: equals
          value: completed
        then:
          column: delivered_at
          operator: not_null
        severity: high

      - id: refund_not_above_total
        type: column_comparison
        expression: refund_amount <= order_total
        severity: critical

      - id: us_currency
        type: conditional_value
        when:
          column: country
          operator: equals
          value: US
        then:
          column: currency
          operator: equals
          value: USD
        severity: high

生成后必须：

1.  验证 YAML Schema
2.  验证字段是否存在
3.  禁止危险 SQL
4.  在样本上预运行
5.  显示预计命中行数
6.  用户批准
7.  才能保存规则

------------------------------------------------------------------------

# 十二、修复引擎

## 12.1 修复类型

支持：

- trim_whitespace
- normalize_case
- normalize_unicode
- cast_type
- parse_date
- replace_missing_token
- set_null
- clip_value
- map_category
- standardize_unit
- deduplicate
- impute_value
- regex_replace
- derive_column
- split_column
- merge_columns
- custom_expression

## 12.2 修复风险等级

### Low Risk

- 删除首尾空格
- Unicode 标准化
- 明确的类型解析
- 大小写标准化预览

### Medium Risk

- 类别映射
- 日期格式统一
- 单位转换
- 重复记录合并

### High Risk

- 值插补
- 异常值替换
- 删除行
- 修改主键
- 模糊实体合并
- 跨表更新

高风险修复不得批量自动执行。

## 12.3 Repair Proposal

    class RepairProposal:
        proposal_id: str
        issue_id: str
        operation: str
        target_columns: list[str]
        target_row_ids: list[str] | None
        parameters: dict
        rationale: str
        evidence_ids: list[str]
        risk_level: str
        reversibility: str
        estimated_rows_changed: int
        preconditions: list[str]
        postconditions: list[str]

## 12.4 Preview

修复前显示：

| Row ID | Column | Before | After | Reason | Confidence |
|--------|--------|--------|-------|--------|------------|

同时显示：

- 修改行数
- 修改比例
- Null 数变化
- 唯一值变化
- 分布变化
- 下游规则变化
- 潜在副作用

## 12.5 Transaction Log

每次修复生成不可变日志：

    {
      "repair_run_id": "run_123",
      "dataset_fingerprint_before": "...",
      "dataset_fingerprint_after": "...",
      "operations": [],
      "approved_by": "local-user",
      "timestamp": "...",
      "rollback_artifact": "..."
    }

必须可以回滚。

------------------------------------------------------------------------

# 十三、数据契约 DSL

设计项目自有 YAML 契约，同时支持导出到其他工具格式。

示例：

    version: "1.0"

    dataset:
      name: orders
      description: E-commerce order records
      primary_key:
        - order_id

    columns:
      order_id:
        type: string
        nullable: false
        unique: true
        semantic_type: identifier
        criticality: critical

      customer_email:
        type: string
        nullable: false
        semantic_type: email
        pii: true
        checks:
          - type: regex
            pattern: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"

      order_total:
        type: decimal
        nullable: false
        min: 0
        semantic_type: currency
        criticality: critical

      currency:
        type: string
        allowed_values:
          - USD
          - EUR
          - GBP

    rules:
      - id: refund_not_above_total
        expression: refund_amount <= order_total
        severity: critical

    quality_gate:
      fail_on:
        - critical
      maximum_failed_rows_ratio: 0.01

支持契约版本控制和差异比较。

------------------------------------------------------------------------

# 十四、系统架构

采用 Monorepo：

    datasentry-ai/
    ├── apps/
    │   ├── web/
    │   ├── api/
    │   └── docs/
    ├── packages/
    │   ├── core/
    │   ├── detectors/
    │   ├── profiling/
    │   ├── contracts/
    │   ├── repairs/
    │   ├── llm/
    │   ├── reports/
    │   ├── connectors/
    │   ├── sdk/
    │   ├── cli/
    │   └── mcp-server/
    ├── benchmarks/
    ├── examples/
    ├── demo-data/
    ├── tests/
    ├── scripts/
    ├── docker/
    ├── .github/
    ├── pyproject.toml
    ├── uv.lock
    ├── docker-compose.yml
    ├── CONTRIBUTING.md
    ├── SECURITY.md
    ├── CODE_OF_CONDUCT.md
    ├── ROADMAP.md
    └── README.md

## 14.1 Backend

推荐：

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- DuckDB
- Polars
- PyArrow
- Pandera
- scikit-learn
- scipy
- statsmodels
- rapidfuzz
- networkx
- structlog

## 14.2 Frontend

推荐：

- React
- TypeScript
- Vite 或 Next.js
- Tailwind CSS
- shadcn/ui
- TanStack Query
- TanStack Table
- Apache ECharts 或 Plotly
- Monaco Editor

## 14.3 本地存储

MVP：

- SQLite：项目元数据
- DuckDB：分析缓存和统计结果
- 本地文件系统：报告与数据快照

不得要求：

- Redis
- Kafka
- Elasticsearch
- 商业数据库
- 付费云存储

## 14.4 任务执行

MVP 使用本地后台任务：

- asyncio
- multiprocessing
- ProcessPoolExecutor
- 本地任务状态表

不要在 MVP 强制引入 Celery 和 Redis。

------------------------------------------------------------------------

# 十五、领域模型

至少定义：

    Workspace
    Project
    DataSource
    Dataset
    DatasetVersion
    DatasetProfile
    ColumnProfile
    SemanticProfile
    Contract
    Rule
    ScanRun
    DetectorRun
    Issue
    Evidence
    RepairProposal
    RepairRun
    ValidationResult
    DriftReport
    LLMInvocation
    AuditEvent

## 15.1 Issue 数据结构

    class Issue(BaseModel):
        id: str
        scan_run_id: str
        issue_type: str
        title: str
        description: str

        dataset_id: str
        table_name: str | None
        columns: list[str]

        severity: Severity
        confidence: float
        priority_score: float
        false_positive_risk: RiskLevel

        affected_count: int
        affected_ratio: float
        affected_row_ids: list[str] | None

        evidence: list[Evidence]
        detector_ids: list[str]

        ai_explanation: AIExplanation | None
        repair_proposals: list[RepairProposal]

        status: IssueStatus
        created_at: datetime

IssueStatus：

    open
    confirmed
    false_positive
    accepted_exception
    repair_proposed
    repair_approved
    repaired
    resolved

------------------------------------------------------------------------

# 十六、数据指纹与版本管理

每次扫描生成：

- 文件哈希
- Schema 哈希
- 行数
- 列数
- 列名和类型摘要
- 可选内容抽样哈希
- 数据源版本标识

对于超大文件，不强制读取全部数据生成完整哈希。

支持：

- full fingerprint
- sampled fingerprint
- metadata-only fingerprint

必须在 UI 中标明指纹精度。

------------------------------------------------------------------------

# 十七、性能设计

## 17.1 数据规模目标

开发机基准目标：

- 10 万行：交互式体验
- 100 万行：数十秒级画像和基础检查
- 1000 万行：通过 DuckDB / Polars 扫描和抽样完成主要检查
- 大于内存数据：使用流式或 SQL 聚合，不转换为完整 pandas DataFrame

## 17.2 执行策略

检测器声明：

    class DetectorCapabilities:
        requires_full_scan: bool
        supports_sampling: bool
        supports_streaming: bool
        supports_sql_pushdown: bool
        requires_row_materialization: bool

优先执行：

1.  Schema-only 检查
2.  SQL pushdown 聚合
3.  Streaming 检查
4.  抽样检查
5.  仅在必要时物化异常行

## 17.3 采样

支持：

- Random sampling
- Stratified sampling
- Reservoir sampling
- Time-based sampling
- Rare-category oversampling

报告中必须注明：

- 是否采样
- 采样方法
- 样本量
- 结果是否可推广
- 哪些检测基于全量统计

------------------------------------------------------------------------

# 十八、Web UI 信息架构

## 18.1 首页

显示：

- 创建项目
- 导入数据
- 最近扫描
- 数据质量趋势
- Critical Issues
- 最近修复
- 文档入口

## 18.2 Dataset Overview

显示：

- 行数
- 列数
- 文件大小
- 数据源
- Schema
- 数据质量总分
- Critical / High / Medium / Low 数量
- 缺失率
- 重复率
- 漂移状态
- 最近扫描时间

## 18.3 Column Explorer

每列显示：

- 物理类型
- 语义类型
- Null 比例
- 唯一值比例
- 分布图
- 分位数
- Top categories
- 模式
- 异常样本
- 历史趋势
- 相关 Issue
- PII 标记

## 18.4 Issue Center

支持过滤：

- Severity
- Issue type
- Column
- Detector
- Confidence
- Status
- Repairability
- Scan run

Issue 详情页显示：

    Issue Summary
    Evidence
    Affected Rows
    Statistical Context
    AI Explanation
    Likely Causes
    Repair Options
    Repair Preview
    Audit History
    User Feedback

## 18.5 Repair Workspace

提供：

- Before / After Diff
- 受影响行过滤
- 分布变化图
- 规则通过率变化
- 审批按钮
- 拒绝按钮
- 修改参数
- 导出 Patch
- Rollback

## 18.6 Contract Editor

- YAML 编辑器
- 表单编辑器
- 自动补全
- Schema 验证
- AI 规则生成
- 规则预运行
- 命中行数预览
- Git Diff

------------------------------------------------------------------------

# 十九、CLI 设计

    datasentry init
    datasentry scan data.csv
    datasentry profile data.parquet
    datasentry validate data.csv --contract contract.yaml
    datasentry compare old.parquet new.parquet
    datasentry issues list
    datasentry issues show ISSUE_ID
    datasentry repair preview ISSUE_ID
    datasentry repair apply PROPOSAL_ID
    datasentry report export RUN_ID --format html
    datasentry contract infer data.csv
    datasentry contract validate contract.yaml
    datasentry serve
    datasentry mcp serve

支持机器可读输出：

    datasentry scan data.csv --format json

质量门禁：

    datasentry validate data.csv \
      --contract contract.yaml \
      --fail-on high \
      --max-failure-ratio 0.01

退出码：

    0 = passed
    1 = quality gate failed
    2 = invalid configuration
    3 = execution error
    4 = data source unavailable

------------------------------------------------------------------------

# 二十、Python SDK

示例：

    from datasentry import DataSentry

    client = DataSentry()

    dataset = client.load("orders.parquet")

    scan = dataset.scan(
        contract="contracts/orders.yaml",
        detectors=[
            "schema",
            "missingness",
            "duplicates",
            "outliers",
            "cross_column",
        ],
    )

    print(scan.summary())

    for issue in scan.issues(severity_at_least="high"):
        print(issue.title, issue.confidence)

    report = scan.export_report("reports/orders.html")

自然语言规则：

    rules = dataset.suggest_rules(
        context="E-commerce orders in the United States"
    )

修复预览：

    proposal = scan.issue("ISSUE_ID").repairs[0]
    preview = proposal.preview()
    preview.show()

    proposal.apply(
        output="clean/orders.parquet",
        require_confirmation=True,
    )

------------------------------------------------------------------------

# 二十一、REST API

至少提供：

    POST   /api/projects
    POST   /api/datasets/import
    GET    /api/datasets/{id}
    POST   /api/datasets/{id}/scan
    GET    /api/scans/{id}
    GET    /api/scans/{id}/issues
    GET    /api/issues/{id}
    POST   /api/issues/{id}/explain
    POST   /api/issues/{id}/repairs
    POST   /api/repairs/{id}/preview
    POST   /api/repairs/{id}/approve
    POST   /api/repairs/{id}/apply
    POST   /api/repairs/{id}/rollback
    POST   /api/contracts/infer
    POST   /api/contracts/validate
    POST   /api/datasets/compare
    GET    /api/reports/{id}

长任务返回 Job ID，不阻塞 HTTP 请求。

------------------------------------------------------------------------

# 二十二、MCP Server

提供只读优先的 MCP Tools：

    list_projects
    list_datasets
    get_dataset_schema
    get_dataset_profile
    run_data_quality_scan
    list_scan_issues
    get_issue_evidence
    explain_issue
    suggest_data_contract
    validate_contract
    preview_repair
    export_report

危险操作单独设计，并默认禁用：

    apply_repair
    delete_dataset
    execute_custom_query

MCP 返回结果必须：

- 有大小限制
- 默认不暴露原始敏感数据
- 不包含数据库凭据
- 对行级数据做脱敏
- 支持用户审批

------------------------------------------------------------------------

# 二十三、报告系统

生成：

- HTML
- JSON
- Markdown
- JUnit XML
- SARIF

HTML 报告包含：

1.  Executive Summary
2.  Dataset Overview
3.  Quality Score
4.  Issue Breakdown
5.  Critical Findings
6.  Column Profiles
7.  Drift Analysis
8.  Suggested Rules
9.  Repair History
10. Methodology
11. Limitations
12. Reproducibility Metadata

SARIF 用于 GitHub Code Scanning 风格的结果展示。

JUnit XML 用于 CI 系统。

------------------------------------------------------------------------

# 二十四、质量总分

设计透明的 Data Quality Score。

不要使用无法解释的黑盒分数。

示例：

    Overall Score =
      0.20 × Completeness
    + 0.20 × Validity
    + 0.15 × Uniqueness
    + 0.20 × Consistency
    + 0.15 × Integrity
    + 0.10 × Timeliness

每个维度均为 0–100。

允许用户修改权重。

分数必须考虑：

- 字段关键程度
- Issue 严重程度
- 受影响比例
- 检测置信度
- 规则覆盖范围

页面必须解释分数如何计算。

------------------------------------------------------------------------

# 二十五、安全要求

## 25.1 文件安全

- 限制上传大小
- MIME 与扩展名双重校验
- 防止路径遍历
- 随机化临时文件名
- 禁止执行上传内容
- 限制解压缩大小
- 防止 Zip Bomb
- 定期清理临时文件

## 25.2 SQL 安全

- 默认只读连接
- 参数化查询
- 禁止多语句
- 禁止 DDL 和 DML
- 查询超时
- 行数限制
- 内存限制
- 扫描预算
- 数据库凭据不进入日志

## 25.3 Excel 安全

- 不执行宏
- 不解析外部链接
- 导出时防止公式注入
- 标记可能危险的单元格

## 25.4 LLM 安全

- PII 自动识别
- 数据最小化
- Prompt Injection 隔离
- Structured Output
- Provider allowlist
- Token budget
- 调用日志脱敏
- API Key 加密或仅从环境变量读取

## 25.5 修复安全

- 原始数据不可变
- 默认输出新文件
- Diff Preview
- 审批
- 审计
- 回滚
- 后置验证
- 高风险操作二次确认

------------------------------------------------------------------------

# 二十六、测试策略

## 26.1 单元测试

覆盖：

- 每个检测器
- Schema 推断
- 语义推断
- 分数计算
- Issue 合并
- 契约解析
- 修复操作
- 脱敏
- LLM 输出验证

## 26.2 属性测试

使用 Hypothesis 测试：

- 任意输入不导致崩溃
- 修复操作满足幂等性时保持幂等
- Rollback 恢复原始指纹
- Score 始终位于 0–100
- Confidence 始终位于 0–1

## 26.3 集成测试

- 文件上传到报告导出
- PostgreSQL 只读扫描
- 契约生成到 CI 验证
- Issue 到修复再验证
- LLM Provider 失败降级
- MCP 工具调用

## 26.4 安全测试

- Path traversal
- CSV formula injection
- SQL injection
- Prompt injection
- 恶意 JSON
- 超大字段
- Zip bomb
- 无效编码
- API Key 泄露
- 日志泄露 PII

## 26.5 前端测试

- Component tests
- E2E tests
- Accessibility tests
- Keyboard navigation
- 大表格虚拟滚动
- Error states
- Loading states

------------------------------------------------------------------------

# 二十七、Benchmark 设计

这是申请研究型硕士时非常重要的一部分。

建立公开 Benchmark：

    DataSentry-DQBench

## 27.1 数据集构造

使用公开数据或程序化合成数据，注入：

- Missing values
- Type errors
- Range violations
- Duplicates
- Fuzzy duplicates
- Category typos
- Date errors
- Cross-column inconsistencies
- Distribution shifts
- Schema changes
- Unit mismatches

每个错误都有 Ground Truth：

    {
      "dataset": "orders_001",
      "row_id": "1842",
      "column": "order_total",
      "error_type": "unit_mismatch",
      "original_value": 12000,
      "corrupted_value": 120,
      "expected_detection": true
    }

## 27.2 指标

Issue Detection：

- Precision
- Recall
- F1
- PR-AUC

Row-level Localization：

- Precision@K
- Recall@K
- Mean Average Precision

Severity Ranking：

- NDCG
- Spearman correlation

Repair：

- Exact repair accuracy
- Semantic repair accuracy
- False modification rate
- Data utility retention

Performance：

- Runtime
- Peak memory
- Rows processed per second
- LLM calls
- Token usage
- Cost per scan

## 27.3 消融实验

比较：

- Rules only
- Statistics only
- LLM only
- Rules + Statistics
- Rules + Statistics + LLM
- With and without business context
- With and without evidence fusion

必须验证：

> LLM 是否真正提高了规则建议、解释质量或修复排序，而不是仅增加成本。

## 27.4 与现有工具比较

在许可允许的前提下，选择：

- Pandera
- Great Expectations
- Soda Core
- Evidently

比较维度：

- 检测覆盖
- 配置成本
- 运行时间
- 内存
- 可解释性
- 修复支持
- LLM 增益
- 易用性

保持客观，不进行营销式比较。

------------------------------------------------------------------------

# 二十八、用户反馈学习

每个 Issue 允许用户标记：

- True issue
- False positive
- Expected exception
- Need more context
- Wrong severity
- Wrong repair

系统使用本地反馈调整：

- 字段阈值
- 规则启用状态
- Detector 权重
- Issue 排名
- 项目级例外

第一版不得直接训练复杂模型。

优先实现可解释的项目级偏好学习。

------------------------------------------------------------------------

# 二十九、插件系统

插件可以贡献：

- Detector
- Repair strategy
- Data connector
- Report section
- Semantic type
- Contract exporter
- LLM provider

插件接口必须版本化。

示例：

    from datasentry.plugins import DetectorPlugin

    class MyDetectorPlugin(DetectorPlugin):
        name = "my-domain-detector"
        api_version = "1"

        def register(self, registry):
            registry.add_detector(MyDetector())

------------------------------------------------------------------------

# 三十、开发阶段

## Phase 0：项目基础

交付：

- Monorepo
- Python Package
- FastAPI
- React UI
- SQLite
- DuckDB
- CI
- Lint
- Type Check
- Test
- Docker Compose
- 文档站

## Phase 1：无 AI 的核心 MVP

交付：

- CSV / Parquet / JSON / XLSX 导入
- Schema 推断
- 数据画像
- Missingness
- Type
- Range
- Duplicate
- Outlier
- Category consistency
- Issue model
- HTML / JSON 报告
- CLI
- Python SDK

这一阶段必须在没有 LLM 的情况下具备独立价值。

## Phase 2：AI Copilot

交付：

- Provider abstraction
- Structured output
- Semantic type inference
- Issue explanation
- Rule suggestion
- Natural-language-to-contract
- Repair proposal
- PII masking
- Cost control
- Prompt injection tests

## Phase 3：安全修复闭环

交付：

- Repair DSL
- Preview
- Approval
- Apply
- Validation
- Rollback
- Audit log
- Before/after report

## Phase 4：历史与漂移

交付：

- Dataset versions
- Reference/current comparison
- Schema drift
- Distribution drift
- Trend UI
- Alerts as local webhook

## Phase 5：生态集成

交付：

- GitHub Action
- MCP Server
- SARIF
- JUnit
- Contract exporter
- Plugin SDK

## Phase 6：研究和社区

交付：

- DQBench
- Baseline comparison
- Ablation study
- Technical report
- Reproducible experiments
- Public roadmap
- Good first issues

------------------------------------------------------------------------

# 三十一、首个可演示版本

创建一个电商订单演示数据集，人工注入：

- 2% 缺失 Email
- 1% 非法 Email
- 50 个重复订单 ID
- 30 个负订单金额
- USD / US Dollar / usd 混用
- 美国订单使用 EUR
- 退款金额高于订单金额
- 完成订单缺少 delivered_at
- 混合日期格式
- 20 个模糊重复客户
- 最近一个月 category 分布漂移
- 一个恶意 Prompt Injection 字段值

Demo 流程：

    Upload dataset
      ↓
    Run scan
      ↓
    View quality score
      ↓
    Inspect critical issue
      ↓
    Read statistical evidence
      ↓
    Ask AI for explanation
      ↓
    Generate a contract rule
      ↓
    Preview category normalization
      ↓
    Approve repair
      ↓
    Validate repaired dataset
      ↓
    Export clean Parquet and HTML report

------------------------------------------------------------------------

# 三十二、README 要求

README 首屏必须包含：

- 一句话定位
- Screenshot / GIF
- 核心特性
- 30 秒 Quick Start
- CLI 示例
- Python 示例
- AI 安全原则
- 与传统 Profiling 工具的区别
- Demo 数据集
- Roadmap
- Benchmark
- Contributing

示例开头：

    # DataSentry AI

    DataSentry AI is a local-first, evidence-driven data quality copilot that detects, explains, validates, and safely repairs data problems.

    Unlike black-box AI cleaners, DataSentry separates statistical evidence, semantic inference, business rules, and repair decisions.

------------------------------------------------------------------------

# 三十三、开源要求

许可证优先考虑：

- Apache License 2.0

必须包含：

- LICENSE
- CONTRIBUTING.md
- CODE_OF_CONDUCT.md
- SECURITY.md
- GOVERNANCE.md
- CHANGELOG.md
- ROADMAP.md
- Issue templates
- Pull request template
- Good first issue labels
- Architecture Decision Records

避免提交：

- API Key
- 用户数据
- 大型模型权重
- 未授权数据集
- 版权不明的样本

------------------------------------------------------------------------

# 三十四、代码质量要求

Python：

- Ruff
- mypy 或 pyright
- pytest
- coverage
- pre-commit
- Pydantic
- 完整类型标注
- Google 或 NumPy docstring

TypeScript：

- strict mode
- ESLint
- Prettier
- Vitest
- Playwright

所有公共 API 必须：

- 有类型
- 有 Docstring
- 有错误处理
- 有测试
- 有示例

不得生成：

- 巨型 God Class
- 到处传递无类型 dict
- 空泛的 TODO
- 假实现
- 无测试的核心逻辑
- 将业务逻辑写在 API Controller 中
- 将 LLM 调用散落在各模块
- 静默吞掉异常

------------------------------------------------------------------------

# 三十五、可观测性

记录：

- Scan duration
- Detector duration
- Rows scanned
- Sampling ratio
- Memory estimate
- Issues generated
- Issues merged
- LLM calls
- Input/output tokens
- Cache hit
- Repair duration
- Validation duration

使用结构化日志。

本地默认不发送遥测。

可选匿名遥测必须：

- Opt-in
- 明确说明字段
- 不包含数据内容
- 可随时关闭

------------------------------------------------------------------------

# 三十六、关键非功能需求

- 默认本地运行
- 无 API Key 可用
- 数据不可被静默上传
- 所有修改可预览
- 所有修改可回滚
- 对大数据避免全量物化
- 检测结果可复现
- AI 输出经过 Schema 验证
- 用户可以完全禁用 AI
- 核心模块具有高测试覆盖
- Linux、macOS 和 Windows 可运行
- Docker 一键启动
- Web UI 满足基本无障碍要求

------------------------------------------------------------------------

# 三十七、必须首先输出的设计材料

在编写代码前，先输出：

1.  产品需求文档
2.  用户故事
3.  功能优先级
4.  系统架构图
5.  数据流图
6.  领域模型
7.  数据库 Schema
8.  API 规范
9.  Detector 接口
10. Repair 接口
11. LLM 安全模型
12. Threat Model
13. Benchmark 方案
14. MVP 里程碑
15. 风险清单
16. 测试策略
17. Repository 目录
18. Architecture Decision Records

不要一次性生成整个项目。

------------------------------------------------------------------------

# 三十八、代码实施顺序

严格按以下顺序实施：

    Step 1: Domain models
    Step 2: Dataset loader abstraction
    Step 3: DuckDB and Polars execution layer
    Step 4: Profiling engine
    Step 5: Detector registry
    Step 6: Initial deterministic detectors
    Step 7: Evidence and Issue fusion
    Step 8: Scoring engine
    Step 9: Report generator
    Step 10: CLI
    Step 11: REST API
    Step 12: Web UI
    Step 13: Contract engine
    Step 14: Repair preview and rollback
    Step 15: LLM provider abstraction
    Step 16: AI explanation
    Step 17: AI rule generation
    Step 18: Drift engine
    Step 19: MCP server
    Step 20: Benchmark suite

每一步必须：

1.  输出设计决策
2.  实现最小完整功能
3.  编写测试
4.  运行测试
5.  修复失败
6.  更新文档
7.  给出变更摘要
8.  再进入下一步

------------------------------------------------------------------------

# 三十九、验收标准

项目达到 MVP 完成状态时，必须满足：

- 可以完全离线运行非 AI 功能
- 可以导入至少四种文件格式
- 可以处理至少 100 万行演示数据
- 至少实现 20 种数据质量检查
- 每个 Issue 有结构化 Evidence
- 每个 Issue 区分 Severity 和 Confidence
- 支持自定义 YAML Contract
- 支持自然语言生成候选规则
- AI 输出使用严格结构化 Schema
- AI 不接收未经授权的完整数据
- 支持修复预览
- 支持回滚
- 支持 HTML 和 JSON 报告
- 支持 CLI 和 Python SDK
- 支持 CI 非零退出码
- 核心代码有完整自动化测试
- 有公开 Benchmark
- 有可复现 Demo
- 有明确 License 和贡献指南

------------------------------------------------------------------------

# 四十、最终任务

现在请执行以下任务：

## 第一阶段

先不要生成全部代码。

请先完成：

1.  对上述需求进行一致性检查。
2.  找出过度设计、技术风险和隐私风险。
3.  将功能划分为 MVP、V1 和 Future。
4.  给出最终技术栈及选择理由。
5.  给出系统架构图，使用 Mermaid。
6.  给出领域模型图，使用 Mermaid。
7.  给出 Repository 完整目录。
8.  设计核心 Pydantic Models。
9.  设计 Detector、Evidence、Issue、Repair 的接口。
10. 设计 SQLite 元数据表。
11. 给出前 12 周开发计划。
12. 定义 MVP 的 Definition of Done。
13. 给出首批 30 个 GitHub Issues，其中至少 10 个标记为
    `good first issue`。
14. 给出技术报告或论文可研究的三个 Research Questions。
15. 给出 Benchmark 的实验设计和消融方案。

## 实施约束

- 不要求任何付费基础设施。
- 除用户自带 LLM Token 外，所有资源必须免费。
- 不得使用伪代码代替关键接口设计。
- 不得声称未实际实现的功能已经完成。
- 对存在不确定性的地方明确标记假设。
- 优先保证 MVP 可运行，而非功能数量。
- 所有 AI 能力必须有无 AI 降级路径。
- 所有修复必须默认只生成新数据版本。
- 所有危险操作必须要求明确审批。
- 所有输出应达到生产级开源项目的工程质量。
