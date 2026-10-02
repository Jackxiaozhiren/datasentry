"""把异常文本变成可外发文本的唯一一处（REST、Web UI、MCP 共用）。

`D2-02` / `D5-06` 立这条规则时只有 REST 面走它；`A-8` 量出 `/ui/*` 有 14 处直接插
`str(exc)`，`A-8-mcp` 又量出 MCP 面 13 处。同一条判定写三遍正是本审计反复计数的漂移源，
所以这里只有一份实现，两个面都从这里取。
"""

from __future__ import annotations

import re

# Two separators are required, so a lone `/v1` survives while `/v1/scans`,
# `/srv/workspace/x.csv`, `C:\Users\…` and `\\nas\share\…` do not.
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\|/)[^\s'\"`,;)]*(?:/|\\)[^\s'\"`,;)]*")
_SEP_RE = re.compile(r"[/\\]")
# Whitespace plus the run after it. Quotes are excluded from the run so a quoted filename's closing
# `'` stays where the operator can read it; one opening `'` is allowed because `O'Reilly`-style
# directories are real and Python's OSError repr quotes with `'`.
_NEXT_TOKEN_RE = re.compile(r"\s*'?([^\s'\"`,;)\r\n]*)")


def redact_paths(text: str) -> str:
    """Replace every path-shaped run with ``<path>``, spaces inside the run included.

    A run continues across whitespace only when the *immediately* following token carries another
    separator -- that is what redacts `/srv/My Data/orders.csv` whole while
    `failed to read /srv/a/b.csv after 3/5 attempts` keeps its reason.
    """
    out: list[str] = []
    cursor = 0
    while (found := _PATH_RE.search(text, cursor)) is not None:
        end = found.end()
        while (nxt := _NEXT_TOKEN_RE.match(text, end)) is not None and _SEP_RE.search(nxt.group(1)):
            end = nxt.end()
        out.append(text[cursor : found.start()])
        out.append("<path>")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def safe_detail(exc: BaseException) -> str:
    """Keep the reason of a 4xx, drop the map of the server's filesystem (D2-02, D5-06, A-8).

    `FileNotFoundError` renders as "[Errno 2] No such file or directory: '/srv/…/x.csv'": useful
    to the operator who can look at the server, and a directory listing for anyone who cannot.
    The boundary, stated so the next reader does not over-trust it: an absolute or drive-rooted run
    is always replaced, while a fragment that only *looks* relative can survive a stop character
    (`/srv/My Reports (2024)/x.csv` leaves `Reports (2024)/x.csv`). That is a file name, not a map.
    """
    text = str(exc) or type(exc).__name__
    return redact_paths(text)[:200]
