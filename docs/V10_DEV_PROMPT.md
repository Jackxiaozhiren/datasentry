# V10 开发计划书：i18n 深化 + MCP 配置透传 + 增量画像

- **目标版本**：v0.12.0（参照 v0.11.0 = `9b927be`）
- **范围**：四项用户选定方向全部落地——
  Step 74 CLI 全局 --lang、Step 75 报告正文翻译、Step 76 MCP scan
  配置透传、Step 77 增量画像（增量跳过 + 画像复用）
- **惯例**：通信中文；代码/提交英文、注释极简；Conventional Commits；
  每 Step 落 ADR + CHANGELOG + 计划书；门禁 ruff/mypy/pytest
  （--cov-fail-under=85）；commit 推 main；收尾升版 + tag + GitHub
  release + CI 观察

## 一、现状与方案要点（勘察结论）

### Step 74 CLI 全局 --lang（ADR-074）

- 现状：`--lang` 仅挂在 `report export` 局部（cli.py:1076-1082），
  全局参数区（cli.py:967-974）无 lang；scan/issues/score/trend 的
  text 输出英文硬编码；UI 走 api.py `?lang=` 已全支持
- **坑**：argparse 子命令局部 `--lang default="en"` 会覆盖全局值
  → 删除 report export 局部参数，统一消费全局 `args.lang`
- 方案：全局 `--lang {en,zh} default=en`；`_cmd_scan`/
  `_cmd_issues_*`/`_cmd_score`/`_cmd_trend_*` 的 text 输出走
  `t(lang, "cli.*")`（i18n.py 扩充 cli.* 键域）
- 边界：JSON/JUnit/SARIF 机器格式不译；ADR-069 原「CLI 其他命令
  text 输出不动」边界更新为本 ADR

### Step 75 报告正文翻译（ADR-075）

- 现状：i18n.py L10N en/zh 双表已存在（框架文案全译）；正文四源
  未译——①检测器 display_name/description（各 initial 文件 ClassVar
  硬编码，报告渲染直接透出 html.py:548/markdown.py:60）；②issue
  title（fusion.py FAMILY_TITLES + `_title()`）；③issue description
  （fusion.py `_description()` 固定格式 `[{detector_id} v{version}]
  {issue_type}: {count}`）；④修复建议（suggestions.py
  `_SUGGESTION_TABLE`）；⑤证据描述为含计数 f-string（各检测器
  detect() 内写死）
- 方案（渲染层翻译，数据层不动——机器契约键保持英文）：
  1. i18n.py 扩展键域：`families.*`（FAMILY_TITLES zh）、
     `detector_names.*`（40+ 检测器中文名，按 detector_id）、
     `suggestions.*`（label/rationale/risk zh）、`issue.*`（描述
     模板 zh）
  2. fusion.py 的 `_title`/`_description` 模板化（`families.xxx`
     键 + `{cols}`/`{count}` 占位），渲染层按 lang 映射——或
     fusion 直接接受 lang 参数（数据面改动，ADR 定）
  3. html.py/markdown.py/interactive.py 渲染 title/description/
     suggestion 时经 `t(lang, ...)` 映射
  4. **边界**：含计数的动态证据描述（evidence description f-string）
     不译（模板化 40+ 处成本高、收益低），记录于 ADR；JSON 契约
     键不译
- 验收：`--lang zh` 报告全框架 + issue 标题/描述 + 修复建议 + 检测
  器名中文；`en` 输出与 v0.11.0 逐字一致（快照对比）

### Step 76 MCP scan 配置透传（ADR-076）

- 现状：MCP `scan_file`（mcp_server.py:94-137）仅透传 seed；REST
  /scans（api.py:57-67, 102-108）已支持 detectors/seed/tags/sampling
- 方案：MCP scan_file inputSchema 增 `sampling_size`（integer）、
  `sampling_ratio`（number）、`sampling_method`（enum
  random/reservoir/none）、`sampling_seed`（integer）、`detectors`
  （array）、`tags`（array）→ 构造 SamplingConfig + ScanConfig
  透传；语义与 CLI/REST 一致（size/ratio 给定才开启抽样）
- 验收：MCP 调 scan_file 带 sampling_size → 与 CLI 同配置等价
  （SamplingInfo 落库一致）；无参数行为不变

### Step 77 增量画像（ADR-077）

- 现状：变更感知仅调度器有（Step 53/58：stats_fingerprint 两层
  复合 + job_runs.file_hash 精确相等跳过，scheduler/core.py:129-162,
  344-361）；client.scan_file 无任何增量逻辑（每次全扫 + 全画像）；
  本地文件 stats_fingerprint 抛 NotImplementedError
  （csv.py:314-319，ADR-058：本地用单层文件 SHA-256）
- 方案：`client.scan_file(..., incremental=False)` 新参数（默认
  关闭，行为与 v0.11.0 完全一致）：
  1. 开启时：open 后算当前指纹（本地=文件 SHA-256 content_fingerprint；
     远程=stats+content 两层，复用 scheduler 的 `_source_fingerprint`
     逻辑——抽到共享函数），与上次成功扫描指纹（元数据库
     scan_runs.fingerprint 或调度 job_runs.file_hash）比对
  2. 未变更 → 复用上次 scan_run 的画像与结果：跳过 run_scan 全量
     检测，落一条 skipped 语义的 ScanRun（沿用调度器 skipped 惯例），
     画像文件复用/拷贝上次的 profiles/{run_id}.json
  3. 已变更 → 正常全扫（现状路径）
- 验收：大文件二次扫描（未变更）耗时 ≈ 指纹读取量级（<1s 级，
  远小于全量 13s）；变更后正常全扫；默认路径零改动

## 二、落地清单（逐 step 更新：ADR + 测试 + CHANGELOG + 计划书）

### Step 74（ADR-074）CLI 全局 --lang
- cli.py：全局 `--lang`；删除 report export 局部；text 输出经
  `t(lang, "cli.*")`；i18n.py 增 cli.* 键域（scan 摘要/issues/score/
  trend 行）
- 测试：`--lang zh` 全局生效（scan/issues/score text 中文）、
  `report export --lang zh` 仍工作（全局接管）、默认 en 不变、
  cli.* 键缺失回退 en
- 影响：cli + i18n + tests + ADR + CHANGELOG + DEVELOPMENT + 计划书

### Step 75（ADR-075）报告正文翻译
- i18n.py：families.* / detector_names.* / suggestions.* / issue.*
  键域 zh 表
- fusion.py：_title/_description 模板化（lang 参数化或渲染层映射
  ——ADR 定：选渲染层映射，fusion 数据面不动）
- suggestions.py：suggest_repairs 接受 lang 或渲染层映射（ADR 定）
- html.py/markdown.py：title/description/suggestion/detector 名经
  t() 映射
- 测试：`--lang zh` 报告含中文 issue 标题/描述/建议；en 快照与
  v0.11.0 一致（逐字比对关键行）
- 影响：i18n + fusion + suggestions + html + markdown + tests +
  ADR + CHANGELOG + DEVELOPMENT + 计划书

### Step 76（ADR-076）MCP scan 配置透传
- mcp_server.py：scan_file schema 增 sampling_*/detectors/tags；
  构造 SamplingConfig + ScanConfig 透传
- 测试：MCP scan_file 带 sampling_size → scan_runs 里 SamplingInfo
  一致；无参数等价默认；非法 method 拒绝
- 影响：mcp_server + tests + ADR + CHANGELOG + DEVELOPMENT + 计划书

### Step 77（ADR-077）增量画像
- 共享指纹比较：scheduler 的 _source_fingerprint/复合哈希抽到
  client 可用函数（或 client 直接复用 scheduler 模块导入——ADR 定）
- client.scan_file(..., incremental=False)：指纹比对 → 未变更：
  复用上次结果 + 画像（skipped ScanRun 语义）；变更：全扫
- 测试：未变更跳过（run 数不增/画像复用）、变更恢复全扫、默认
  路径行为不变、远程源两层指纹（PG 集成测试可选跑）
- 影响：client + scheduler（函数抽取）+ tests + ADR + CHANGELOG +
  DEVELOPMENT + 计划书

## 三、验收标准（V10）

1. `datasentry --lang zh scan ...` 与 `report export`：CLI text 与
   报告（框架 + issue 标题/描述 + 建议 + 检测器名）中文；en 逐字
   不变（快照对比）
2. MCP scan_file 透传 sampling 配置生效（与 CLI 等价，SamplingInfo
   一致）；无参数行为不变
3. 增量画像：同文件二次扫描未变更 → 跳过全量（耗时 <1s 级）且
   画像复用；变更 → 正常全扫；`incremental` 默认关闭零影响
4. 既有全套测试零改动通过（默认路径行为不变）；基准保持 PASS
5. 门禁全绿 + 覆盖 ≥85%（维持 95% 附近）

## 四、落地状态

- Step 74（ADR-074）✅ 已落地（待 commit）：全局 --lang + cli.*
  i18n 键域 + report export 全局接管；测试 3 新增/1 更新，门禁绿