"""Fixture 留存（AC-11b 的前提）：把历史 run 的源数据快照进工作区，使重扫不再依赖原文件。

为什么需要它：``rescan`` 目前按 ``run.source_path`` 找源文件。文件被移动、删除或改写之后，
那条 run 就永远落进 ``uncovered``——AC-11b 的分母不是"检测器改了几次"，而是"还有几条能复核"。
留存副本把"能重扫"从"源文件还在"变成"曾经存过"。

四条约束：

1. **显式留存，不随扫描自动复制。** 副本是源数据的第二份拷贝，可能含 PII，也占磁盘。扫描是
   热路径，不该替使用者决定这件事；只有 ``datasentry fixtures capture`` 会写副本。
2. **先验指纹再落盘。** 复制之前必须确认源文件仍与 run 的指纹一致（否则存下来的是"另一次
   数据"）。本模块不做这个判断——判断要打开数据源，属于 client；这里只做复制与记账。
3. **清单是唯一索引。** ``manifest.json`` 记录 run_id → 相对路径、sha256、大小、原路径、
   留存时间与指纹档位。重扫只认清单，不扫目录猜——半截副本或被替换的文件不会冒充 fixture。
4. **目录名只由 run_id 派生。** run_id 过白名单后作目录名，文件名固定为 ``source<后缀>``
   （后缀决定数据源类型，必须保留；原文件名记在清单里）。这样源文件名里的 ``..`` 或路径分隔符
   无法把写入点带到工作区外。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from datasentry_core.atomicio import atomic_write_text

#: 单条 run 留存副本的默认上限（64 MiB）。超过即拒绝，放宽由调用方显式传参决定。
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

#: run_id 白名单：首个字符必须是字母或数字，其余允许 ``._-``。``.`` / ``..`` 因此不可能成立。
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: 只保留形如 ``.csv`` 的后缀；异常后缀直接丢弃（重扫会按未知格式报不可比，而非猜类型）。
_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,16}$")

_CaptureStatus = Literal["captured", "already", "skipped", "failed"]


def _as_str(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"fixture manifest field {key!r} must be a string")
    return value


def _as_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"fixture manifest field {key!r} must be an integer")
    return value


@dataclass(frozen=True, slots=True)
class FixtureEntry:
    """一条 run 的留存副本清单项。``rel_path`` 相对 fixture 根，工作区整体搬走仍有效。"""

    run_id: str
    rel_path: str
    sha256: str
    size_bytes: int
    source_path: str
    captured_at: str
    fingerprint_type: str

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "rel_path": self.rel_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_path": self.source_path,
            "captured_at": self.captured_at,
            "fingerprint_type": self.fingerprint_type,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> FixtureEntry:
        """解析清单。字段缺失或类型不对一律抛 ``ValueError``，不猜默认值。"""
        return cls(
            run_id=_as_str(raw, "run_id"),
            rel_path=_as_str(raw, "rel_path"),
            sha256=_as_str(raw, "sha256"),
            size_bytes=_as_int(raw, "size_bytes"),
            source_path=_as_str(raw, "source_path"),
            captured_at=_as_str(raw, "captured_at"),
            fingerprint_type=_as_str(raw, "fingerprint_type"),
        )


class FixtureStore:
    """工作区内 ``<workspace>/.datasentry/fixtures`` 的读写门面（每条 run 一个子目录）。

    只碰文件系统：不打开数据源、不判断可比性、不算质量分。判断留给 client，结论留给 rescan。
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def run_dir(self, run_id: str) -> Path:
        """单个 run 的目录；run_id 非法直接抛，不让未校验字符串进入路径拼接。"""
        if not _RUN_ID.match(run_id):
            raise ValueError(f"unsafe scan run id for fixture path: {run_id!r}")
        return self._root / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def path_for(self, entry: FixtureEntry) -> Path:
        """清单项对应的副本绝对路径；清单被改成指向工作区外时抛 ``ValueError``。"""
        candidate = (self._root / entry.rel_path).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise ValueError(f"fixture path escapes the store: {entry.rel_path!r}")
        return candidate

    def get(self, run_id: str) -> FixtureEntry | None:
        """读清单：没有副本返回 ``None``；清单存在但损坏则抛 ``ValueError``。

        "损坏"不当"没有"处理——把一份读不懂的清单静默忽略，等于让那条 run 悄悄掉出分母。
        """
        path = self.manifest_path(run_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"fixture manifest must be a JSON object: {path}")
        entry = FixtureEntry.from_dict(raw)
        if entry.run_id != run_id:
            raise ValueError(f"fixture manifest run_id mismatch: {entry.run_id!r} != {run_id!r}")
        return entry

    def entries(self) -> list[FixtureEntry]:
        """全部清单项，按留存时间升序。目录名不合白名单的条目被跳过，见 ``orphan_dirs``。"""
        if not self._root.is_dir():
            return []
        entries: list[FixtureEntry] = []
        for child in self._root.iterdir():
            if not child.is_dir() or not _RUN_ID.match(child.name):
                continue
            entry = self.get(child.name)
            if entry is not None:
                entries.append(entry)
        return sorted(entries, key=lambda item: item.captured_at)

    def verify(self, entry: FixtureEntry) -> bool:
        """副本内容是否仍是清单记录的字节。副本被替换或截断时返回 False，清单不改。

        重扫不依赖它——指纹比对已经覆盖内容一致性；它服务于"这份副本到底还能不能用"的
        显式追问，以及 ``capture --overwrite`` 判断自己是在重建还是在重复。
        """
        path = self.path_for(entry)
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest() == entry.sha256

    def orphan_dirs(self) -> list[str]:
        """认不出的目录：名字不合白名单，或有目录无清单（崩溃残留 / 清单被删）。

        ``list`` 不把它们算成 fixture，但也不假装它们不存在——留给 ``fixtures list`` 显示。
        """
        if not self._root.is_dir():
            return []
        orphans: list[str] = []
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            if not _RUN_ID.match(child.name) or not (child / "manifest.json").is_file():
                orphans.append(child.name)
        return sorted(orphans)

    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.entries())

    def capture(
        self,
        *,
        run_id: str,
        source: Path,
        source_path: str,
        fingerprint_type: str,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        overwrite: bool = False,
    ) -> tuple[FixtureEntry, bool]:
        """复制 ``source`` 进 fixture 目录并写清单；返回 ``(清单项, 是否新建)``。

        幂等：已存在相同 sha256 的副本时复用清单项并返回 ``False``。内容不同且
        ``overwrite=False`` 抛 ``FileExistsError``——静默覆盖会让"这份副本对应哪次扫描"
        失去唯一答案。``max_bytes=None`` 表示不设上限，由调用方为磁盘负责。
        """
        if not source.is_file():
            raise FileNotFoundError(f"fixture source is not a file: {source}")
        declared = source.stat().st_size
        if max_bytes is not None and declared > max_bytes:
            raise ValueError(f"fixture source is {declared} bytes, over the {max_bytes} budget")

        existing = self.get(run_id)
        run_dir = self.run_dir(run_id)
        fresh_dir = not run_dir.exists()
        run_dir.mkdir(parents=True, exist_ok=True)
        suffix = _SUFFIX.match(source.suffix)
        name = "source" + (suffix.group(0) if suffix else "")
        target = run_dir / name
        staging = run_dir / f".{name}.staging"

        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as reader, staging.open("wb") as writer:
                os.fchmod(writer.fileno(), 0o600)
                while chunk := reader.read(1 << 20):
                    copied += len(chunk)
                    if max_bytes is not None and copied > max_bytes:
                        raise ValueError(f"fixture source grew past the {max_bytes} budget")
                    digest.update(chunk)
                    writer.write(chunk)
            hexdigest = digest.hexdigest()
            same_content = existing is not None and existing.sha256 == hexdigest
            if existing is not None and not same_content and not overwrite:
                raise FileExistsError(
                    f"fixture for run {run_id} already holds different content "
                    f"({existing.sha256[:12]} vs {hexdigest[:12]}); pass overwrite to replace it"
                )
            os.replace(staging, target)
        except BaseException:
            staging.unlink(missing_ok=True)
            if fresh_dir:
                # 只回收本次新建的空目录；已有目录里可能躺着上一份副本，不能连坐。
                with contextlib.suppress(OSError):
                    run_dir.rmdir()
            raise

        for stale in run_dir.iterdir():
            if stale.is_file() and stale.name not in {"manifest.json", name}:
                stale.unlink()

        entry = FixtureEntry(
            run_id=run_id,
            rel_path=f"{run_id}/{name}",
            sha256=hexdigest,
            size_bytes=copied,
            source_path=source_path,
            captured_at=existing.captured_at
            if same_content and existing is not None
            else datetime.now(UTC).isoformat(),
            fingerprint_type=fingerprint_type,
        )
        atomic_write_text(
            self.manifest_path(run_id),
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
        return entry, existing is None

    def prune(
        self,
        *,
        keep: int | None = None,
        max_bytes: int | None = None,
        dry_run: bool = False,
    ) -> list[FixtureEntry]:
        """保留最新 ``keep`` 条且总量不超过 ``max_bytes``，删掉其余；返回被删清单项。

        两个条件同时给出时先按条数截、再按字节从最旧的开始丢。``dry_run`` 只计算不落盘——
        删除副本不可逆，先给人看一眼。两个条件都不给则不删任何东西。
        """
        if keep is not None and keep < 0:
            raise ValueError(f"keep must be >= 0, got {keep}")
        if max_bytes is not None and max_bytes < 0:
            raise ValueError(f"max_bytes must be >= 0, got {max_bytes}")
        items = sorted(self.entries(), key=lambda item: item.captured_at, reverse=True)
        kept = items if keep is None else items[:keep]
        if max_bytes is not None:
            total = 0
            survivors: list[FixtureEntry] = []
            for entry in kept:
                if total + entry.size_bytes > max_bytes:
                    continue
                total += entry.size_bytes
                survivors.append(entry)
            kept = survivors
        keep_ids = {entry.run_id for entry in kept}
        removed = [entry for entry in items if entry.run_id not in keep_ids]
        if not dry_run:
            for entry in removed:
                shutil.rmtree(self.run_dir(entry.run_id), ignore_errors=True)
        return removed


@dataclass(frozen=True, slots=True)
class CaptureRow:
    """一条 run 的留存结果。``skipped`` / ``failed`` 都带原因，不留空白状态。"""

    run_id: str
    status: _CaptureStatus
    reason: str = ""
    size_bytes: int = 0
    sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class CaptureReport:
    """一批 run 的留存结论。被跳过的行留在 ``rows`` 里，不折叠成一个计数。"""

    rows: tuple[CaptureRow, ...] = ()

    @property
    def captured(self) -> int:
        return sum(1 for row in self.rows if row.status == "captured")

    @property
    def already(self) -> int:
        """已存在同内容副本的 run 数；它们同样可被重扫，不是失败。"""
        return sum(1 for row in self.rows if row.status == "already")

    @property
    def skipped(self) -> int:
        return sum(1 for row in self.rows if row.status in {"skipped", "failed"})

    @property
    def covered(self) -> int:
        """留存后可重扫的 run 数：AC-11b 覆盖率的分子，跳过的行不在其中。"""
        return self.captured + self.already

    def to_dict(self) -> dict[str, object]:
        """机器可读留存表。字段名即归档契约, 改动需同步测试。"""
        return {
            "checked": len(self.rows),
            "captured": self.captured,
            "already": self.already,
            "skipped": self.skipped,
            "covered": self.covered,
            "rows": [row.to_dict() for row in self.rows],
        }

    def to_markdown(self) -> str:
        lines = [
            "| run_id | status | bytes | note |",
            "| --- | --- | ---: | --- |",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.run_id} | {row.status} | {row.size_bytes} | {row.reason or '-'} |"
            )
        return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "CaptureReport",
    "CaptureRow",
    "FixtureEntry",
    "FixtureStore",
]
