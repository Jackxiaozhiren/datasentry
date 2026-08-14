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

### Step 84（ADR-084）插件测试夹具

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

### Step 85 收尾 v0.14.0

- CHANGELOG [0.14.0] 三节 + ADR-082~084 落档 + DEVELOPMENT 路线图
  V12 完成段 + 计划书落地状态；升版 + tag + release + CI 观察

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

### Step 82（ADR-082）插件清单与安装管理 —— ⏳ 待开始

### Step 83（ADR-083）完整性校验与信任锚 —— ⏳ 待开始

### Step 84（ADR-084）插件测试夹具 —— ⏳ 待开始

### Step 85 收尾 v0.14.0 —— ⏳ 待开始

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