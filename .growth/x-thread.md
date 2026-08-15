# X 分发素材（英文，数据工程圈）

配图：`docs/demo/quickstart.gif`（已入库）
GIF 直链（供引用）：https://raw.githubusercontent.com/Jackxiaozhiren/datasentry/main/docs/demo/quickstart.gif
话题：#DataEngineering #DataQuality #MCP #Python

## 主帖（建议发布：美东周三 09:00）

> Your data quality tool should not need your data. Or your credit card.
>
> I built DataSentry: 39 statistical detectors (duplicates, outliers, bad dates…), every finding with samples + ratio + confidence. No expectations to write first — just `pip install datasentry-ai && datasentry scan orders.csv`. [GIF]
>
> Local-first. LLM optional (works offline). MCP server with 20 tools, so Claude runs real checks, not guesses. Human approves every fix.
>
> Apache-2.0 → github.com/Jackxiaozhiren/datasentry

## 回复链 1（主帖后 30 分钟，MCP 角度）

> The part I didn't expect people to care about most: the MCP server.
>
> "are there duplicate orders in orders.csv?" → the agent runs the actual detectors and returns evidence — affected rows, confidence, samples. Not a context-window guess.
>
> Same approval gate as the CLI: agents can inspect, never modify without you.

## 回复链 2（主帖后 2-3 小时，证据链角度，附带截图或 issue show 输出）

> Every issue carries its evidence chain:
>
> "Datetime anomaly in order_date — 9 values fail ISO pattern, 5 impossible dates, confidence 99.5%, 4.5% of rows affected."
>
> You can argue with the threshold. You can't argue with the count.

## 回复链 3（次日，诚实局限角度）

> Honest limits, since you'll ask:
> • Outlier detectors (Isolation Forest/LOF) are tuned for tabular data — weird distributions will false-positive. That's why confidence is exposed.
> • Fuzzy duplicate thresholds need tuning per dataset.
> • The Web UI is functional, not pretty.
>
> Detection is deterministic. AI only suggests. You approve. That's the whole design.

## 互动纪律

- 发帖后 2 小时内回复每条评论
- 不删任何批评性回复；批评是这条帖子最大的传播素材
- 有人问"和 GE/Soda 区别" → 贴博客 2 链接（.growth/blog-2-ge-vs-datasentry-en.md，发布到 Dev.to 后替换为 Dev.to 链接）
