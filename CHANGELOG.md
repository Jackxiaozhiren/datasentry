# Changelog

本项目的所有显著变更按时间倒序列出。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-08-05

MVP 里程碑：九项硬性验收 M1–M9 全达成（36+ 检测器、融合评分、
质量门禁、修复闭环、报告引擎、Docker、CI 十阶段、M9 Demo）。

### 新增（Step 25–31，V1 第一阶段收尾）

- **Step 31**：检测器插件 API v1 —— `load_plugin_detectors`
  目录动态加载（Detector 协议发现）、`<workspace>/plugins/`
  自动加载、CLI `datasentry detectors`（ADR-031）
- **Step 30**：危险规则批准安全阀门 —— `rules approve --file`
  重跑预运行复核，dangerous 规则需 `--force` 才激活（ADR-030）
- **Step 29**：Ollama 原生 `/api/generate` 接入 + 超时重试统一
  （`_post_with_retry` 共享辅助）（ADR-029）
- **Step 28**：自然语言 → 规则候选 —— propose 脱敏画像 → LLM
  严格 JSON → 预运行报告 → 候选落库待批 → approve 转正；
  `llm_cache` prompt 哈希复用（ADR-028）
- **Step 27**：LLM Provider 抽象（core 零网络接口）+
  确定性 PII 脱敏管线 + 审计闭环 `llm status/invocations`
  （ADR-027）
- **Step 26**：工程收尾 —— Apache-2.0 LICENSE、
  CONTRIBUTING.md、Makefile、Dockerfile + compose、
  CI 十阶段（ADR-026）
- **Step 25**：M9 Demo 单脚本（5000 行 5.4s，预算内可复现）
  （ADR-025）

### 新增（Step 1–24，MVP 主体）

- 36+ 检测器：缺失模式族（4 核心）、日期时间族（6 核心）、
  表示变体与编码（spelling/fullwidth/mojibake/invalid_numeric）、
  跨字段规则（受限安全表达式求值，ADR-015）等
- 证据驱动融合 + 六维质量评分（27 章归一化，ADR-013）
- 质量门禁 `scan --fail-on`（22 章场景 C，ADR-014）
- 修复闭环：propose → preview → apply → rollback（15 章，
  ADR-020）
- 报告引擎：JSON / Markdown / HTML 三形态（26 章）
- 多格式连接器：CSV / Parquet / JSONL / XLSX（7.1，ADR-019）
- 契约引擎（跨表/多文件约束，C-04）
- REST API（FastAPI 单工作区门面，ADR-023）+ Web UI
  （服务端渲染五核心页，ADR-024）
- 性能基准：1e6 行双档判定（20.4，ADR-021/022）

### 工程

- Python >= 3.12，DuckDB 执行引擎（ADR-005/007）
- 门禁：ruff + mypy --strict + pytest 覆盖率 >= 85%
  （当前 408 例，96.35%）
- Docker 一键启动（`datasentry-server` 入口）
