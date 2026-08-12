# V5 开发计划书：多数据源扩展与凭据管理（Step 56~59）

- 目标版本：v0.7.0（完成后统一升版，按仓库既有惯例）
- 前置基线：V4（Step 55, ADR-055）已发布 v0.6.0，CI 全绿
- 文档约定：ADR-056~059 追加到 `docs/00-设计裁决记录-ADR.md` 末尾；
  `CHANGELOG.md` 顶部新增 `## [0.7.0]`；架构/坑位进 `docs/DEVELOPMENT.md`
- 通信语言：中文（代码/提交信息英文，注释极简——仓库风格零注释或极简）

## 一、目标与边界

### 目标

1. **MySQL 数据源**（V1 路线图遗留项，`docs/03-设计材料-MVP-V1-划分.md` 第 157 行
   「MySQL / DuckDB 文件连接器」）：`scan mysql://...` 直达远程 MySQL 库表，
   与 PG（Step 55）同等能力（全检测器/评分/报告/门禁/修复/漂移 + 调度变更感知）。
2. **云存储文件源**：`scan s3://.../file.csv|parquet|jsonl`（含 gs://、az://），
   本地文件源的整套体验搬到对象存储。
3. **远程源增量指纹**（ADR-055 明确的边界：「远程源 MVP 不做增量采样优化」——
   本版本正面演进）：全表哈希/全量下载的代价降下来，大表/大文件调度跳过判定
   轻量化。
4. **凭据管理完善**（ADR-054 决策 6 遗留：「等凭据管理成熟再接」——PG 已有
   env 引用 MVP，本版本通用化）：统一凭据加载/存储/审计。

### 边界（不做）

- 不做 MySQL 之外的新 SQL 数据库（SQL Server/Oracle 留白）
- 不做云存储写入（只读扫描）
- 不做凭据托管服务（Vault 等）集成，只做本地 secrets 文件 + 环境变量体系
- 不做多表批量扫描（glob 多文件留白，见 Step 57 决策点）
- 不引入新重依赖（沿用 DuckDB 扩展 + 标准库，同 AD-054 约束）

## 二、分步方案（4 个 step，串行推进，完成一个再开下一个）

### Step 56：MySQL 数据源连接器（ADR-056）

- **方案（开工前实测已通过）**：`INSTALL mysql; LOAD mysql` macOS 可用（无系统库
  依赖）；`ATTACH 'host=...;user=...;password=...;database=...' AS my (TYPE
  mysql, READ_ONLY)` 注册只读视图。`MySQLDataHandle(FileDataHandle)` 模式完全
  复刻 PostgresDataHandle（_ensure_view 时机连库 + _RedactingExecutor 凭据净化）。
- **识别与接入**：client.scan_file 识别 `mysql://` 前缀（DSN 进 options，同
  PG）；凭据环境变量引用 `DATASENTRY_MYSQL_DSN`（connection_ref 语义同 PG）。
- **类型归一化**：mysql 扩展返回的 TINYINT UNSIGNED/DECIMAL/TIMESTAMP 等物理
  类型规范化进 `_normalize_physical_type`（PG 已建，扩展映射表即可）。
- **集成测试**：docker mysql:8 容器（`datasentry-mysql-test`，端口 3307→3306，
  root/testpass，db testdb，沿 PG 测试容器惯例）；CI test job 加
  `services.mysql`（mysql:8，healthcheck `mysqladmin ping`）。若 CI Linux 上
  mysql 扩展缺系统库则在 CI 预装 libmysqlclient（macOS 本地实测无需）。
- **交付物**：connectors/mysql.py + registry 注册 + SDK/CLI/MCP 接入 +
  单元测试（FakeExecutor，28 例量级）+ 集成测试 6 例量级 + ADR-056。

### Step 57：云存储文件源（ADR-057）

- **方案**：`LOAD httpfs`（实测可用）——`read_blob('s3://...')` 或 DuckDB
  ATTACH 远程 parquet/CSV。文件源语义对齐：schema/read_sample/sql_aggregate/
  count_rows/fingerprint/warnings 全部复用 FileDataHandle 共享实现，路径由
  本地 Path 换成远程 URI（requires_path 已抽象，新增 remote 分支）。
- **凭据**：优先标准环境变量（AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/
  AWS_ENDPOINT_URL 等 httpfs 原生读取）；MinIO 本地测试用
  `SET s3_endpoint`。不做配置文件解析（凭据管理统一走 Step 59）。
- **变更感知**：云文件无本地字节 → 文件哈希语义需要远程实现：优先
  `read_blob` 流式下载计算？成本高。决策点：ETag/对象元数据（Content-MD5）
  作为第一层快速失效，必要时才全量下载。**此决策开工前实测后向用户确认**。
- **集成测试**：docker MinIO（`datasentry-minio-test`，沿 PG 容器惯例），
  aws cli 或 boto3？——零新依赖约束：用 DuckDB httpfs 自身读写（PUT/POST
  到 S3 兼容端点建桶传文件，MinIO 是 S3 兼容，httpfs 可写）。CI 加 minio
  service（如遇 CI 复杂度超预算，退化为本地验证 + 单元测试）。
- **交付物**：connectors/remote_file.py（或 file_based 内扩展）+ URI 识别
  （s3:///gs:///az:// 前缀）+ 类型归一化复用 + 测试 + ADR-057。

### Step 58：远程源增量指纹（ADR-058）

- **现状**：PG 内容指纹 = 单查询全表 md5(string_agg...)（Step 55）；云文件
  若走全量下载哈希成本更高。大表调度 skipped 判定成为瓶颈。
- **方案（分层快速失效，开工前向用户确认选型）**：
  1. 统计层：row_count + schema_hash 快速比对（行数/结构变 → 立即判定变更，
     零内容读取）；
  2. 内容层：仅当统计层一致时才算内容指纹（PG 现有全表哈希 / 云文件
     ETag 或采样哈希）；
  3. （可选，看需求）采样层：`LIMIT N ORDER BY` 确定性采样哈希，配置化
     sample_rows（默认 0 = 禁用，保守等价现状）。
- **调度器联动**：`_source_fingerprint` 抽象不动，handle.fingerprint 实现
  升级即生效；`last_successful_hash` 字段天然兼容（TEXT）。
- **验证**：bench 数据集（百万行）前后耗时对比，写进 DEVELOPMENT.md。
- **交付物**：fingerprint 实现分层重构 + 配置项 + 测试（含大表耗时断言）
  + ADR-058。

### Step 59：凭据管理完善（ADR-059）

- **现状**：DSN 走 CLI 参数 / connection_ref 环境变量引用，只存内存态，
  错误净化（_RedactingExecutor）。缺：多数据源时代凭据分散在 shell/env，
  无统一加载与审计。
- **方案（零新依赖）**：
  - `datasentry secrets` 子命令族：`set/get/list/rm`，管理
    `~/.config/datasentry/secrets.env`（chmod 600 强制校验，set 时
    交互式无回显输入，不进 shell history）；
  - CLI/API/MCP/调度器统一解析链：CLI 参数 > 进程环境变量 >
    secrets.env 自动加载；connection_ref 语义扩展（引用名先在 env 找，
    找不到再查 secrets 文件，仍查不到 → DataSourceNotFoundError）；
  - 审计：`datasentry secrets list` 只显示键名不显示值；全仓库 grep
    确认测试/文档零真实凭据（占位符惯例）。
- **交付物**：secrets 子命令 + 统一解析链 + 文档（README 凭据一节）+
  测试 + ADR-059。

## 三、工程铁律（每步必须遵守）

1. **每步流程**（8 步法）：设计决策 → 实现 → 测试 → 修复 → 文档（ADR +
   DEVELOPMENT.md + CHANGELOG）→ 变更摘要。每步收尾执行：
   `uv run ruff check .` → `uv run ruff format --check .` →
   `uv run mypy packages/core/src/datasentry_core src/datasentry` →
   `make test`（`uv run pytest --cov=datasentry_core --cov-fail-under=85
   --cov-report=term`）→ 涉及则 `make check-all`（demo + bench）→
   提交 → `gh run list --workflow=ci.yml --limit 1` 轮询确认 CI 全绿。
2. **提交规范**：Conventional Commits，一次提交一个逻辑变更；提交信息
   中文描述（如 `feat: MySQL 数据源连接器 (V5, Step 56, ADR-056)`）；
   提交前 `git status` 确认只暂存意图内文件；**绝不提交密钥**（DSN/密码/
   测试凭据只出现在 CI service env 或本地 env，测试代码用占位值）。
3. **文档铁律**：每个功能 step 在 `docs/00-设计裁决记录-ADR.md` **末尾追加**
   ADR（V5 从 **ADR-056** 起）；`CHANGELOG.md` 顶部新增 `## [0.7.0]` 段
   （按时间倒序）；架构/坑位进 `docs/DEVELOPMENT.md`（沿用既有小节风格）。
4. **版本节奏**：V5 完成后统一升 **v0.7.0**（pyproject.toml +
   src/datasentry/__init__.py + packages/core 两处 + uv.lock 同步），
   CHANGELOG 补发布说明，按仓库既有惯例提交
   `chore: 发布 v0.7.0（...）` + 打 tag `v0.7.0` + 推送（tag 触发 PyPI
   发布 + Pages 更新；发布前 `make check` + `uv build` 双包构建验证）。
5. **自主推进**：用户已授权「我都听你的」，可直接推进；但遇到以下情况
   **先问**：引入新的重依赖、涉及外部账号/密钥/费用、需要改已有 CLI
   行为（破坏性变更）、范围明显超出第二节边界、方案实测不可行需要切
   备选（如 mysql 扩展在 CI 缺系统库、MinIO 集成测试超预算）。
6. **坑位备忘（历史踩坑，避免重犯）**：
   - 本机无系统 `python`，一律 `uv run python`；临时脚本用 `mktemp -d`
     + `uv venv` 隔离
   - 注册表断言会变：`tests/test_detector_registry.py` 与
     `tests/test_connectors.py` 的默认注册表顺序/不支持类型断言需同步
     更新（Step 55 教训：POSTGRESQL 转正后 unsupported 用例换过断言；
     MySQL 转正后需要再换——建议 unsupported 用例改用未支持的
     SQLSERVER 枚举或 URI 无凭据用例）
   - 凭据红线沿用 Step 55 全套：_RedactingExecutor 净化（DSN→`***`、
     密码→`***`）、evidence/报告/日志零泄漏、测试有泄漏断言
   - mysql/httpfs 扩展 `INSTALL` 幂等；扩展安装失败给可操作错误
     （缺网络/版本不匹配），不要裸抛 DuckDB 底层异常
   - run_id 随机后缀、sqlite3 row_factory、SchedulerStore 独立 db 调
     core_schema.migrate——既有坑位照抄
   - 测试用假时钟；断言用 trigger 返回的 run_id 查 store，不依赖排序
   - mypy strict：croniter `# type: ignore[import-untyped]` + cast；
     PEP 695 泛型 `_json_safe[T]`；参数校验 `math.isfinite`
   - 新增数据源必须过一遍「调度器变更感知」测试矩阵（未变跳过/变更
     重扫/不可达回退），MySQL 有真实容器、云文件至少单元级
   - 云文件源没有本地 path：FileDataHandle 的 `_path` 相关守卫（full 档
     `_path is None → ConnectorError`）与 requires_path 语义要在
     remote 分支重新审视，别让 `content_fingerprint` 的 `assert
     self._path is not None` 在云文件上裸崩

## 四、启动清单（开工前必须完成）

按顺序执行后向用户汇报计划（含：MySQL/云存储实测结论、增量指纹选型、
CI service 配置方案、版本与 ADR 编号规划），**得到确认再写代码**：

1. `git log --oneline -5`、`git status`、`git diff HEAD~1 --stat` 确认
   基线干净（V4 发布后应只有 V5 新增）
2. 读 `docs/00-设计裁决记录-ADR.md` 末尾 ADR-055（V5 直接前件）、
   `docs/DEVELOPMENT.md` Step 55 段、`README.md` 多数据源宣称、
   `CHANGELOG.md` 顶部
3. 读 `packages/core/src/datasentry_core/connectors/`（postgres.py 是
   Step 56 的核心参考；file_based.py 的 requires_path/_path 守卫是
   Step 57 的改造面）、`src/datasentry/client.py`（scan_file 前缀推断）、
   `src/datasentry/scheduler/core.py`（Step 58 的调用面）
4. **实测**（关键决策，结果决定选型）：
   - mysql 扩展 `ATTACH TYPE mysql READ_ONLY` 连本地 docker mysql:8
     （建容器 + 灌数 + 读回 + 断连失败净化），确认 macOS 行为；
     CI Linux 的 mysql 扩展系统库依赖情况（可在计划中写明预装预案）
   - httpfs `read_blob('s3://...')` 对 MinIO（建容器）读写验证，
     确认凭据 env 读取与 endpoint 配置方式；远程 ETag 获取可行性
   - 增量指纹：对 PG 测试容器灌百万行量级数据，实测当前全表哈希
     耗时，作为 Step 58 的基准数字
5. 检查 `uv sync` 状态与 `make check` 本地全绿（新增 integration 测试
   必须自动跳过，不破坏本地门禁）
6. 向用户确认：MySQL 容器/CI service 配置、MinIO 是否进 CI、增量指纹
   选型（统计层+内容层 / 是否要采样层）、secrets 文件路径与权限语义、
   完成后是否发布 v0.7.0

## 五、交付节奏与输出要求

- V5 = 4 个 step（Step 56~59），每个 step 内含 1~3 个提交单元（实现 /
  测试+CI / 文档+版本），完成一个再开下一个；每步结束汇报：改了哪些
  文件、测试增量、覆盖率、CI 结果、ADR 编号
- 每步必须全绿才算完成（本地 `make check` + 远端 CI 全绿；本地无容器时
  integration 自动跳过不视为缺失）
- 全部完成后汇总 V5 收尾：版本 0.7.0、CHANGELOG、tag 推送触发发布、
  README 功能矩阵更新（多数据源列表补 MySQL/云存储、凭据一节）
