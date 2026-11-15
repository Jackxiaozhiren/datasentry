"""PRD R-20：README 里的数字必须能被一条命令重现，计数不许硬写。

三层判据：

1. **散文里的数字必须有出处**——任何含数字的散文段落，后面必须紧跟一个 fenced 代码块，
   或本身含一个指向仓库内证据的链接。这是 PRD 原文那条「插一个裸数字 ⇒ 红」。
2. **检测器计数只许出现在 re-count 命令的期望输出里**，且必须等于运行时长度。硬写一个
   `39 detectors` 会在检测器增减那天变成假话（R-21 记的 showcase 数字就是这么烂掉的）。
3. **必须写明启用条件**——注册表总数不等于某份文件实际触发的数量。

刻意不判的形态（都是标识符或字面量，不是度量）：fenced 代码块、行内 code、链接目标、
HTML 块、标题行、表格行，以及 `_IDENTIFIER_LITERALS` 里逐个登记、写明理由的字面量。
"""

from __future__ import annotations

import pathlib
import re

from datasentry_core.detectors.initial import build_initial_detectors

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_TABLE_ROW = re.compile(r"^\s*\|")
_HTML_LINE = re.compile(r"^\s*<")
_INLINE_CODE = re.compile(r"`[^`]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_REPO_LINK = re.compile(r"\]\((?!https?://)[^)]+\)")
_DIGIT = re.compile(r"\d")

# 名字/版本，不是度量。加进来必须说明为什么它不是“可变的量”。
_IDENTIFIER_LITERALS = ("Apache-2.0",)

# re-count 命令及其期望输出（`# 39` 这一行是唯一允许出现该数字的地方）
_RECOUNT = re.compile(r"datasentry detectors[^\n]*\n#\s*(\d+)")

# 「N detectors」这种计数声明（散文里一律不许出现）
_DETECTOR_CLAIM = re.compile(r"\b\d+\s+(?:deterministic\s+)?detectors\b", re.IGNORECASE)

_ENABLE_CONDITION_HINTS = ("skipped", "depends on the schema")


def _blocks(text: str) -> list[tuple[int, str, str]]:
    """按行切块：(起始行号, kind, 内容)。kind ∈ {prose, fence, other}。"""
    blocks: list[tuple[int, str, str]] = []
    buf: list[str] = []
    start = 0
    in_fence = False

    def flush() -> None:
        nonlocal buf
        if buf:
            blocks.append((start, "prose", "\n".join(buf)))
            buf = []

    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            blocks.append((lineno, "fence", line))
            continue
        if in_fence:
            continue
        if not line.strip():
            flush()
            continue
        if _HEADING.match(line) or _TABLE_ROW.match(line) or _HTML_LINE.match(line):
            flush()
            blocks.append((lineno, "other", line))
            continue
        if not buf:
            start = lineno
        buf.append(line)
    flush()
    return blocks


def _numeric_prose_violations(text: str) -> list[tuple[int, str]]:
    """含数字却没有出处的散文段落。"""
    blocks = _blocks(text)
    violations: list[tuple[int, str]] = []
    for index, (lineno, kind, body) in enumerate(blocks):
        if kind != "prose":
            continue
        cleaned = _LINK_TARGET.sub("]()", _INLINE_CODE.sub("", body))
        for literal in _IDENTIFIER_LITERALS:
            cleaned = cleaned.replace(literal, "")
        if not _DIGIT.search(cleaned):
            continue
        following = blocks[index + 1][1] if index + 1 < len(blocks) else "EOF"
        if following == "fence" or _REPO_LINK.search(body):
            continue
        violations.append((lineno, body))
    return violations


def _readme() -> str:
    return README.read_text(encoding="utf-8")


class TestReadmeNumbers:
    def test_no_bare_numbers_in_prose(self) -> None:
        violations = _numeric_prose_violations(_readme())
        assert violations == [], (
            "README 散文里这些数字没有出处(补一条 fenced 命令或指向证据的链接):\n"
            + "\n".join(f"  L{line}: {body.splitlines()[0][:120]}" for line, body in violations)
        )

    def test_detector_count_is_never_hardcoded(self) -> None:
        hits = _DETECTOR_CLAIM.findall(_readme())
        assert hits == [], (
            "检测器数量必须由命令枚举, 不许写死在正文/图示里: "
            f"{hits}; re-count 命令见 `datasentry detectors | python3 -c ...`"
        )

    def test_recount_output_matches_runtime(self) -> None:
        match = _RECOUNT.search(_readme())
        assert match is not None, "README 必须保留一条 re-count 命令及其期望输出 `# <n>`"
        assert int(match.group(1)) == len(build_initial_detectors())

    def test_readme_documents_the_enable_condition(self) -> None:
        text = _readme().lower()
        assert any(hint in text for hint in _ENABLE_CONDITION_HINTS), (
            "README 必须说明「注册表总数 ≠ 某份文件实际触发数」, "
            f"至少包含其一: {_ENABLE_CONDITION_HINTS}"
        )

    def test_predicate_catches_a_bare_number(self) -> None:
        """判据本身的反证：插一个裸数字必须被抓到（否则整条门禁在空转）。"""
        sample = "# Title\n\nIt ships 39 detectors and flags 42 issues.\n"
        violations = _numeric_prose_violations(sample)
        assert violations, "裸数字没有被判定为违规 —— 门禁是空转的"
        assert "42 issues" in violations[0][1]

    def test_predicate_accepts_a_number_with_a_following_command(self) -> None:
        """反向控制：数字后面紧跟命令时必须放行，否则这条门禁只会逼人删内容。"""
        sample = (
            "# Title\n\nIt ships 42 issues in the sample.\n\n```bash\ndatasentry scan x.csv\n```\n"
        )
        assert _numeric_prose_violations(sample) == []

    def test_predicate_has_a_live_denominator(self) -> None:
        """分母守卫：解析器必须真的看见内容，否则「无违规」只是空转。

        今天合规面只剩 LICENSE 那行的 `Apache-2.0`（已按标识符登记）。分母归零意味着
        解析器把整份文件误判成 fence/other——那时这条门禁永远不会红，却永远显示通过。
        """
        blocks = _blocks(_readme())
        kinds = {kind for _, kind, _ in blocks}
        assert kinds == {"prose", "fence", "other"}, f"块分类退化: {kinds}"
        assert len(blocks) >= 100, f"块数异常偏少({len(blocks)}), 解析器可能坏了"
        numeric = [
            body
            for _, kind, body in blocks
            if kind == "prose"
            and _DIGIT.search(_LINK_TARGET.sub("]()", _INLINE_CODE.sub("", body)))
        ]
        assert numeric, "解析不到任何含数字的散文段 —— 门禁在空转"

    def test_gate_flips_on_mutated_copies(self) -> None:
        """反证：三类违规分别注入**真实 README 的副本**，每一层都必须翻红。

        只对合成样例断言是不够的——那只能证明判据函数会算，不能证明它盯得住这个文件。
        """
        original = _readme()
        expected = len(build_initial_detectors())

        bare = original.replace(
            "That number is the registry",
            "In practice it surfaces 42 issues per scan. That number is the registry",
            1,
        )
        assert bare != original, "注入点漂移: README 的结构变了, 先修这条反证"
        assert _numeric_prose_violations(bare), "裸数字没有被抓到"

        hardcoded = original.replace(
            "registry of **deterministic detectors**",
            "registry of **40 deterministic detectors**",
            1,
        )
        assert hardcoded != original, "注入点漂移: README 的结构变了, 先修这条反证"
        assert _DETECTOR_CLAIM.findall(hardcoded), "硬写计数没有被抓到"

        stale = original.replace(f"\n# {expected}\n", f"\n# {expected + 1}\n", 1)
        assert stale != original, "注入点漂移: README 的结构变了, 先修这条反证"
        match = _RECOUNT.search(stale)
        assert match is not None
        assert int(match.group(1)) != expected, "陈旧的期望输出没有被抓到"
