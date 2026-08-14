# V11 开发计划书（v0.13.0）

## 一、目标

V10（v0.12.0）已发布。V11 三件套收官全球化与扫描管线：

1. **证据级动态描述翻译**（Step 78，ADR-078）：detector 运行时生成的
   证据 description（29 处 `f-string`，8 个文件）模板化 + zh 镜像，
   报告证据节全量中文化；en 输出逐字不变（含历史数据回退）。
2. **调度任务 ScanConfig 配置透传**（Step 79，ADR-079）：JobCommand
   增 config 字段，调度扫描可带 sampling/detectors/tags（与 CLI/MCP
   同源），补齐"调度任务不能配扫描"缺口。
3. **增量画像列级 diff**（Step 80，ADR-080）：文件变更但 schema 列签名
   未变 → 全扫不变，画像 sidecar 按列增量复用（只重算新增/变更列）；
   列签名变 → 全量画像。绝不引入漏检（数据行变更仍全量）。

收尾：Step 81 升版 v0.13.0（根 pyproject + 双 `__init__.__version__`，
core 包恒 0.7.0 不动；tag v0.13.0 触发 PyPI + Pages）+ GitHub release
+ DEVELOPMENT 路线图 + 计划书落地状态。

## 二、Step 分解

### Step 78（ADR-078）证据描述模板化 + zh 镜像

- **勘察结论**：29 处 `description=f"..."` 分布：
  datetime 6 / categorical 4 / numeric 5 / textual 8 / textual_variants 2 /
  missing 2 / formula 1 / uniqueness 1；均在 core 包 detectors 内
  （i18n 同在 core，无跨包问题）。
- **方案**：新增 `reporting/evidence_desc.py`（core 内）：
  - `ev(key, base=None, **params)` 生成 en 渲染文本（EvText：str 子类，
    携带 key/params/base）；en 模板 = 现 f-string 同占位文本
    （`.format(**params)`），en 输出逐字不变（同参同结果）
  - `make_evidence`（detectors/common.py）识别 EvText 自动把
    `base + _text_key/_params` 并入 evidence.data（原 data 语义零
    变化，修复引擎/渲染层照旧读 data 字段）
  - `translate_evidence_desc(lang, data, description)`：zh 渲染时按
    data 内 `_text_key`/`_params` 取 zh 模板同参渲染；历史数据
    （无 meta）/模板缺失/参数缺失回退原文（诚实降级）
- **定案（终版）**：落库 description 保持纯 en 文本（结构化 meta 走
  evidence.data，JSON 契约面零改动，历史数据与 en 快照零影响）；
  zh 渲染面 = 交互 detail 面板证据节（issue_rows 增 evidence 字段 +
  JS 渲染）；JSON/Markdown/JUnit/SARIF 数据面不译（机器契约，延续
  ADR-075）；早前"key|json 前缀落库"讨论已弃（改走 data meta）
- **影响**：detectors 8 文件 29 处改造（ev 调用点）+ i18n
  `evidence_desc.*` 29 组 en/zh 键 + common.make_evidence 合并逻辑 +
  交互渲染接线 + tests（en 逐字快照 + zh 断言 + 历史回退 + 降级）

### Step 79（ADR-079）调度任务 ScanConfig 透传

- **现状**：JobCommand 仅 project/path/dataset_id/table_name/
  export_report；executor.execute → client.scan_file 无 config；
  CLI/MCP 已支持 sampling/detectors/tags，调度任务缺口。
- **方案**：JobCommand 增 `config: ScanConfig | None = None`
  （pydantic 序列化 JSON，store 落库自动）；executor.execute 传
  `config=command.config`；任务创建侧（CLI/API）增
  `--sampling-*/--detectors/--tags` 透传（与 _cmd_scan 同源构造）；
  指纹语义不变（文件级，config 不参与跳过判定——文件未变仍跳过，
  ADR 记录边界）。
- **影响**：scheduler/models.py + core.py + 任务创建命令 + tests

### Step 80（ADR-080）增量画像列级 diff

- **方案**：`_save_profile` 增列级复用：读取上次 completed 扫描的
  画像 sidecar（profiles/{prev_id}.json），比较当前 schema 列签名
  （fingerprint.column_signature）与上次——列集合未变 → 全量画像
  （数据变，画像必变，不优化）；列集合变（增/删/改）→ 未变列的
  ColumnProfile 从上次 sidecar 复制，仅重算新增/变更列 + 全局行数；
  无上次 sidecar → 全量。
- **边界**：仅 schema 变更场景收益（宽表加列）；数据变更全量
  画像（无漏检）；列改名视为删+增。
- **影响**：client.py `_save_profile` + profiler 列级接口 + tests

### Step 81 收尾 v0.13.0

- CHANGELOG [0.13.0] 三节 + ADR-078~080 落档 + DEVELOPMENT 路线图
  + 计划书落地状态；升版 + tag + release + CI 观察

## 三、验收标准（V11）

1. `--lang zh` 报告证据节全中文（含计数/阈值参数正确填充）；en 输出
   与 v0.12.0 逐字一致（快照对比）；历史 scan_run 证据 zh 回退 en
   原文不报错
2. 调度任务带 ScanConfig（sampling/detectors/tags）落库并生效，与
   CLI 等价（SamplingInfo 一致）；无配置任务行为不变
3. 列级 diff：加列场景仅重算新列画像（旧列画像字段复用）；数据
   变更场景全量画像（无漏检）；无 sidecar 场景全量
4. 既有全套测试零改动通过；门禁全绿（ruff + format + mypy --strict
   + pytest 覆盖 ≥85%，维持 95% 附近）
5. 提交纪律：Conventional Commits、中文注释极简、ADR/CHANGELOG/
   计划书同步

## 四、落地状态

### Step 78（ADR-078）证据描述模板化 + zh 镜像 —— ✅ 已完成

- `reporting/evidence_desc.py`：EvText（str 子类，携带 key/params/base）+
  `ev()` + `translate_evidence_desc()`；i18n `evidence_desc.*` 29 组
  en/zh 键；common.py make_evidence 合并 base+meta；29 处调用点
  8 文件全量改造（en 逐字不变，JSON 契约面零改动）
- 接线：issue_rows 增 evidence 字段（zh 渲染）+ 交互 detail 面板
  证据节（JS）+ `detail.evidence` 键；JSON/Markdown/JUnit/SARIF
  保持 en（机器契约）
- 修复：ev base 数据丢失根因（EvText 携带 base，make_evidence 合并
  `{**base, **data, **meta}`）；i18n.py 增 E501 per-file 豁免
  （双语表数据行）
- tests/test_evidence_desc.py 13 例（en 逐字 / zh 同参 / 历史回退 /
  降级 / make_evidence 合并 / 交互行集成）；门禁全绿（覆盖 95.03%）
- 提交：`97d8dfe`

### Step 79（ADR-079）调度任务 ScanConfig 透传 —— ⏳ 待开始

### Step 80（ADR-080）增量画像列级 diff —— ⏳ 待开始

### Step 81 收尾 v0.13.0 —— ⏳ 待开始

## 五、勘察备忘

- 证据 f-string：`grep -rn "description=f" packages/core/src/datasentry_core/detectors/`
- JobCommand：src/datasentry/scheduler/models.py:40；executor 挂载点
  core.py:227；store 落库 model_dump_json 自动
- 画像：client._save_profile（client.py:122）+ profiler.profile
  （engine/profiler.py:70，单条聚合下推）；sidecar profiles/{run_id}.json；
  fingerprint.column_signature（csv.py:268）