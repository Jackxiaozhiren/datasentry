# DataSentry launch copy — Growth V2

These drafts use the current product positioning. Re-check version numbers, commands, and metrics immediately before publishing.

## Show HN

### Title

**Show HN: DataSentry – automatically find and safely repair bad data locally**

### Body

I built DataSentry around a problem I kept running into with data-quality workflows: before you can enforce a rule, you often have to discover that the problem exists at all — and after you find it, the hard part is fixing it without making the dataset worse.

DataSentry treats that as one loop:

**Detect → Explain → Repair → Verify**

The detection core is deterministic rather than LLM-based. A scan runs evidence-driven detectors and gives findings context such as samples, ratios, confidence, severity, and a six-dimension quality score.

AI is optional and sits on the proposal side of the boundary. A repair can be proposed and previewed, but applying it writes a repaired copy instead of overwriting the source. The result can be re-scanned, diffed, and rolled back.

Quick start:

```bash
pip install datasentry-ai
datasentry scan orders.csv
datasentry issues list --severity high
```

There is also a Web/REST surface and an MCP stdio server:

```bash
datasentry mcp
```

The MCP part is useful to me because an agent can call the real data-quality tools instead of guessing from a prompt, while DataSentry keeps its repair/approval boundaries.

The project is local-first: DuckDB runs the deterministic core, and LLM integration is optional (including local Ollama). It can scan common files plus databases/cloud-object inputs, export CI-friendly reports, and compare historical drift.

I deliberately removed the project's old “1M rows in ~X seconds” headline because a benchmark without hardware and workload conditions is not very useful. The benchmark is in the repo so people can reproduce it on their own machine, and published results should include the environment.

What I'd especially value feedback on:

1. Does the deterministic-detection / AI-proposal boundary match how you'd want to trust a repair tool?
2. Is the Detect → Explain → Repair → Verify workflow clear from the first-run experience?
3. Which integration would make you actually try this in a real pipeline?

GitHub: https://github.com/Jackxiaozhiren/datasentry

Demo: https://jackxiaozhiren.github.io/datasentry/

---

## LinkedIn

Bad data is rarely a one-step problem.

You have to find the issue, understand whether it is real, fix it safely, and prove the fix did not introduce something worse.

That's the workflow I am focusing DataSentry on:

**Detect → Explain → Repair → Verify**

- deterministic, evidence-backed detection;
- optional AI for proposals rather than authority;
- repair preview before mutation;
- apply to a copy, not the source;
- re-scan verification, diff, and rollback;
- local-first execution with CLI, Web/REST, and MCP surfaces.

Quick start:

`pip install datasentry-ai`

`datasentry scan orders.csv`

The project is Apache-2.0. I am especially looking for feedback from people who run data-quality checks in CI or data pipelines: where does the workflow still feel awkward?

GitHub: https://github.com/Jackxiaozhiren/datasentry

---

## X / short post

DataSentry is an open-source, local-first data quality tool built around one loop:

Detect → Explain → Repair → Verify

Deterministic detection. Evidence-backed issues. AI can propose; humans approve. Repairs go to a copy and can be verified/diffed/rolled back.

`pip install datasentry-ai`

https://github.com/Jackxiaozhiren/datasentry

---

## 中文社区版本

我在做一个开源的数据质量工具 DataSentry，现在把项目主线重新收敛成了四步：

**发现 → 解释 → 修复 → 验证（Detect → Explain → Repair → Verify）**

它和“直接让大模型判断数据对不对”的思路不同：

- 底层检测是确定性的，不依赖 LLM；
- 每个问题尽量提供样本、比例、置信度等证据；
- AI 只负责辅助提出规则或修复建议；
- 修复前可以 preview；
- apply 写入副本，不覆盖原始文件；
- 修复后重新扫描验证，还可以看 diff 或 rollback；
- DuckDB 本地执行，LLM 也是可选的；
- 同时提供 CLI/TUI、Web/REST 和 MCP 接口。

最简单的体验方式：

```bash
pip install datasentry-ai
datasentry scan orders.csv
datasentry issues list --severity high
```

项目： https://github.com/Jackxiaozhiren/datasentry

我现在最希望得到的不是“再加多少功能”的建议，而是实际工作流反馈：如果你要把它接进自己的数据 pipeline，哪一步最阻碍你使用？

---

## Publishing checklist

Before posting:

- [ ] Verify the current release/version.
- [ ] Re-run the quickstart commands from a clean environment.
- [ ] Check that the live demo loads.
- [ ] Do not quote test counts, coverage, detector counts, MCP tool counts, or performance numbers unless verified against the current release.
- [ ] Do not make broad claims about competitor deployment models or missing features without current evidence.
- [ ] Link to the repository, not several competing calls to action.
- [ ] Read and follow the target community's self-promotion rules.
