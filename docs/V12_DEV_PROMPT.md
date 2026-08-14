# V12 开发计划书（v0.14.0）

## 一、目标

V11（v0.13.0）已发布。V12 主题：**插件生态治理**——补齐
"插件 API v1（Step 31/50）可加载"到"可治理"的缺口：
清单化、可安装、完整性校验、可自测。路线图 V2 四项
（云侧协作 / 报告交互 / PII 还原 / 插件治理）中，PII 还原已
在 Step 48-49 落地（pii_vault + 审计 + rotate），插件治理为
下一顺位；云侧协作范围过大（需服务端），留待后续。

1. **插件清单与安装管理**（Step 82，ADR-082）：workspace `plugins/`
   支持 manifest（plugin.yaml：name/version/author/license/description），
   `plugin install <path>` 安装、`plugin uninstall <name>` 卸载、
   `plugin list` 展示清单字段；无 manifest 的旧平铺 `.py` 插件照常
   加载（向后兼容）。
2. **完整性校验与信任锚**（Step 83，ADR-083）：安装时对插件文件
   计算 SHA-256 锁定（.datasentry/plugin_locks.json）；每次加载校验
   哈希——被篡改插件拒绝加载（可 `--allow-modified` 显式放行）；
   锁文件即信任锚。安全模型延续 ADR-031/050：插件=本机可信代码，
   不做沙箱；校验解决"篡改无感知"，不解决"恶意本机代码"。
3. **插件测试夹具**（Step 84，ADR-084）：`plugin test <name>`：插件
   可声明 fixtures（样例数据 + 期望检测器/issue 阈值），复用扫描
   管线跑断言（fail-fast 输出），插件作者无需手写测试脚手架。
4. **收尾 Step 85**：升版 v0.14.0（根 pyproject + 双
   `__init__.__version__`，core 包恒 0.7.0 不动；tag v0.14.0 触发
   PyPI + Pages）+ GitHub release + DEVELOPMENT 路线图 + 计划书
   落地状态。

## 二、Step 分解

### Step 82（ADR-082）插件清单与安装管理

- **现状**：`load_plugin_detectors(registry, [workspace/plugins])`
  平铺加载 `*.py`（跳过 `_`/`.` 前缀，fail-fast）；entry points 版
  优雅降级；`plugin list` 仅展示检测器元数据 + source
  （builtin/dir/entrypoint）+ 失败项；无安装/卸载/清单。
- **方案**：
  - manifest 协议：`plugins/<name>/plugin.yaml`
    （name/version/author/license/description + detectors 声明可选）
  - 加载兼容：`<workspace>/plugins/*.py`（旧平铺）与
    `<workspace>/plugins/<name>/*.py`（清单目录）都支持；有清单的
    目录按清单名组织，无清单平铺文件沿用现有逻辑（零迁移）
  - `plugin install <path|dir>`：复制到 workspace `plugins/<name>/`
    （保留 manifest 或生成占位），冲突名报错；`plugin uninstall
    <name>` 删目录；`plugin list` 增清单字段（version/author/license）
- **影响**：packages/core/src/datasentry_core/plugins.py（目录遍历
  逻辑扩展）+ client.py list_plugins + cli.py plugin 子命令
  （install/uninstall/list）+ tests

### Step 83（ADR-083）完整性校验与信任锚

- **现状**：目录插件加载前无任何内容校验；加载即执行代码。
- **方案**：
  - `plugin install` 时计算每个文件 SHA-256，写入
    `.datasentry/plugin_locks.json`（plugin → files/hashes/
    installed_at/version）
  - 加载时校验：哈希不匹配 → 拒绝加载并报错（PluginLoadError，
    提示 `plugin reinstall` 或 `--allow-modified`）；锁文件缺失的
    旧插件（安装于本功能之前）→ 首次加载时提示并自动建锁
    （锁定当前内容）
  - 校验在 import 前完成（import 即执行，必须先验后载）
- **边界**：不引入签名公钥体系（本机信任模型不变）；不覆盖
  entry points 插件（由包管理器/发行版负责完整性）；校验失败
  绝不影响内置检测器与其他插件（fail-fast 仅限该插件）
- **影响**：plugins.py 加载前置校验 + 锁文件读写（新模块
  plugin_locks.py 或并入 plugins.py）+ cli 提示 + tests

### Step 84（ADR-083）插件测试夹具

- **现状**：插件作者无自测设施；只能手写临时脚本构造
  DetectionContext 跑检测器。
- **方案**：
  - manifest 可声明 `fixtures:`：data.csv 路径 + 期望断言列表
    （detector_id 出现/缺席、min_issues 阈值、quality_dimension）
  - `plugin test <name>`：隔离注册表（仅内置 + 该插件）跑扫描
    管线（CSV 连接器 + ScanRunner），逐断言校验，fail-fast 输出
    通过/失败明细（复用报告 JSON 面）
  - 无 fixtures 声明 → 提示跳过（exit 0）
- **影响**：新 CLI 子命令 + 断言求值（复用注册表/扫描管线）+ tests
- **落地**：✅ 完成（ADR-083；实现 + 15 测试 + CLI 冒烟 + 门禁
  95.01%；详见本文档 Step 84 落地记录）

### Step 85 收尾 v0.14.0

- CHANGELOG [0.14.0] 三节 + ADR-082~084 落档 + DEVELOPMENT 路线图
  V12 完成段 + 计划书落地状态；升版 + tag + release + CI 观察
- **落地**：✅ 完成（v0.14.0 2026-08-14；PyPI/Pages/CI 全绿；
  release v0.14.0 已建；详见 Step 85 落地记录）

## 三、验收标准（V12）

1. `plugin install` 装好清单插件 → `plugin list` 显示清单字段 →
   `plugin uninstall` 移除；旧平铺 `.py` 插件零迁移照常加载；
   目录冲突/非法 manifest 有明确报错
2. 锁文件校验：安装后篡改插件文件 → 拒绝加载并提示；`plugin
   reinstall` 或 `--allow-modified` 可恢复；旧插件首次加载自动建锁
3. `plugin test`：对含预期 issue 的样例数据通过；篡改后断言失败
   明确输出；无 fixtures 跳过
4. 既有测试零改动通过；门禁全绿（ruff + format + mypy --strict
   + pytest 覆盖 ≥85%，维持 95% 附近）
5. 提交纪律：Conventional Commits、中文注释极简、ADR/CHANGELOG/
   计划书同步

## 四、落地状态

### Step 82（ADR-082）插件清单与安装管理 —— ✅ 完成

- 提交：``90ddac2``（docs 回填 `待回填`）
- 落地：plugins.py 增 PluginManifest/read_plugin_manifests（plugin.yaml
  协议，name/version 必填、name 限 `[a-zA-Z0-9_-]`）+ 目录加载兼容
  （平铺 .py 与清单目录并存，无清单子目录忽略）；client.py 增
  install_plugin/uninstall_plugin/plugins_dir，list_plugins 增
  manifests 视图；cli.py 增 plugin install/uninstall；tests/
  test_plugin_manifest.py 17 例全绿；CLI 冒烟（install→list→uninstall）
  通过；门禁全绿（覆盖 95.02%）

### Step 83（ADR-083）完整性校验与信任锚 —— ✅ 完成

- 提交：``c7af998``（docs 回填 `待回填`）
- 落地：新模块 plugin_locks.py（PluginLocks/PluginLock/build_lock/
  integrity_report/compute_sha256，锁文件 .datasentry/
  plugin_locks.json）；plugins.py 公开 plugin_units + 新增
  load_plugin_detectors_excluding（v1 签名不变）；client 初始化
  接线（先验后载：无锁自动建锁 / 篡改跳过+记 errors）+ reaccept_
  plugin + list_plugins integrity 字段；cli.py 增 plugin reaccept；
  修复 pycache 误判 bug（排除衍生文件）；tests/test_plugin_locks.py
  20 例全绿；CLI 冒烟（install→tamper→reject→reaccept→ok）通过；
  门禁全绿（覆盖 95.00%）

### Step 84（ADR-083）插件测试夹具 —— ✅ 完成

- 交付：plugin.yaml `fixtures` 段（FixtureSpec/FixtureExpectation，
  非法即抛）；`plugin test <name>`（隔离注册表=内置+被测插件、
  标准连接器管线、ScanRunner 全流程、过滤命中检测器的 Issue、
  三态：全过=0 / 失败=EXIT_GATE_FAILED / 无夹具=跳过视为通过、
  不落库）；tests/test_plugin_fixtures.py 15 例全绿；门禁全绿
  （ruff/mypy --strict/pytest 95.01%）；CLI 冒烟 pass/fail/skip
  三路径验证（exit 0/1/0）
- 坑位：IssueCandidate 必填字段多（detector_version/dataset_id/
  raw_score/confidence/estimated_false_positive_risk/
  suggested_severity）——插件作者易踩；`sql_aggregate` 返回
  FrameBatch（`.table.column()` 取值）；DataFrame 不存在时连接器
  抛 DataSourceNotFoundError（ConnectorError 子类），夹具层需
  捕获 ConnectorError；Issue 的 quality_dimensions 取自检测器
  声明维度，而非 IssueCandidate 传入值

### Step 85 收尾 v0.14.0 —— ✅ 完成

- 交付：升版 v0.14.0（根 pyproject + 双 `__init__.__version__`，
  core pyproject 恒 0.7.0）；CHANGELOG [0.14.0] - 2026-08-14；
  DEVELOPMENT V12 完成段；tag v0.14.0（PyPI 发布 ✓ + Pages ✓ +
  CI ✓）；GitHub release v0.14.0（2026-08-14）；本计划书落地
  状态回填
- V12 收官：插件清单（Step 82）→ 完整性校验（Step 83）→ 测试
  夹具（Step 84）→ 发布（Step 85）；插件 API v1 载荷零改动、
  旧平铺布局零迁移；最终 CI 全绿（覆盖 95.01%，mypy --strict 通过）

## 五、勘察备忘

- 插件加载：packages/core/src/datasentry_core/plugins.py
  （`load_plugin_detectors` 目录版 fail-fast / `discover_entrypoint_detectors`
  entry points 优雅降级 / DETECTOR_ENTRY_POINT_GROUP="datasentry.detectors"）
- 挂载点：client.py `_registry_with_plugins`（workspace/plugins）+
  `_source_map`（builtin/dir/entrypoint）+ `list_plugins`；
  cli.py `plugin list`（Step 50）；registry.register 冲突 ValueError
- 既有裁决：ADR-031（目录版，fail-fast）/ ADR-050（entry points，
  优雅降级）；安全模型=本机可信代码无沙箱（11.10 表达式求值只
  约束规则）
- 存储：无插件相关表（文件系统方案：锁文件 JSON 于 .datasentry/，
  与 profiles/ 惯例一致）