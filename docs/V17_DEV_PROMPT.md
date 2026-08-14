# V17 任务书：PII 加密 vault 管理面补全（v0.19.0）

你是 DataSentry 项目的自主开发代理。按以下任务书完成 V17 开发，每完成一个 Step 就 commit + push + 观察 CI，全部完成后发布 v0.19.0 并汇报。

## 一、项目背景

DataSentry 是本地优先的数据质量 AI copilot（Python ≥3.12，uv 管理，monorepo：`src/datasentry`（应用）+ `packages/core/src/datasentry_core`（核心包，pyproject 恒 0.7.0 勿动））。已迭代至 v0.18.0：扫描/评分/修复/漂移/契约/报告、LLM 辅助（PII 掩码 + 加密 vault）、cron 调度（SQLite 任务队列）、分布式 worker 池（容错路由 + 并行派发）、插件治理、CLI/REST/MCP/Web UI 四面。

**V2-A 已交付的 PII vault 底层（勿重写，只复用）**：

- `src/datasentry/pii_vault.py`：`PIIVault(store)`——`key_source`（"env"/"dev"/"file"）、`key_configured`、`save_mapping(mapping) -> session_id`、`load_mapping(session_id)`、`restore_text(text, session_id)`、`restore_value(value, session_id)`、`rotate_key(new_key=None)`；`VaultKeyMissingError`（key 未配置时抛）；`format_mapping_summary(mapping, preview=2)`
- core 存储 `datasentry_core.storage.store.MetadataStore`：`save_pii_mapping` / `list_pii_mappings(limit=100)` / `delete_pii_mapping(session_id)` / `get_all_pii_mappings()`（轮换用）
- CLI 已有 `datasentry llm restore [session_id] [--text] [--delete]` + `datasentry llm rotate-key`（`src/datasentry/cli.py` ~861 行 `_cmd_llm_restore` / `_cmd_llm_rotate_key`，含 dev-key 警告、VaultKeyMissingError → EXIT_CONFIG）
- 授权语义：**CLI 是本地用户命令，`restore <session>` 即授权查看明文；报告与 UI 默认打码不受影响**——这是既有决策，V17 延续

**V17 缺口**：vault 只有 CLI 面。REST API 无 PII 端点、MCP 无 PII 工具、Web UI 报告只打码无还原入口。V17 对齐 V13"CLI/REST/MCP 三面同语义"模式补全，并给 Web UI 加一个还原查看入口。

## 二、当前状态与基线

- HEAD == origin/main；工作区必须干净；版本 0.18.0（根 `pyproject.toml` line 3 + `src/datasentry/__init__.py` + `packages/core/src/datasentry_core/__init__.py` 三处）
- `docs/00-设计裁决记录-ADR.md`：ADR 止于 ADR-098（93 条记录），V17 从 **ADR-099** 起
- `CHANGELOG.md` 顶部为 `## [0.18.0]`
- 1087 个测试 / 74 个测试文件；覆盖率 95.01%（门禁 85%）
- MCP 工具 15 个；REST 端点见 `src/datasentry/api.py` `_ENDPOINTS` frozenset（~735 行，**新增端点必须同步加入**）
- MCP 工具集被 `tests/test_mcp_*.py` 中 `test_tools_list_shape` 之类**严格相等断言**锁定——**新增工具必须同步更新该断言**
- 项目顶层允许创建 `docs/V17_DEV_PROMPT.md` 作为计划书（仿照 V13-V16 格式），但**不要把本项目文件结构记入新计划书正文**——直接工作

## 三、V17 目标（Step 99-101，ADR-099/100/101）

给 PII vault 补全管理面：

1. **REST API**：`GET /pii/sessions`（列表，含 key_source 提示）、`GET /pii/sessions/{session_id}`（映射摘要）、`POST /pii/sessions/{session_id}/restore`（body: text → 还原明文）、`DELETE /pii/sessions/{session_id}`、`POST /pii/rotate-key`（轮换）；key 未配置时相关端点返回 503 + detail（仿 `/rpc/execute` disabled 语义）；session 不存在 404
2. **MCP**：`pii_sessions`（列表）、`pii_restore`（session_id + text → 还原）、`pii_delete_session`（删除）——3 个新工具，续接既有 15 个；描述注明"显式授权语义"
3. **Web UI**：`/ui/pii` 页面（或报告页内）——列出会话 + 输入还原文本按钮；**默认打码语义不变**，还原结果仅内存展示不落盘。实现放 `src/datasentry/ui.py`（现有 server-rendered 模式），复用既有模板/样式惯例
4. API 缺 key 时返回 503 与 CLI 的 EXIT_CONFIG 语义对齐（VaultKeyMissingError）

**边界（不做）**：不重写 vault 加密算法/存储 schema；不做 key 的 UI 管理（key 仍由 env/文件提供）；不做 PII 检测器新规则；不改 core 包版本。

## 四、Step 分解

### Step 99（ADR-099）：REST API PII 端点 —— ✅ 完成

- 在 `src/datasentry/api.py` 加端点（仿既有 job 端点风格：`from datasentry.pii_vault import PIIVault, VaultKeyMissingError, format_mapping_summary`；`vault = PIIVault(app.state.client._store)`——注意 client 是 `sdk.DataSentry`，store 是其 `_store` 属性，参考 `cli.py:866` 的用法）
- 401/404/422/503 语义清晰；`POST /pii/rotate-key` 返回 `{"key_version": ...}`（rotate_key 返回值含此信息，核对 `pii_vault.py:168` 返回结构）
- `_ENDPOINTS` 同步加 5 个新端点
- 测试（`tests/test_api_pii.py`）：未配置 key → 503（monkeypatch.delenv 确保无 env key + 无 key 文件，注意 `_key_file()` 位置是 `~/.config/datasentry/encryption.key` 之类——查 `pii_vault.py:57`，测试要隔离 HOME 或用 monkeypatch）；配置 key（`DATASENTRY_ENCRYPTION_KEY` env）→ 建映射（直接用 store.save_pii_mapping + vault.save_mapping）→ 列表/摘要/还原/删除/轮换全链路；session 不存在 404；还原成功但 key 轮换后 404/解密失败的语义（rotate 后旧 session 失效——验证 `rotate_key` 行为决定断言）

### Step 100（ADR-100）：MCP 3 个 PII 工具 —— ✅ 完成

- 在 `src/datasentry/mcp_server.py` 用 `@self._tool(...)` 注册 `pii_sessions` / `pii_restore` / `pii_delete_session`（properties 用 camelCase 与既有工具一致，看既有工具命名，如 `session_id` / `text`）
- 工具描述写"explicit authorization semantics: calling this tool is the authorization to view plaintext"
- 同步更新测试里 tools 列表严格断言（找到 `test_tools_list_shape` 所在文件 `tests/test_mcp_*.py` 并加 3 个名字）
- 测试：新工具存在且 schema 合法；`pii_restore` 端到端（save_mapping → restore 返回明文）；无 key → 工具返回错误消息不崩

### Step 101（ADR-101）：Web UI `/ui/pii` + 发布 v0.19.0

- `src/datasentry/ui.py` 加 `/ui/pii` 路由（HTML，复用既有样式惯例——nav/table/button；打码显示 session_id + 时间 + key_version；还原表单 post 到同页并展示还原结果一次）；**还原结果只存内存响应体**
- 冒烟：`uvicorn` 起服务 curl 页面 200
- 收尾：CHANGELOG `[0.19.0]` 节（3 个 Step 小节）、DEVELOPMENT.md V17 段（文档头注释风格，含 ADR-099/100/101）、计划书三 Step 打 ✅ + 坑位记录
- 版本三处 0.18.0 → 0.19.0；commit；tag v0.19.0；push tag；等 PyPI 0.19.0 + Pages + CI 全绿；`gh release create v0.19.0 --title "v0.19.0 — PII 加密 vault 管理面" --notes "..."`；`uv lock --refresh` 后提交 uv.lock（**若网络不通，直接编辑 uv.lock 中 `name = "datasentry-ai"` 包的 `version = "0.18.0"` → "0.19.0" 一行即可**）

## 五、门禁（每个 Step 必须全绿才提交）

```bash
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core
uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85
```

（网络抖动用 `--offline`；预期 1087+ 测试、覆盖率 ≥95%）

## 六、提交纪律与发布节奏

- Conventional Commits：`feat(api): ...` / `feat(mcp): ...` / `feat(ui): ...` / `docs: ...` / `chore: ...`，body 写 Step 号与 ADR 号
- **push 前必查 `git status`；禁止 `git add -A`**（历史三次误收 `p/` 调试目录，已加 `.gitignore` 仍要警惕）；用显式 `git add <files>`
- 每 Step：实现 → 门禁 → ADR + CHANGELOG + 计划书回填 → 单 commit → push → `gh run watch`/`gh run list` 观察 CI 全绿再进下一步
- CI ~5 分钟；不要在 CI 红时继续下一步

## 七、历史坑位清单（务必遵守）

1. **测试稳定性三坑**：
   - 调度 worker 每 1s tick 会真执行到期 job——测试 job 用远未来 cron `"0 0 1 1 *"`；store 直建 job 用 `next_run_at=now + timedelta(days=365)`；cron 5 字段 分/时/日/月/周（day=0 非法 422）
   - 测试不依赖 worker 自动执行
   - HTTPServer/uvicorn 测试：读掉 Content-Length body + finally `server.server_close()`
2. **httpx.ASGITransport 是 async-only**——同步 Client 会报错；真 HTTP 测试用 uvicorn 后台线程模式（参考 `tests/test_remote_executor.py` 的 `_serve`：`Config(app, port=0)` → 轮询 `server.started` → `server.servers[0].sockets[0].getsockname()[1]` → finally `should_exit=True` + `thread.join(timeout=10)`）
3. **run 的 summary 是 JSON 字符串字段**（不是平铺字段）
4. `/jobs` 创建返回 **201**、trigger 返回 **202**（不是 200）
5. `create_job` 收 `ScheduledJob` 对象（非关键字参数）
6. MCP 工具列表严格相等断言——加工具必须同步改断言
7. `_ENDPOINTS` frozenset——加 REST 端点必须同步加
8. ruff F841 未用变量会被拦
9. 版本 bump 三处 + uv.lock datasentry-ai 包版本行；**bump 应单独 commit**（历史教训：bump 被 docs commit 吞并）
10. PyPI 索引有延迟（发布后数分钟才可见）；Pages 部署工作流 `pages.yml` 上传 `docs/`（含 index.html）——push main 自动重部署
11. `datasentry --project <dir> --format json` 是全局参数放子命令前；CLI 输出 envelope（`{ok, command, data, warnings}`）JSON 流
12. key 相关测试隔离：`PIIVault._key_file()` 指向用户级路径（核对 `pii_vault.py:57` 具体路径，测试用 monkeypatch 隔离）

## V17 执行坑位记录（Step 99-101）

- 缺 key 判定用 `key_configured`（key_source=dev 时 False）：API 503 /
  MCP 错误消息 / UI 提示，均 gate 在 dev 兜底键之前——dev key 只
  留给 CLI（有显式告警）
- DELETE /pii/sessions/{id} 不 gate key（删除密文行无需密钥，与
  CLI `--delete` 一致）；测试断言无 key 时 delete 仍 404 而非 503
- rotate-key 后旧 env key 解密失败 → 503（VaultKeyMissingError），
  不是 404；去掉 env 后 vault 改读轮换写下的 key 文件 → 还原恢复
- API 测试 key 隔离：monkeypatch.setenv/delenv +
  monkeypatch.setattr("datasentry.pii_vault._key_file", ...) 双管齐下
- CHANGELOG 实际是顺序追加结构（最新节在文件尾部，非严格倒序）——
  [0.19.0] 追加在 [0.18.0] 之后

## 八、验收

1. 门禁四连全绿，新增测试 ≥15 例（API ~8 + MCP ~5 + UI/其他）
2. `llm restore` CLI 与 `POST /pii/sessions/{id}/restore` 还原结果一致（同 vault 同映射）
3. 无 key 时：CLI EXIT_CONFIG、API 503、MCP 错误消息、UI 显示"未配置密钥"提示——四面语义对齐
4. 报告/UI 默认打码不受影响（既有 `mask_text_pii` 路径零改动）
5. v0.19.0 发布完成：PyPI ✓ / Pages ✓ / CI ✓ / GitHub release ✓ / uv.lock 同步 ✓ / 工作区干净

## 九、完成后汇报格式

按"完成概述 / 新增能力（Step 99-101 各一段）/ 测试与门禁数据 / 发布状态 / 遗留问题"五段式汇报，中文，简明。
