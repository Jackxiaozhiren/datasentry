"""原子写文本：同目录暂存、`os.replace` 发布、失败时收走自己的暂存件。

阶段 3 第 9 项（`PHASE3_PROPOSALS.md` §9）切片 a 的产物：同一段契约此前在
`secrets.write_secrets`（`Q-10`）、`PluginLocks.to_file` 与 `DataSentry._save_profile`（`Q-5`）、
`pii_vault.rotate_key`（`Q-21`）里各实现了一遍，第三遍才有了普查。契约固定为：

- 先 `os.fchmod` 再写内容——密钥类负载不允许在权限收紧前落盘（实测曾以 0644 写出）；
- 发布失败时删除本次创建的暂存件；删除本身失败时在**原异常**上 `add_note` 点名，
  既不把"没删成"说成"已清理"，也不让清理错误顶掉原错误；
- 父目录由调用方创建——各现场的父目录策略不同（secrets 要 0700），共享件不替调用方决定；
- `staging` 参数给需要唯一暂存名的并发写者（`pii_vault` 两进程并发 rotate）。
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(
    path: Path,
    payload: str,
    *,
    mode: int = 0o600,
    encoding: str = "utf-8",
    staging: str | None = None,
) -> None:
    """把 `payload` 原子写到 `path`：先暂存后发布，失败不留残骸。"""
    tmp = path.with_name(staging if staging is not None else f".{path.name}.tmp")
    try:
        with tmp.open("w", encoding=encoding) as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(payload)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException as failure:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_failure:
            failure.add_note(f"staging file could not be removed: {tmp} ({cleanup_failure})")
        raise
