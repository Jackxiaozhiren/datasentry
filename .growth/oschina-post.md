# OSCHINA 帖草稿

标题：**DataSentry：本地优先的 AI 数据质量 Copilot 开源了（Apache-2.0）**

---

## 项目简介

DataSentry 是一个以统计证据为基础、以 AI 为辅助、以人工审批为保障的本地优先数据质量平台。扫描一次数据，输出六维质量评分，每个问题都带证据链（样本、占比、置信度）。

- GitHub：https://github.com/Jackxiaozhiren/datasentry
- PyPI：`datasentry-ai`（当前 v0.25.0）
- 文档：https://jackxiaozhiren.github.io/datasentry/
- 许可证：Apache-2.0

## 核心能力

- **39 个证据驱动检测器**：缺失、日期、编码、跨字段规则、跨表外键、精确+模糊重复、Isolation Forest/LOF 离群等；检测核心为确定性统计，不依赖任何 AI 模型
- **六维质量评分**：完整性/有效性/唯一性/一致性/完整性/时效性，权重可解释
- **修复闭环人工审批**：propose → preview（规则重跑前后对比）→ apply（指纹副本+回滚产物）→ rollback；AI 只建议，人决定
- **漂移引擎**：历史扫描对比（schema、行数、评分、问题分布）
- **CI 质量门禁**：`scan --fail-on` 按严重级/评分阻断；导出 JSON/Markdown/HTML/JUnit/SARIF
- **调度与分布式**：cron 任务队列、webhook、变更感知跳过；`datasentry worker` 多 worker 池（轮询路由、故障转移、并行派发）
- **MCP server 集成（20 个工具）**：Claude 等 AI 助手可直接调用扫描/查问题/提议修复，人工审批门槛一致
- **LLM 安全设计**：自然语言 → 规则候选 + 预演模拟；prompt 前 PII 进加密保险库（密钥轮换）；每次调用审计；可接本地 Ollama，数据不出机器
- **数据源**：CSV/Parquet/JSONL/XLSX/DuckDB/SQLite/PostgreSQL/MySQL/s3:// gs:// az://

## 快速开始

```bash
pip install datasentry-ai
datasentry scan orders.csv
datasentry issues list --severity high
datasentry score latest   # v0.25.0 新增：无需手输 run_id
```

## 工程质量

1229 个测试、94.94% 覆盖率、mypy --strict、110+ 条架构决策记录（ADR）、CI 全绿、持续发布。

## 最新版本 v0.25.0

增长期实测摩擦驱动的可用性版本（ADR-117）：

- `datasentry score latest`：解析为最近一次扫描，无需手输 run_id
- score JSON 信封新增 `scan_run_id` 字段
- `datasentry issues list --limit N`：问题列表截断

---

备注：OSCHINA 发布以客观项目介绍为主（该社区对纯广告敏感），评论区可引导技术讨论。
