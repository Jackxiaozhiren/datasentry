# GitHub Discussions 置顶帖（Welcome + FAQ）

> 发布方式：Discussions → 新建 → 选 Announcements 分区 → 发布后钉在置顶。
> 启用 Discussions 时默认会建 5 个分区，建议删除多余的，保留：
> 💬 使用答疑（General / Q&A 合并）· 🧠 检测器创意（Ideas）· 🚀 Roadmap（Announcements）

---

**标题：欢迎使用 DataSentry 👋 从这里开始**

DataSentry 是一个本地优先的数据质量平台：以统计证据为基础、以 AI 为辅助、以人工审批为保障。

**快速开始**

```bash
pip install datasentry-ai
datasentry scan orders.csv
datasentry issues list --severity high
datasentry score latest
```

**常用链接**

- 文档：https://jackxiaozhiren.github.io/datasentry/
- 报告 bug：请附上最小复现数据（或脱敏 CSV）和你跑的命令
- 提检测器创意：请到「🧠 检测器创意」分区，说明数据形态与期望行为

**FAQ**

- **Q：不配置 LLM 能用吗？** A：能。检测、评分、修复、调度全部是确定性实现，LLM 只是可选增强（可接本地 Ollama，数据不出机器）。
- **Q：AI 能直接改我的数据吗？** A：不能。任何修复都要走 propose → preview → apply → rollback 的人工审批闭环；MCP 给 agent 的工具同样受此约束。
- **Q：支持哪些数据源？** A：CSV/Parquet/JSONL/XLSX/DuckDB/SQLite/PostgreSQL/MySQL/s3:// gs:// az://。
- **Q：和 Great Expectations / Soda 什么关系？** A：见博客《Great Expectations vs DataSentry》——定位是"检测框架"而非"断言框架"，且 contract 可导出为 GE 格式衔接使用。

**Roadmap 预告**（见「🚀 Roadmap」分区）

- 异步触发协议（cancel/状态查询异步化）
- 报告交互增强
- webhook 事件去重

欢迎任何使用反馈、检测器创意与贡献。规则：AI 建议的东西在人类批准之前都只是提案。
