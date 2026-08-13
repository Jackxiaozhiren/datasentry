"""Column Profiles 交互节（Step 61，V6，ADR-061）：画像表 + 可排序 + 迷你空值条。

数据契约：`DatasetProfile.model_dump(mode="json")`（列画像 dict 数组经
`column_profiles` 键透传）；文本单元格（top_categories 值）在渲染期经
`mask_text_pii` 掩码——画像产线刻意不写原始样本（profiler examples 留空），
显示层双保险。

设计沿用 ADR-049/060 模式：Python 纯函数（profile_rows/sort_profiles）作为
JS 行为语义参照（可测）；交互逻辑原生 JS 零依赖内联；动态数据经 json_script
转义（`</script>` 免疫）；JS 只以 textContent 写单元格。
"""

from __future__ import annotations

from html import escape
from typing import Any

from datasentry_core.reporting import mask_text_pii
from datasentry_core.reporting.i18n import t
from datasentry_core.reporting.interactive import json_script

SORTABLE_KEYS = frozenset({"name", "null", "unique", "distinct", "mean", "median", "std"})

_PROFILES_JS = """(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("profiles-data").textContent);
  var columns = data.columns || [];
  var L = data.labels || {};
  function l(key, fallback) { return L[key] !== undefined ? L[key] : fallback; }
  var state = {key: "null", reverse: true};
  var tbody = document.getElementById("profiles-tbody");
  var headers = Array.prototype.slice.call(
    document.querySelectorAll("#profiles-table th[data-key]"));

  function cmp(a, b) {
    var key = state.key;
    var ka, kb;
    if (key === "name") {
      ka = (a.name || "").toLowerCase(); kb = (b.name || "").toLowerCase();
    } else { ka = a[key]; kb = b[key]; }
    var d = ka < kb ? -1 : ka > kb ? 1 : 0;
    return state.reverse ? -d : d;
  }

  function cell(text, cls) {
    var td = document.createElement("td");
    if (cls) { td.className = cls; }
    td.textContent = text;
    return td;
  }

  function render() {
    var rows = columns.slice().sort(cmp);
    headers.forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-key") === state.key) {
        th.classList.add(state.reverse ? "sorted-desc" : "sorted-asc");
      }
    });
    tbody.textContent = "";
    rows.forEach(function (c) {
      var tr = document.createElement("tr");
      var nameTd = cell(c.name);
      var typeTd = cell(c.physicalType || "-", "meta");
      nameTd.appendChild(typeTd);
      tr.appendChild(nameTd);
      tr.appendChild(cell(c.semanticType || l("profiles.semantic_unknown", "unknown"),
                          "badge-semantic"));
      tr.appendChild(cell(c.containsPii ? l("profiles.pii_yes", "pii") : l("profiles.pii_no", "no"),
                          c.containsPii ? "badge-pii" : "meta"));
      var nullTd = document.createElement("td");
      var track = document.createElement("span");
      track.className = "profiles-bar-track";
      var bar = document.createElement("span");
      bar.className = "profiles-bar";
      bar.style.width = (c.null * 100).toFixed(1) + "%";
      track.appendChild(bar);
      nullTd.appendChild(track);
      nullTd.appendChild(document.createTextNode(" " + (c.null * 100).toFixed(1) + "%"));
      tr.appendChild(nullTd);
      tr.appendChild(cell(c.unique === null ? "-" : (c.unique * 100).toFixed(1) + "%"));
      tr.appendChild(cell(String(c.distinct)));
      tr.appendChild(cell(c.min === null ? "-" : c.min));
      tr.appendChild(cell(c.median === null ? "-" : c.median));
      tr.appendChild(cell(c.mean === null ? "-" : c.mean));
      tr.appendChild(cell(c.max === null ? "-" : c.max));
      tr.appendChild(cell(c.std === null ? "-" : c.std));
      var chipsTd = cell("");
      (c.topCategories || []).forEach(function (t) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = t.value + " \u00d7" + t.count;
        chipsTd.appendChild(chip);
        chipsTd.appendChild(document.createTextNode(" "));
      });
      tr.appendChild(chipsTd);
      tbody.appendChild(tr);
    });
  }

  headers.forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.getAttribute("data-key");
      if (state.key === key) { state.reverse = !state.reverse; }
      else { state.key = key; state.reverse = key !== "name"; }
      render();
    });
  });
  render();
})();"""


def _fmt(v: Any) -> str | None:
    """数值显示：None → None（渲染为 -）；有限浮点 → 4 位有效数字。"""
    if v is None:
        return None
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)


def profile_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """DatasetProfile model_dump(mode="json") → 视图行（top_categories 已掩码）。"""
    rows = []
    for name, col in data.get("column_profiles", {}).items():
        top = []
        for value, count in col.get("top_categories") or []:
            top.append({"value": mask_text_pii(str(value)), "count": int(count)})
        rows.append(
            {
                "name": str(name),
                "physicalType": str(col.get("physical_type") or "-"),
                "semanticType": str(col.get("semantic_type") or "unknown"),
                "containsPii": bool(col.get("contains_pii")),
                "null": round(float(col.get("null_ratio") or 0.0), 4),
                "unique": round(float(col.get("unique_ratio") or 0.0), 4),
                "distinct": int(col.get("distinct_count") or 0),
                "min": _fmt(col.get("min")),
                "median": _fmt(col.get("median")),
                "mean": _fmt(col.get("mean")),
                "max": _fmt(col.get("max")),
                "std": _fmt(col.get("std")),
                "topCategories": top[:3],
            }
        )
    return rows


def sort_profiles(
    rows: list[dict[str, Any]],
    *,
    key: str = "null",
    reverse: bool = True,
) -> list[dict[str, Any]]:
    """列排序（默认 null 占比降序——最差列置顶）；name 走大小写不敏感文本比较。"""
    if key not in SORTABLE_KEYS:
        raise ValueError(f"unsupported sort key: {key}")
    if key == "name":
        return sorted(rows, key=lambda r: (r["name"] or "").lower(), reverse=reverse)
    return sorted(rows, key=lambda r: r[key], reverse=reverse)


def render_column_profiles(data: dict[str, Any], *, lang: str = "en") -> str:
    """Column Profiles 交互容器：可排序表 + 迷你空值条 + 语义/PII 徽标 + top 类别。"""
    rows = profile_rows(data)
    payload = {
        "columns": rows,
        "labels": {
            "profiles.semantic_unknown": t(lang, "profiles.semantic_unknown"),
            "profiles.pii_yes": t(lang, "profiles.pii_yes"),
            "profiles.pii_no": t(lang, "profiles.pii_no"),
        },
    }
    return (
        '<div id="profiles">'
        '<table id="profiles-table">'
        "<thead><tr>"
        f'<th data-key="name">{escape(t(lang, "profiles.column"))}</th>'
        f"<th>{escape(t(lang, 'profiles.semantic'))}</th><th>{escape(t(lang, 'profiles.pii'))}</th>"
        f'<th data-key="null">{escape(t(lang, "profiles.null"))}</th>'
        f'<th data-key="unique">{escape(t(lang, "profiles.unique"))}</th>'
        f'<th data-key="distinct">{escape(t(lang, "profiles.distinct"))}</th>'
        f"<th>{escape(t(lang, 'profiles.min'))}</th>"
        f'<th data-key="median">{escape(t(lang, "profiles.median"))}</th>'
        f'<th data-key="mean">{escape(t(lang, "profiles.mean"))}</th>'
        f"<th>{escape(t(lang, 'profiles.max'))}</th>"
        f'<th data-key="std">{escape(t(lang, "profiles.std"))}</th>'
        f"<th>{escape(t(lang, 'profiles.top_categories'))}</th>"
        "</tr></thead>"
        '<tbody id="profiles-tbody"></tbody>'
        "</table>"
        "</div>"
        f'<script type="application/json" id="profiles-data">{json_script(payload)}</script>'
        f"<script>{_PROFILES_JS}</script>"
    )


__all__ = [
    "SORTABLE_KEYS",
    "profile_rows",
    "render_column_profiles",
    "sort_profiles",
]
