"""已发布版本索引的渲染器: 让每一个 git tag 都能在 CHANGELOG.md 里被读到.

用法:
    uv run python scripts/release_index.py --emit-md /tmp/index.md   # 只渲染区域正文
    uv run python scripts/release_index.py --write                   # 写回 CHANGELOG.md 的生成区

背景: 仓库里的 tag 远多于 CHANGELOG 的版本段落, 早期发布的版本对读者不可见.
手抄会随每次发布继续腐烂, 所以这一段是生成的. 生成区不含时间戳, 因此同一棵树上的渲染是
确定的: 表格行数与 `git tag` 的个数一一对应, `--emit-md` 的输出可用字节比对来校验.

注意: 渲染依赖完整 tag 历史. GitHub Actions 的 checkout 默认浅克隆, 若要在 CI 里校验
生成区, 需要显式设置 `fetch-depth: 0`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BEGIN = "<!-- BEGIN GENERATED RELEASE INDEX -->"
END = "<!-- END GENERATED RELEASE INDEX -->"
SECTION_HEADING = "## 已发布版本索引"

ROW_RE = re.compile(r"^\| v?[0-9][^ |]* \|", re.M)


def git(*args: str, root: Path) -> str:
    """Run a read-only git command and return its stdout with the trailing newline cut."""
    res = subprocess.run(("git", *args), cwd=root, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def render(root: Path) -> str:
    """The generated region's body: a lead-in line and one table row per tag."""
    tags = git("tag", "--sort=creatordate", root=root).splitlines()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = {s.lstrip("v") for s in re.findall(r"^## \[([^\]]+)\]", changelog, re.M)}
    lines = [
        "由 `scripts/release_index.py --write` 生成, 请勿手改. `段落` 列指该版本是否已有",
        "独立的变更段落; `-` 表示它只在这里可见, 补写段落需要判断哪些提交值得写给用户看.",
        "首行的 `提交数` 是从仓库起点到该 tag, 其余是相对上一个 tag.",
        "",
        "| tag | 日期 | 提交数 | 段落 |",
        "|---|---|---|---|",
    ]
    for index, tag in enumerate(tags):
        previous = tags[index - 1] if index else None
        rev = f"{previous}..{tag}" if previous else tag
        count = git("rev-list", "--count", rev, root=root)
        date = git("log", "-1", "--format=%ad", "--date=short", tag, root=root)
        section = "有" if tag.lstrip("v") in sections else "-"
        lines.append(f"| {tag} | {date} | {count} | {section} |")
    return "\n".join(lines)


def region(text: str) -> tuple[int, int] | None:
    """Offsets of the generated body inside `text`, or None when the markers are absent."""
    start, stop = text.find(BEGIN), text.find(END)
    if start < 0 or stop < 0 or stop < start:
        return None
    return start + len(BEGIN), stop


def splice(root: Path, body: str) -> str:
    """The whole changelog with the generated region written, creating it when missing."""
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    bounds = region(text)
    if bounds is not None:
        head, tail = bounds
        return f"{text[:head]}\n{body}\n{text[tail:]}"
    marker_anchor = "## [Unreleased]"
    section = f"{SECTION_HEADING}\n\n{BEGIN}\n{body}\n{END}\n\n"
    if marker_anchor in text:
        return text.replace(marker_anchor, section + marker_anchor, 1)
    return f"{text.rstrip()}\n\n{section}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--emit-md", type=Path, help="write only the generated body to this file")
    parser.add_argument("--write", action="store_true", help="splice the region into CHANGELOG.md")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.emit_md is None and not args.write:
        parser.print_help()
        return 2
    body = render(root)
    rows = len(ROW_RE.findall(body))
    if args.emit_md is not None:
        args.emit_md.parent.mkdir(parents=True, exist_ok=True)
        args.emit_md.write_text(body, encoding="utf-8")
        print(f"rows={rows} -> {args.emit_md}")
        return 0
    path = root / "CHANGELOG.md"
    before = path.read_text(encoding="utf-8")
    after = splice(root, body)
    if before == after:
        print("CHANGELOG.md already current")
        return 0
    path.write_text(after, encoding="utf-8")
    print(f"wrote {path} rows={rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
