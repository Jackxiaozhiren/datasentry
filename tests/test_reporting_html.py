"""PRD R-01/R-02 报告 `<head>` 不变量测试（自包含报告必须手机可开、断网可开）。

需求与验收来自 `PRD_user-facing-surfaces_and_value.md` §5 G1：
R-01 `<head>` 必须含 viewport meta；R-02 生成物不得含任何外部 http(s) 引用。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from datasentry_core.reporting.html import render_html
from test_reporting_profiles import _profiles, _report

_VIEWPORT_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'


class TestReportHead:
    def test_report_emits_viewport_meta(self) -> None:
        html = render_html(_report())
        head = html.split("</head>", 1)[0]
        assert _VIEWPORT_META in head


_EXTERNAL_LOADERS = re.compile(
    r"<script[^>]+\bsrc\s*=\s*[\"']https?:|"
    r"<link[^>]+\bhref\s*=\s*[\"']https?:|"
    r"<img[^>]+\bsrc\s*=\s*[\"']https?:|"
    r"@import\s+url\(\s*[\"']?https?:|"
    r"url\(\s*[\"']?https?:",
    re.IGNORECASE,
)


class TestReportSelfContained:
    """PRD R-02：自包含 = 不加载任何外部资源（断网/内网可用是产品卖点）。

    只匹配会发起网络加载的向量（script/link/img 与 CSS url()），不匹配正文里
    作为证据出现的 URL 文本——后者是合法内容，不是外引。
    """

    def test_report_has_zero_external_references(self) -> None:
        html = render_html(_report())
        assert _EXTERNAL_LOADERS.findall(html) == []


_VOID_TAGS = {"meta", "link", "br", "img", "input", "hr", "col"}


class _TableParentFinder(HTMLParser):
    """收集每个 <table> 的直接父元素 class（断言用解析器做，不靠扫行）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.parent_classes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_TAGS:
            self.stack.append((tag, dict(attrs).get("class") or ""))
        if tag == "table":
            self.parent_classes.append(self.stack[-2][1] if len(self.stack) >= 2 else "")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def _table_parent_classes(html: str) -> list[str]:
    parser = _TableParentFinder()
    parser.feed(html)
    return parser.parent_classes


class TestReportMobile:
    def test_every_table_sits_in_a_scroll_container(self) -> None:
        # 分母含 Column Profiles 节：30 列宽表正是把整页撑到 1164px 的那个元素，
        # 只测不带 profiles 的报告会让这个盲区继续存在。
        parents = _table_parent_classes(render_html(_report(), profiles=_profiles()))
        assert len(parents) >= 4, parents
        assert all("table-scroll" in cls for cls in parents), parents


def _style_block(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert match, "报告未内联 <style>, 此断言会空转"
    return match.group(1)


def _css_rule(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, f"CSS 里找不到 {selector} 规则, 此断言会空转"
    return match.group(1)


def _narrow_media_bodies(css: str) -> list[str]:
    """按花括号配对取出每个 @media (max-width: N) 块的正文（不是猜式正则）。"""
    bodies: list[str] = []
    for match in re.finditer(r"@media[^{]*max-width[^{]*\{", css):
        depth, index = 0, match.end() - 1
        while index < len(css):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(css[match.end() : index])
                    break
            index += 1
    assert bodies, "CSS 里没有任何 max-width 断点, 此断言会空转"
    return bodies


class TestReportBilingual:
    """PRD R-05：中英双语是已实现能力，这里把它钉住并自带反证。

    注意两个我自己踩过的坑: (1) catalog 的真实值是 `zh-CN`, 不是 `zh`;
    (2) 日期按 locale 格式化, 所以"全文数字相同"这个前提不成立——要比的是
    内嵌 JSON 里的数据字段, 文案与日期格式允许不同。
    """

    def test_zh_report_switches_html_lang_and_text(self) -> None:
        html = render_html(_report(), lang="zh")
        assert '<html lang="zh' in html, "zh 的 html.lang 前缀变了"
        assert re.search(r"[\u4e00-\u9fff]", html), "zh 报告正文没有 CJK"
        # 反证：未知语言按既有契约回退 en（html.py docstring）。回退失效则此行红。
        assert '<html lang="zh' not in render_html(_report(), lang="xx")

    def test_zh_and_en_carry_the_same_issue_data(self) -> None:
        def payload(html: str) -> dict:
            raw = re.search(
                r'<script type="application/json" id="issue-data">(.*?)</script>',
                html,
                re.DOTALL,
            )
            assert raw, "报告里没有 issue-data 载荷, 此断言会空转"
            return json.loads(raw.group(1))

        en = payload(render_html(_report(), lang="en"))
        zh = payload(render_html(_report(), lang="zh"))
        # 文案必须真的不同, 否则下面比的是同一份字符串。
        assert en["labels"] != zh["labels"]
        numeric = [(r["severity"], r["priority"], r["affected"]) for r in en["issues"]]
        assert numeric == [(r["severity"], r["priority"], r["affected"]) for r in zh["issues"]]
        assert en["pageSize"] == zh["pageSize"] == 25


class TestReportLayoutInvariants:
    def test_score_bar_uses_border_box(self) -> None:
        # width:100% + 1px 双边框在 content-box 下比容器宽 2px；浏览器实测
        # .score-bar 392 vs 父容器 390，窄屏与桌面同样溢出。
        rule = _css_rule(_style_block(render_html(_report())), ".score-bar")
        assert "box-sizing: border-box" in rule, rule

    def test_narrow_viewport_rules_win_the_cascade(self) -> None:
        # 浏览器实测: 媒体块写在 _CSS 开头时, 后面第 143 行的
        # `.report-nav { position: sticky }` 在同特异性下胜出, 我加的 static 完全失效
        # (computed 仍读到 sticky, 窄屏吸顶导航条占掉 176px)。所以窄屏断点必须写在
        # 它要覆盖的基础规则之后。
        css = _style_block(render_html(_report()))
        nav_rule = css.index(".report-nav {")
        media_at = css.index("@media (max-width")
        assert media_at > nav_rule, "窄屏断点排在 .report-nav 规则之前, 会输掉层叠"

    def test_narrow_viewport_gives_controls_a_touch_target(self) -> None:
        # 实测缺口：nav 链接 20px、select/input 28px、button 23-24px（WCAG 2.5.5 要 44）。
        css = _style_block(render_html(_report(), profiles=_profiles()))
        bodies = _narrow_media_bodies(css)
        sized = [b for b in bodies if re.search(r"min-height:\s*(44px|2\.75rem)", b)]
        assert sized, "窄屏断点里没有把可交互控件抬到 44px 的规则"
        assert re.search(r"\ba\b|report-nav", sized[0]), sized[0][:120]
