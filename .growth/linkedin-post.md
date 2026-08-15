# LinkedIn 长帖（英文，作者视角）

标题建议：**Why I built a local-first data quality tool — and what "AI copilot" should mean**

---

The reason there are so many data quality tools is not that the problem is hard to detect. It's that detection was never the hard part.

I spent the last months building DataSentry, a local-first data quality platform, and the design came down to one split I kept returning to: **statistics detect, AI translates, humans approve.**

Here's what I mean, and why I think the industry gets the AI part backwards.

**Detection should be deterministic.**

A missing-value detector needs a count and a ratio, not a language model. An outlier detector needs a model and a threshold, not a "vibe." When an AI agent tells you "your file has duplicate orders" from what it infers about your data, that's not a data quality check — it's a hallucination with a question mark.

DataSentry runs 39 statistical detectors and every finding carries an evidence chain: samples, affected ratio, confidence. You can argue with the threshold. You can't argue with the count. The rows are right there.

**AI should be an accessory, not a dependency.**

The tool works with zero LLM configured — detection, scoring, repair, scheduling all run offline, on a laptop, with DuckDB. If you do want AI, it's for three things it's genuinely good at: turning natural language into rule candidates (verified by a preflight simulation), explaining issues in business terms, and letting agents run checks through an MCP server with 20 tools.

And it never sees raw PII. Values go into an encrypted vault before any prompt.

**Nobody should be able to change your data without you.**

The repair loop is propose → preview → apply → rollback. Proposals state exactly which rows change. Apply writes a fingerprinted copy and a rollback artifact. AI suggests; you decide. I'd rather lose the convenience than lose the control.

Why local-first? Because the most common dirty data lives in a repo, a laptop, or a server you already own. Standing up a platform — or shipping your data to one — to check a CSV is a category error. Local-first isn't a limitation. It's the point.

DataSentry is Apache-2.0, ~1200 tests, ~95% coverage. The MCP server is the piece I think people will find most surprising: an agent that runs real checks on your data, with real evidence, instead of answering from context.

If you've hit the wall where "data quality platform" means "enterprise procurement cycle," this might be worth a look.

https://github.com/Jackxiaozhiren/datasentry

#DataEngineering #DataQuality #LLM #MCP #OpenSource

---

备注：
- 若想要中文版可再改写（中文 LinkedIn 生态较小，默认英文）
- 发帖时附 demo GIF：https://raw.githubusercontent.com/Jackxiaozhiren/datasentry/main/docs/demo/quickstart.gif
