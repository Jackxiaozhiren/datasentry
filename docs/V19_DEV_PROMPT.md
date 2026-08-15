# V19（v0.21.0）开发任务书：跨进程/多调度端一致性

> 基准：v0.20.0 已发布（V18 完成，CI/PyPI/Pages/release 全绿，工作区干净）
> ADR 止于 ADR-104（99 条记录）；MCP tools 20；测试 1155 passed；覆盖率 95.01%

## 目标

把「单进程假设」从存储与密钥路径中清除：元数据 SQLite 跨进程并发写不再
BUSY 炸裂、vault.key 原子写 + 会话冲突检测、调度端互斥有跨进程证明。
不做架构级改造（不加消息队列、不加网络协议、不改存储 schema）。

## 现状事实（探查结论，写进 ADR）

1. `MetadataStore`（core/store.py）：单连接 + `threading.RLock`，无显式
   `PRAGMA busy_timeout`（依赖 connect 默认 timeout=5.0 的隐式 busy 处理）；
   WAL 由 migrate 设置（schema.py:19）。跨进程并发写 → 5s 后
   "database is locked"。
2. `SchedulerStore`（scheduler/store.py）：每次操作新连接 + `BEGIN IMMEDIATE`
   + `PRAGMA busy_timeout = 5000` + 条件更新原子抢占——**已跨进程安全**，
   但全部测试都是单进程，无并发证明。
3. `PIIVault.rotate_key`（pii_vault.py:207-210）：`key_path.write_text()` 非
   原子——两进程并发 rotate 会交错/损坏 key 文件。
4. `save_pii_mapping`（store.py:661）：`INSERT OR REPLACE` 静默覆盖——
   session_id 确定性派生（内容 sha256 前 16 hex），**同 ID 不同内容会被
   静默覆盖丢数据**，无检测。密文每次 nonce 随机 → 不能比较密文判冲突。
5. `list_pii_mappings`：`ORDER BY created_at DESC`——同秒并发写入顺序不
   稳定（无 rowid 二级排序），会话顺序化缺失。

## 三个 Step（每步：实现 → 测试 → 四连门禁 → 文档 → 单独 commit → CI 全绿）

### Step 105（ADR-105）：元数据存储跨进程加固 + 会话顺序化 —— ✅ 完成

- `MetadataStore.__init__` 显式 `PRAGMA busy_timeout = 5000`（+ 注释：WAL
  已有，synchronous 默认 NORMAL 不设）；保持单连接+RLock（不加连接池，
  YAGNI）；文档串更新
- `list_pii_mappings` → `ORDER BY created_at DESC, rowid DESC`（同秒稳定
  序，跨进程一致；purge/list 同源受益）
- 新增 `tests/test_store_concurrency.py`（**subprocess 并发，不用
  multiprocessing——pytest spawn 递归陷阱**）：helper 脚本经
  `sys.executable -c` 起两个进程并发写 scan/issues/pii_mapping N 次 →
  无 "database is locked"、最终计数 = 写入数；busy_timeout 生效断言
- 测试 ~8 例

### Step 106（ADR-106）：vault 密钥原子写 + 会话冲突检测

- `rotate_key` 原子写：`vault.key.tmp.<pid/随机>`（0600）→ `os.replace()`
  → 删除 tmp；并发 rotate 后文件必为某次完整内容
- key 文件读取容错：文件为空/残留半写 → 明确错误（不静默用错 key）
- 会话冲突检测（vault 层，底层 INSERT OR REPLACE 不动）：
  `save_mapping` 先 `get_pii_mapping`，旧行存在时解密比较明文——
  明文相等 → 幂等跳过（省写）；解密失败（key 失配，轮换后重扫场景）
  → 降级允许重写（保持现状）；明文不等 → 抛 `PIIMappingConflictError`
  （内容碰撞/注入拦截，不覆盖）
- `llm status` 加 key 指纹字段（key_source=file：路径+mtime+内容
  sha256 前 8；env/explicit/dev：指纹前 8，不泄露密钥材料）——多进程
  「在用同一把 key」可感知；REST/UI 不加（YAGNI）
- 测试 ~10 例（原子性、并发 rotate 多进程、冲突拦截、幂等跳过、key
  失配降级、status 新字段）；`llm status` 既有断言更新（只加字段不断
  字段，向后兼容）

### Step 107（ADR-107）：多调度端互斥跨进程验证 + 感知

- 新增 `tests/test_scheduler_concurrency.py`（subprocess 并发）：
  两进程同时 claim_due_jobs/claim_job/finish_run → 同 job 只被抢一次、
  run 无丢失、状态机不坏；两进程并发扫描写 pii_mappings 不丢不 BUSY
  （调度端写会话可被主进程感知的并发证明）
- 无新 MCP 工具（20 不变）、无 REST/UI 变更；核心是**证明**与文档
- 测试 ~8 例

### Step 104 对齐的收尾（ADR-105/106/107 落档）

- CHANGELOG `[0.21.0]`（**加在文件顶部**）三小节；DEVELOPMENT.md V19
  段；计划书 3 Step ✅ + 坑位记录；index.html → 0.21.0 / 新测试数 /
  102 ADR / 20 tools
- bump 0.20.0→0.21.0：`pyproject.toml:3`、`src/datasentry/__init__.py:5`、
  `packages/core/src/datasentry_core/__init__.py:111`（**bump 单独
  commit**）；uv.lock（`uv lock --refresh` 优先）
- tag v0.21.0；push；等 PyPI/Pages/CI 全绿；`gh release create v0.21.0
  --title "v0.21.0 — 跨进程/多调度端一致性" --notes ...`

## 门禁（每步全绿才 commit）

1. `uv run --offline ruff check .`
2. `uv run --offline ruff format --check .`
3. `uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core`
4. `uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85`

## 验收

- 新增测试 ≥ 26 例；1155 → ≥1181 passed；覆盖率 ≥85%（预计持平 95%）
- 跨进程并发写无 "database is locked"（subprocess 实测证明）
- 同 session_id 不同内容写入被拦截（报错不覆盖）；同内容幂等跳过
- 并发 rotate 后 key 文件完整、全部映射可解密
- `llm status` 显示 key 指纹；pii 会话列表同秒顺序稳定
- v0.21.0 完整发布（bump/tag/PyPI/Pages/CI/release/uv.lock/工作区干净）

## 坑位（V19 预埋 + 历史复读）

1. **multiprocessing + pytest spawn 递归**：子进程会重 import 测试模块 →
   递归跑测试。一律用 `subprocess.run([sys.executable, "-c", helper])`，
   helper 脚本独立、无测试断言
2. SQLite 跨进程写测试里 `.wal/.shm` 残留——store close 后自动清理；
   子进程 helper 也要正常 close
3. 密文每次 nonce 随机 → 密文必不等 → 冲突检测必须解密比**明文**，
   不能比密文
4. key 失配（轮换后）解密失败 ≠ 冲突——降级允许重写，否则轮换后
   重扫必崩（回归红线）
5. `llm status` 新字段只能加不能断（历史断言可能严格匹配旧文本）
6. 原子写顺序：tmp 写 + fsync（可选）+ os.replace + chmod（replace
   前 chmod tmp 保证权限先行）
7. SchedulerStore 已跨进程安全——**不加锁不加连接池**，只加证明
8. CHANGELOG 新节在顶部；bump 单独 commit；显式 `git add <files>`
9. `--project`/`--format json` 全局参数在子命令前；CLI 校验错误
   EXIT_ERROR=3、配置缺失 EXIT_CONFIG=2
10. 测试要能过 CI（ubuntu runner 同样跑 subprocess 并发，目录用
    tmp_path 隔离，不碰用户配置）

## 最终报告格式（交付）

五段式中文汇报：完成概述 / 新增能力 / 测试与门禁数据 / 发布状态 /
遗留问题（V19 后候选：报告交互增强、调度端远程执行器细化等）。
