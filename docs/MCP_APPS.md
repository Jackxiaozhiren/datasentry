# MCP Apps (R-10 / R-11 / R-13)

`report_export` can return a read-only card next to its JSON. Nothing else changes.

## Negotiation

The client declares support in `initialize`; the server never guesses:

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"capabilities": {"extensions": {"mcp-apps": {}}}}}
```

`extensions` is the current spelling, `experimental` is accepted for older hosts.
Both are checked for the `mcp-apps` key. A declaration that is present but
malformed (a string, a list, an empty object where an object is required, a
number) is treated as **not declared**.

## What each side gets

| Client | `initialize` result | `tools/call report_export` |
|---|---|---|
| Does not declare | unchanged: `capabilities` is exactly `{"tools": {"listChanged": false}}` | one `text` block, byte-identical to the pre-feature response |
| Declares `mcp-apps` | adds `capabilities.extensions["mcp-apps"].resourceUri` | the same `text` block, plus one `resource` block: `text/html` at `ui://datasentry/report.html` |

The JSON payload is identical in both columns. A script that parses
`content[0].text` cannot tell whether the client rendered a card.

This is a hard constraint, not a nicety: `tests/fixtures/mcp_report_export_legacy.json`
is a snapshot of the response captured **before** the feature existed, and
`tests/test_mcp_apps.py` compares the serialized response against it byte for
byte. Any added key, extra block or `_meta` turns that test red, and two
injection controls prove the comparison has a denominator.

## The card

`src/datasentry/mcp_apps.py` renders one `<article>`: dataset and run id, the
quality score, and the per-severity issue counts. It contains no `<script>`, no
`<form>`, no `<button>`, no `<input>`, no `href` and no approval vocabulary
(`approve` / `apply` / `reject` / `rollback`). Invariant 2 says an agent may only
propose; a card that cannot act is the cheapest way to keep that true.

## Narrow viewport (390 CSS px)

The card inherits R-01's invariant: a phone must not need horizontal scrolling.
The first version omitted the viewport meta, so a mobile browser laid out at
980 CSS px and pushed the count column off-screen. It now declares
`width=device-width` and the card is `box-sizing:border-box; width:100%` with
a `max-width:520px` cap for wide screens.

Measured with headless Chrome over CDP
(`Emulation.setDeviceMetricsOverride`, `width=390, height=844, mobile=true`),
using `scrollWidth` and `getBoundingClientRect`, not text content:

| Arm | `innerWidth` | `document.scrollWidth` | right edge of each count | `note` right edge |
|---|---|---|---|---|
| before the fix | 980 | 980 | 538 | 538 |
| after the fix | 390 | 390 | 372 | 372 |

`--window-size=390,420` is **not** a valid way to measure this: this Chrome
clamps the CSS viewport to 500 px, so an earlier 390×420 PNG was a 500 px
layout cropped to 390 physical pixels. Device emulation is required. Chrome is
bound to `127.0.0.1` on a fixed port, killed after the run, and the port is
checked for zero listeners. After the run the archived screenshot
[`docs/assets/mcp-apps-card-390.png`](assets/mcp-apps-card-390.png) shows all
five counts and the full note inside 390 px.

CI can only hold the **structural** half of this contract:
`tests/test_mcp_apps.py` asserts the viewport meta and the fluid-but-capped
`.card` rule, and two injection controls prove each assertion can go red for
the right reason. The geometry above is a local measurement and is not
reproduced by CI.

## No second implementation of the domain

Every number on the card is read from the report payload, and the gate asserts
that the set of numeric tokens in the rendered `<body>` is a subset of the
numeric tokens in `json.dumps(report)`. The comparison reads only `<body>`;
everything in `<head>` — the single `<style>` block and the viewport meta — is
outside it, so CSS cannot hide an invented figure. A second test renders two
different payloads and asserts the numbers move with them.

## Verification status

渲染结果**未在真客户端验证**：本仓库的验证只到「构造一个声明支持的客户端 →
收到 `text/html` 资源 → 静态检查其内容 → 用无头浏览器在 390 CSS px 下量过排版」。
没有在 Claude / ChatGPT / Cursor 等真实 MCP Apps 客户端里渲染过；上面归档的截图
是浏览器排版证据，**不是**真客户端里的渲染结果（R-12 的验收要求）。

Not verified in a real client means exactly that. Closing R-12 needs a human with
a host that supports the extension, a loopback-only server (`datasentry mcp` over
stdio is the supported path), and a screenshot **from that host** archived
outside the data directory. The `_meta` field a given host expects may also differ from the
`resource` block used here; that has to be checked against the host's docs at
that time, not assumed now.

## Running it

```bash
# the legacy path, byte for byte
uv run --no-sync pytest tests/test_mcp_apps.py -k legacy

# the card, with the resource block
uv run --no-sync pytest tests/test_mcp_apps.py -k declaring
```
