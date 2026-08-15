# V18 任务书：PII vault 密钥与会话生命周期管理收尾（v0.20.0）

你是 DataSentry 项目的自主开发代理。按以下任务书完成 V18 开发，每完成一个 Step 就 commit + push + 观察 CI，全部完成后发布 v0.20.0 并汇报。

## 一、项目背景

DataSentry 是本地优先的数据质量 AI copilot（Python ≥3.12，uv 管理，monorepo：`src/datasentry`（应用）+ `packages/core/src/datasentry_core`（核心包，pyproject 恒 0.7.0 勿动））。已迭代至 v0.19.0：V17 补全了 PII vault 管理面（REST 五端点、MCP 三工具、Web UI /ui/pii 页、CLI/REST/MCP/UI 四面缺 key 语义对齐）。

**V17 已交付（勿重写，只复用）**：

- `src/datasentry/pii_vault.py`：`PIIVault(store)`——`key_source`（"env"/"dev"/"file"）、`key_configured`、`save_mapping`、`load_mapping`、`restore_text`、`rotate_key(new_key=None)`（新密钥重加密全部映射 + 写本地 key 文件，返回 {"new_key", "rotated", "key_file"}）；`VaultKeyMissingError`；`format_mapping_summary`
- CLI：`llm restore [session_id] [--text] [--delete] [--limit]` + `llm rotate-key [--new-key]`
- REST：`GET /pii/sessions`、`GET /pii/sessions/{id}`、`POST /pii/sessions/{id}/restore`、`DELETE /pii/sessions/{id}`（204，无需密钥）、`POST /pii/rotate-key`（不返回密钥材料）
- MCP：`pii_sessions` / `pii_restore` / `pii_delete_session`（18 工具，tools/list 严格相等断言锁定）
- UI：`/ui/pii` 会话列表 + 还原表单（结果仅内存展示）；缺 key 显示提示不提供表单

**V18 缺口**：

1. 密钥轮换/设置只有 CLI 面完整（`rotate-key --new-key`）；REST `POST /pii/rotate-key` 不支持指定新密钥、MCP 无轮换工具、UI 无密钥管理卡片
2. 会话只增不删过期（无清理能力）——到期会话只能手工逐条 `--delete`

V18 收尾这两个生命周期管理缺口：密钥轮换/设置完整透传 + 会话按龄清理（purge）四面（CLI/REST/MCP/UI）。

## 二、当前状态与基线

- HEAD == origin/main（`3bd3ec3`，CHANGELOG 归一 commit）；工作区必须干净；版本 0.19.0（根 `pyproject.toml` line 3 + `src/datasentry/__init__.py` + `packages/core/src/datasentry_core/__init__.py` 三处）
- `docs/00-设计裁决记录-ADR.md`：ADR 止于 ADR-101（96 条记录），V18 从 **ADR-102** 起
- `CHANGELOG.md` 已归一为 Keep-a-Changelog 顺序：**`## [0.19.0]` 在顶部**，V18 新节加在文件顶部
- 1131 个测试；覆盖率 95.01%（门禁 85%）
- MCP 工具 18 个；REST `_ENDPOINTS` frozenset（`src/datasentry/api.py` ~881 行，**新增端点必须同步加入**）
- MCP 工具集被 `tests/test_mcp_server.py` 中 `test_tools_list_shape` 严格相等断言锁定——**新增工具必须同步更新该断言**（V18 共 +2：pii_rotate_key、pii_purge_sessions，最终 20 个）
- 项目顶层允许创建 `docs/V18_DEV_PROMPT.md` 作为计划书（仿照 V13-V17 格式），但**不要把本项目文件结构记入新计划书正文**——直接工作

## 三、V18 目标（Step 102-104，ADR-102/103/104）

1. **密钥轮换/设置完整透传**：REST `POST /pii/rotate-key` 支持可选 body `{"new_key": str|null}`（无 body 时自动生成，向后兼容）；MCP 新增 `pii_rotate_key` 工具（可选参数 `newKey`）；UI `/ui/pii` 加密钥卡片（key_source 状态 + 轮换按钮 + 设置密钥表单）——四面与 CLI `rotate-key --new-key` 语义一致
2. **会话按龄清理（purge）**：`llm restore --purge --older-than <days>`（CLI）、`POST /pii/sessions/purge`（REST，body `{"older_than_days": int}`，非法 422）、`pii_purge_sessions`（MCP，`olderThanDays`）、UI 清理表单——删除 created_at 早于 N 天的会话；**无需密钥**（删除密文行与 delete 同语义）
3. 缺 key 语义延续 V17：rotate/set-key 需要密钥（缺 key → REST 503 / MCP 错误消息 / UI 不显示密钥卡片）；purge 不需要密钥（四面对齐）

**边界（不做）**：不重写 vault 加密算法/存储 schema（核心包加方法必须走既有存储 API，如 list/delete_pii_mapping 组合，不改 SQL schema）；不做 key 的 CLI 交互式输入（仍由 env/文件/显式参数提供）；不做 PII 检测器新规则；不改 core 包版本。

## 四、Step 分解

### Step 102（ADR-102）：密钥轮换/设置完整透传（REST body + MCP 工具 + UI 密钥卡片）—— ✅ 完成

- `src/datasentry/pii_vault.py` 不修改（rotate_key 已支持 new_key 参数）
- REST：`POST /pii/rotate-key` 加可选请求体 `PiiRotateRequest`（`new_key: str | None = None`）；无 body / body 空对象行为与现在完全一致（自动生成）；返回仍不含密钥材料；缺 key 503；`_ENDPOINTS` 无需变化（端点不变）
- MCP：`@self._tool` 注册 `pii_rotate_key`（properties: `newKey` 可选 string）→ `vault.rotate_key(new_key=newKey)` → `{"ok": True, "keyVersion": "file", "rotated": N, "keyFile": path}`；缺 key → `ok:false` 错误消息；描述注明"轮换后旧密钥失效、新密钥写入本地 key 文件"
- UI：`render_pii` 加密钥卡片（key_configured 时）——显示 key_source、轮换按钮（POST `/ui/pii/rotate`）、设置密钥表单（new_key 输入，POST `/ui/pii/key`）；不配置时现有"未配置"提示下加一行 hint（"运行 'datasentry llm rotate-key' 创建密钥"）；结果 alert-ok 展示 rotated 数 / key_file
- i18n：`ui.pii_key_card`、`ui.pii_rotate_button`、`ui.pii_set_key_form`、`ui.pii_new_key_label`、`ui.pii_rotate_result`、`ui.pii_key_hint` 等（en+zh）
- 测试（`tests/test_api_pii.py` + `tests/test_mcp_pii.py` + `tests/test_ui_pii.py` 追加）：REST 无 body 轮换（行为不变）、带 new_key 轮换（轮换后旧 env key 解密 503、新 key 可还原）、缺 key 503；MCP 工具存在 + schema、e2e 轮换、newKey 指定、缺 key 错误消息；UI 密钥卡片显示、轮换按钮 POST、设置表单 POST（含 XSS 转义）

### Step 103（ADR-103）：会话按龄清理 purge 四面—— ✅ 完成

- `src/datasentry/pii_vault.py` 加 `purge_sessions(older_than_days: int) -> int`：遍历 `list_pii_mappings(limit=10**6)`，`created_at < utcnow - timedelta(days=N)` 则 `delete_pii_mapping`，计数返回；**不触碰存储 schema**
- CLI：`llm restore` 加 `--purge` + `--older-than <int>`；`--purge` 时忽略 session_id（互斥校验：`--purge` 与 `session_id` 同给则报错 EXIT_ERROR；`--purge` 必须带 `--older-than` 且 ≥1，否则报错）；输出 `{"purged": N}`；无 key 也能 purge（dev 兜底警告照常）
- REST：`POST /pii/sessions/purge` body `{"older_than_days": int}` → `{"purged": N}`；`older_than_days < 1` 或缺失 → 422；无需密钥（无 503 gate）；`_ENDPOINTS` 加 `"POST /pii/sessions/purge"`
- MCP：`pii_purge_sessions`（required: `olderThanDays` int）→ `{"ok": True, "purged": N}`；非法参数（<1）→ `ok:false` 错误消息；无需密钥
- UI：`render_pii` 加清理表单（days 数字输入 min=1 + 按钮，POST `/ui/pii/purge`，key_configured 与否都显示）；结果 alert-ok 展示 purged 数
- i18n：`ui.pii_purge_form`、`ui.pii_purge_days`、`ui.pii_purge_button`、`ui.pii_purged_result` 等（en+zh）
- 测试：CLI purge（删旧留新、无 key 可 purge、缺参/非法 days 报错、与 session_id 互斥）；REST purge（e2e、422、无 key ok）；MCP purge（e2e、非法参数）；UI purge 表单（提交显示结果）

### Step 104（ADR-104）：文档 + 发布 v0.20.0 —— ✅ 完成

- 收尾：CHANGELOG `[0.20.0]` 节（**加在文件顶部**，2 个 Step 小节）、DEVELOPMENT.md V18 段、计划书三 Step 打 ✅ + 坑位记录、ADR-102/103/104 落档、`docs/index.html` 刷新（版本 0.20.0、新测试数、99 ADR、20 MCP tools）
- 版本三处 0.19.0 → 0.20.0（**bump 单独 commit**）；`uv.lock` 中 `name = "datasentry-ai"` 包的 `version` 行同步（优先 `uv lock --refresh`，网络不通则直接编辑）；tag v0.20.0；push tag；等 PyPI 0.20.0 + Pages + CI 全绿；`gh release create v0.20.0 --title "v0.20.0 — PII vault 密钥与会话生命周期管理" --notes "..."`

## 五、门禁（每个 Step 必须全绿才提交）

```bash
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core
uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85
```

（网络抖动用 `--offline`；预期 1131+ 测试、覆盖率 ≥95%）

## 六、提交纪律与发布节奏

- Conventional Commits：`feat(api): ...` / `feat(mcp): ...` / `feat(ui): ...` / `docs: ...` / `chore: ...`，body 写 Step 号与 ADR 号
- **push 前必查 `git status`；禁止 `git add -A`**（历史误收 `p/` 调试目录）；用显式 `git add <files>`
- 每 Step：实现 → 门禁 → ADR + CHANGELOG + 计划书回填 → 单 commit → push → `gh run watch`/`gh run list` 观察 CI 全绿再进下一步
- CI ~5 分钟；不要在 CI 红时继续下一步

## 七、历史坑位清单（务必遵守）

1. **测试稳定性三坑**：调度 worker 每 1s tick 会真执行到期 job（测试用远未来 cron `"0 0 1 1 *"`）；测试不依赖 worker 自动执行；HTTPServer/uvicorn 测试读掉 Content-Length body + finally `server.server_close()`
2. **httpx.ASGITransport 是 async-only**——真 HTTP 测试用 uvicorn 后台线程模式（参考 `tests/test_remote_executor.py` 的 `_serve`）
3. `/jobs` 创建返回 **201**、trigger 返回 **202**
4. MCP 工具列表严格相等断言——加工具必须同步改断言（V18 两次：18→19→20）
5. `_ENDPOINTS` frozenset——加 REST 端点必须同步加（V18 一次：+purge）
6. ruff F841 未用变量会被拦
7. 版本 bump 三处 + uv.lock datasentry-ai 包版本行；**bump 应单独 commit**
8. PyPI 索引有延迟；Pages 部署工作流上传 `docs/`（含 index.html）——push main 自动重部署
9. `datasentry --project <dir> --format json` 是全局参数放子命令前；CLI 输出 envelope（`{ok, command, data, warnings}`）JSON 流
10. key 相关测试隔离：monkeypatch.setenv/delenv("DATASENTRY_ENCRYPTION_KEY") + monkeypatch.setattr("datasentry.pii_vault._key_file", ...) 双管齐下
11. 缺 key 判定用 `key_configured`（key_source=dev 时 False）
12. `rotate_key` 需要密钥（无法解密存量映射 → VaultKeyMissingError → 503/错误消息）；rotate 后旧 key 解密失败 503 不是 404
13. CLI 校验类错误用 EXIT_ERROR（=3），配置缺失用 EXIT_CONFIG（=2）
14. CHANGELOG 现在是 Keep-a-Changelog 倒序（最新在顶部）——新节加顶部

## 八、验收

1. 门禁四连全绿，新增测试 ≥15 例（Step 102 ~8 + Step 103 ~8）
2. 四面密钥语义对齐：rotate/set-key 缺 key → CLI EXIT_CONFIG / REST 503 / MCP 错误消息 / UI 无密钥卡片；purge 无 key 四面对齐（CLI 可跑 / REST 200 / MCP ok / UI 表单可用）
3. `POST /pii/rotate-key` 无 body 行为与 v0.19.0 完全一致（向后兼容）；带 new_key 后新 key 可解密、旧 key 解密 503
4. purge 只删过期会话（新的保留）；`--older-than <1` / `older_than_days <1` 被拒绝
5. v0.20.0 发布完成：PyPI ✓ / Pages ✓ / CI ✓ / GitHub release ✓ / uv.lock 同步 ✓ / 工作区干净

## 九、完成后汇报格式

按"完成概述 / 新增能力（Step 102-104 各一段）/ 测试与门禁数据 / 发布状态 / 遗留问题"五段式汇报，中文，简明。

## V18 执行坑位记录（Step 102-104）

- REST purge 端点不能走 `_pii_vault()` gate helper（它 503 拦
  缺 key）——purge 语义无需密钥，直接 `PIIVault(client._store)`
  构造（首次测试暴露：无 key 时 503 ≠ 预期 200）
- pydantic `Field(ge=1)` 自动 422，比手写校验干净
- `save_pii_mapping` 支持显式 `created_at`（datetime 参数）——
  purge 测试借此制造"60 天前/2 天前"会话
- SIM102 规则：嵌套 if 要合并（purge 遍历里 delete 计数）
- MCP 可选参数：properties 声明 + 不出现在 required，handler
  用默认值（**arguments 关键字分发）
- UI i18n 里单引号会被 escape() 转成 &#x27;——测试断言不要带
  单引号原文
- DEVELOPMENT.md 追加 V18 段时误替换 V17 段——先确认锚点唯一
  再 edit（已恢复，两段并存）
