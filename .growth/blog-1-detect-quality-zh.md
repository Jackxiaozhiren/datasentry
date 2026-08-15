# 用 LLM 做数据质量检测，我把"检测"和"建议"分开了

做数据质量检查这些年，我发现现有工具分成两派：一派是规则引擎，不先写一份配置文件什么都不告诉你；另一派是托管平台，数据不搬到它的云上就不给你看。但最常见的场景偏偏是：仓库里一个 CSV、笔记本上一个 DuckDB 文件、公司内网一台 Postgres——数据是脏的，你想马上知道脏在哪，又不想为这件事搭一套基础设施。

所以我做了一个本地优先的数据质量工具，叫 DataSentry。这篇文章讲它怎么检测问题，以及我为什么坚持"统计负责检测、AI 只负责建议"这条分界线。

## 检测是统计，AI 只是配件

我做这个工具时反复回退到一条设计原则：**检测必须是确定性的，AI 只能辅助**。缺失值检测器要的是计数和比例，不需要大模型；离群点检测要的是模型和阈值，不是"感觉"。如果你问一个 AI agent"这份文件里有没有重复订单"，它凭对文件的"印象"回答你，那不是数据质量检查，是带问号的幻觉。

DataSentry 内置 39 个检测器：缺失、日期解析、编码、跨字段规则、跨表外键、精确+模糊重复、Isolation Forest / LOF 离群模型。每个问题都带证据链：样本、占比、置信度。不是一行日志，是证据。

在故意污染的 200 行 CSV 上，全流程是这样的：

```bash
$ pip install datasentry-ai
$ datasentry scan orders.csv
```

```json
{
  "status": "completed",
  "row_count": 200,
  "issues_count": {"low": 6, "medium": 6, "high": 1},
  "total_issues": 13,
  "detector_runs": 39,
  "quality_score": 95.3
}
```

那个 high 级别的问题，详情长这样：

```bash
$ datasentry issues show <issue_id>
```

```json
{
  "issue_type": "datetime_anomaly",
  "title": "Datetime anomaly in order_date",
  "description": "[invalid_date v1.0.0] invalid_date: 9 | [impossible_date v1.0.0] impossible_date: 5",
  "severity": "high",
  "confidence": 0.995,
  "affected_count": 9,
  "affected_ratio": 0.045,
  "false_positive_risk": "low",
  "evidence": [
    {"detector_id": "invalid_date", "description": "9 values fail ISO date pattern"},
    {"detector_id": "impossible_date", "description": "5 values are impossible dates"}
  ]
}
```

9 个值不符合 ISO 日期格式、5 个是不存在的日期（比如 `2026-13-01`），置信度 99.5%。阈值你可以争辩，计数你没法争辩——具体行就在那里。

## 修复闭环：propose → preview → apply → rollback

检测只是一半。第二条设计原则是：**任何修改数据的动作，必须有人批准**。修复流程是四个明确步骤：

```bash
$ datasentry repair propose <issue_id> --file orders.csv
```

```json
{
  "proposed": true,
  "proposal_id": "prop_069d4f189890",
  "operation": "set_null",
  "target_columns": ["order_date"],
  "estimated_rows_changed": 14,
  "rationale": "set invalid values to NULL (missing semantics)"
}
```

提案明确告诉你改什么、改多少行。`repair preview` 会在真实数据上重跑规则，让你看前后对比再决定。`repair apply` 会写一份带指纹的副本和回滚产物：

```json
{
  "applied": true,
  "run_id": "rep_21a5dffb5d6f",
  "fingerprint_before": "90ba77cb...",
  "fingerprint_after": "0706d18f...",
  "rollback_artifact": ".datasentry/repairs/rep_21a5dffb5d6f.before.csv"
}
```

`repair rollback` 从那份产物恢复。自动化修复的爆炸半径被限制在一个文件、一次运行、随时可回滚。

## LLM 真正该干的活

有了确定性核心当事实源，LLM 拿到三个它真正擅长的任务：

1. **自然语言 → 规则候选。** `datasentry rules propose "order_date should always be a valid date" --file orders.csv` 返回规则候选，并在真实数据上做预演模拟——在保存任何东西之前。LLM 起草，预演验证，你批准。
2. **把问题翻译成业务语言。** 证据链是结构化的，但一句"过去 120 天有 9 行日期无法解析"这样的概括，是真正有价值的翻译层。
3. **让 agent 直接跑检查。** DataSentry 带一个 MCP server，20 个工具。Claude 或其他支持 MCP 的 agent 可以直接调用 scan、issues、repair——人工审批的门槛一样存在。agent 可以*检查*数据，但想*改*数据必须你点头。

这里有两个安全属性值得说：第一，**LLM 永远看不到原始 PII**——任何 prompt 之前，值都会被替换进加密保险库（密钥可轮换）；第二，**不配 LLM 也完全能用**——默认 provider 就是 `null`，检测、评分、修复、调度全部正常工作。LLM 是升级选项，不是依赖：

```bash
$ datasentry llm status
{"provider": "null", "configured": false, "recent_calls": 0}
```

想用也可以接本地 Ollama，数据不出机器。

## 提前告诉你这些坑

- **离群检测器不是万能的。** Isolation Forest / LOF 是为表格数据调的，喂奇怪分布的数据会误报。这正是证据链要暴露置信度和样本的原因——允许检测器犯错，但要错得明显、错得可见。
- **重复检测取决于你对"重复"的定义。** 精确重复容易，模糊重复的阈值要花时间调。
- **"置信度"是检测器对模式的信心，不是对你业务的判断。** 99.5% 只说明模式识别很确定，至于这个模式对你重不重要——那是你的判断，工具只是把证据摆到能判断的程度。

## 我反复回到的那条分界线

统计负责检测，LLM 负责翻译和建议，人负责批准。这不是妥协，是让每一层在其角色内可信的设计：确定性核心因为确定而可审计，LLM 因为表达力强而有用，因为永远不握着笔而安全。

DataSentry 是 Apache-2.0，约 1200 个测试、覆盖率 95%。我个人觉得 MCP server 是最容易被低估的一块——agent 在你的数据上跑真实检查、拿真实证据，而不是凭上下文猜。

- 仓库：https://github.com/Jackxiaozhiren/datasentry
- 文档：https://jackxiaozhiren.github.io/datasentry/

如果你试了，我最想听两件事：你第一个想用哪个检测器（插件 API 加自己的检测器很简单），以及人工审批这个门槛在哪个场景让你觉得是摩擦、而不是安全。
