"""报告交互面的键盘可达性（WCAG 2.1.1；`B3-e` 后续的可访问性收尾）。

此前三个交互面只有鼠标路径：两张可排序表的 `<th>` 只挂 `click`，问题行只靠点整行
展开详情，评分条的 `data-dim-link` 依赖 document 级 keydown 委托（浏览器里能用，
但静态检查看不到"等价键盘事件"，也无法用 Tab 到达表头与行）。这里钉住三件事：

1. 每个可排序表头都在 Tab 序列里，并带 `aria-sort` 初值；
2. 非可排序表头不进 Tab 序列（否则用户要按十几次才跳过一行表头）；
3. 每个 `click` 绑定都有同元素的 `keydown` 绑定——用计数相等做棘轮，
   将来新增第三张可排序表却忘了键盘路径时会红。
"""

from __future__ import annotations

import re

from datasentry_core.reporting.html import render_html
from test_reporting_profiles import _profiles, _report

_TH_RE = re.compile(r"<th\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_INTERACTIVE_TABLES = ("issue-table", "profiles-table")


def _table_slice(html: str, table_id: str) -> str:
    start = html.index(f'id="{table_id}"')
    return html[start : html.index("</table>", start)]


def _th_attrs(html: str) -> list[dict[str, str]]:
    """两张交互表的表头属性表。

    只取 `#issue-table` / `#profiles-table`：其余章节是静态表，没有排序语义，
    把它们的表头混进来会让"非可排序表头不进 Tab 序列"这条断言失去意义。
    """
    attrs: list[dict[str, str]] = []
    for table_id in _INTERACTIVE_TABLES:
        for tag in _TH_RE.findall(_table_slice(html, table_id)):
            attrs.append(dict(_ATTR_RE.findall(tag)))
    return attrs


def _sorted_headers(html: str) -> list[dict[str, str]]:
    return [attrs for attrs in _th_attrs(html) if "data-key" in attrs]


def _scripts(html: str) -> str:
    """只取可执行 `<script>`（`type="application/json"` 的数据块不算）。"""
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


def _report_html() -> str:
    return render_html(_report(), profiles=_profiles())


class TestSortableHeaders:
    def test_every_sortable_header_is_in_the_tab_order(self) -> None:
        headers = _sorted_headers(_report_html())
        assert len(headers) == 11, "7 列画像 + 4 列问题表; 数量变了要同步更新这条断言"
        assert all(h.get("tabindex") == "0" for h in headers), "可排序表头必须能 Tab 到"

    def test_sort_state_is_announced_before_the_first_click(self) -> None:
        headers = _sorted_headers(_report_html())
        assert all(h.get("aria-sort") == "none" for h in headers), (
            "初始 aria-sort 必须是 none, 否则读屏器会先报一个不存在的排序"
        )

    def test_plain_headers_do_not_steal_focus(self) -> None:
        plain = [attrs for attrs in _th_attrs(_report_html()) if "data-key" not in attrs]
        assert plain, "没有非排序列的话这条断言没有分母"
        assert all("tabindex" not in attrs for attrs in plain), "不可排序的表头不该进 Tab 序列"

    def test_headers_keep_their_scope(self) -> None:
        headers = _th_attrs(_report_html())
        assert all(attrs.get("scope") == "col" for attrs in headers), (
            "scope=col 是表头与数据格的关联依据, 缺失会让读屏器按位置猜测"
        )


class TestKeyboardBindings:
    def test_each_sortable_click_handler_has_a_keyboard_twin(self) -> None:
        js = _scripts(_report_html())
        clicks = js.count('th.addEventListener("click"')
        keys = js.count('th.addEventListener("keydown"')
        assert clicks == 2, f"应有 2 张可排序表(问题表 + 列画像), 实际 {clicks}"
        assert keys == clicks, f"click={clicks} 但 keydown={keys}: 有一张表只能用鼠标排序"

    def test_sort_state_is_repainted_after_each_sort(self) -> None:
        js = _scripts(_report_html())
        assert js.count('setAttribute("aria-sort"') == 2, (
            "两张表都要在 render() 里同步 aria-sort, 否则排序后读屏器仍报旧状态"
        )

    def test_enter_and_space_both_sort(self) -> None:
        js = _scripts(_report_html())
        assert js.count('e.key === "Enter"') >= 2
        assert js.count('e.key === " "') >= 2, "空格是按钮类控件的标准激活键, 不能只认回车"

    def test_issue_rows_are_focusable_and_report_expansion(self) -> None:
        js = _scripts(_report_html())
        assert "tr.tabIndex = 0" in js, "问题行必须能 Tab 到"
        assert 'tr.setAttribute("aria-expanded", "false")' in js
        assert 'tr.addEventListener("keydown"' in js, "行展开必须有键盘等价路径"
        assert 'tr.setAttribute("aria-expanded"' in js, "展开/收起后要更新状态"

    def test_score_dimension_segments_bind_their_own_keydown(self) -> None:
        js = _scripts(_report_html())
        assert 'seg.addEventListener("keydown"' in js, (
            "评分条此前用 document 级委托, 静态检查与部分辅助技术都看不到键盘等价"
        )


class TestFocusIsVisible:
    def test_css_gives_sorted_headers_and_rows_a_focus_ring(self) -> None:
        html = _report_html()
        assert "th[data-key]:focus-visible" in html
        assert "tr.issue-row:focus-visible" in html

    def test_css_marks_sortable_headers_as_clickable(self) -> None:
        html = _report_html()
        assert "th[data-key] { cursor: pointer" in html, "可排序表头要给鼠标用户一个可点的信号"
