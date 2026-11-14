"""D5-04/D5-09：网络面扫描路径收束（API/UI/MCP 共用 choke point）。

CLI / 调度器走本地信任边界（操作员本机），不经过本模块；
REST（`POST /scans`、`/ui/scans`）与 MCP `scan_file` 必须先调
`resolve_allowed_scan_path`，再进 `client.scan_file`。

规则（fail-closed）：

- 远端 DSN/URI（`postgresql://`、`mysql://`、`s3://`、`gs://`、`az://`…）
  直接放行——它们不经过本地文件系统命名空间；
- 绝对路径必须落进 workspace 或 `DATASENTRY_ALLOWED_ROOTS`（`os.pathsep`
  分隔），否则抛 `ScanPathRejected`（`ValueError` 子类 → REST 422）；
- 相对路径锚在**工作区**，不是进程 CWD（行 r5/A-1：服务进程按文档就站在项目的
  祖先目录，锚 CWD 等于把项目外的文件说成项目内的）；
- 方案名大小写敏感，且闸门表 == 消费方派发集：`S3://`、`gcs://`、`http(s)://` 这类
  无人派发的拼法直接拒（`A-10`），不再"放行后被当本地路径按 CWD 打开"；
- 设备/伪文件系统（`/dev/`、`/proc/`、`/sys/`）一律拒绝；
- `resolve()` 跟随符号链接，杜绝链接逃逸。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 本地文件系统之外的源：一律放行（由各 connector 自行处理凭据与鉴权）。
# This list must equal what the consumers actually dispatch -- `client.scan_file` routes
# postgres(ql)/mysql to the DSN connectors and s3/gs/az to the httpfs remote connector
# (`_CLOUD_PREFIXES`), and nothing else. A scheme listed here but handled nowhere was waved past the
# gate and then read as a CWD-relative local path, which is the same defect as a case-mismatched
# spelling (A-10). `test_the_gate_admits_exactly_what_the_consumer_dispatches` pins the equality.
_REMOTE_SCHEMES = (
    "postgresql://",
    "postgres://",
    "mysql://",
    "s3://",
    "gs://",
    "az://",
)

# A URI-ish prefix, scheme per RFC 3986 (case-insensitive). Anything shaped like this that is
# not in `_REMOTE_SCHEMES` is a source nobody can open, and anchoring it into the workspace
# would report a URL as a directory inside it (A-10 review #4).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

_DEVICE_PREFIXES = ("/dev/", "/proc/", "/sys/")


class ScanPathRejected(ValueError):
    """扫描路径被 workspace 收束拒绝（fail-closed，REST 映射 422）。"""


def allowed_roots(workspace: str | Path) -> list[Path]:
    """workspace + `DATASENTRY_ALLOWED_ROOTS` 扩展根（均 resolve）。"""
    roots = [Path(workspace).expanduser().resolve()]
    extra = os.environ.get("DATASENTRY_ALLOWED_ROOTS", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots


def _is_remote(raw: str) -> bool:
    """Case-sensitive on purpose. A remote pass-through is a *skipped check*, so the question
    "is this remote?" must be answered the same way by the gate and by every consumer below it --
    `client.scan_file` dispatches on these exact lowercase spellings. Matching case-insensitively
    waved `S3://../../etc/passwd.csv` through with no resolve and no root comparison, then handed it
    to the local-file branch to resolve against the CWD (A-10).
    """
    return any(raw.startswith(s) for s in _REMOTE_SCHEMES)


def resolve_allowed_scan_path(raw: str, *, workspace: str | Path) -> str:
    """校验并规范扫描路径；非法抛 `ScanPathRejected`。

    返回**校验过的那个串**（去掉首尾空白与包裹引号后的形态），而不是原样 `raw`：
    判的是 strip 后的路径、发的却是另一个路径，等于校验可以被首部空格绕过。
    返回值仍是未 resolve 的原相对/绝对写法，以保留 glob 语义与下游行为。

    相对路径锚在**工作区**（`workspace` + `DATASENTRY_ALLOWED_ROOTS`），不再锚
    `Path.cwd()`（D5-04 的行 r5）。锚在 CWD 时，服务进程只要站在项目祖先目录
    （`Dockerfile` 的 `WORKDIR /app` + compose 的 `DATASENTRY_PROJECT=/app/workspace`
    正是这种关系）就能用 `"secret.csv"` 或 `"proj/../secret.csv"` 扫到项目外文件；
    2026-09-28 本机实测两者均为 201。
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        raise ScanPathRejected("empty scan path")
    if _is_remote(text):
        return text
    if text.lower().startswith("file://"):
        text = text[len("file://") :]
    elif _SCHEME_RE.match(text):
        raise ScanPathRejected(f"unsupported source scheme: {text.split('://', 1)[0]!r}")
    roots = allowed_roots(workspace)
    candidate = Path(text).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (roots[0] / candidate).resolve()
    for prefix in _DEVICE_PREFIXES:
        if str(resolved) == prefix.rstrip("/") or str(resolved).startswith(prefix):
            raise ScanPathRejected(f"device path not scannable: {raw!r}")
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        # Same wording the REST/MCP faces already report: the roots list is server layout and has
        # no business in a client-facing refusal.
        raise ScanPathRejected(f"scan path outside workspace: {raw!r}")
    if candidate.is_absolute():
        return text
    # Hand back the *anchored* form. Returning the bare relative string would let the caller open
    # it against its own CWD, i.e. the check and the file actually read would use different bases --
    # that is how "secret.csv" still scanned the parent directory after the anchor was moved.
    # Joining (not resolving) keeps glob characters intact.
    return str(roots[0] / text)
