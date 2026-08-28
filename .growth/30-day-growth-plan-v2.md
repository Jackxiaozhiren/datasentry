# DataSentry — 30-day open-source growth plan

The objective is not to maximize raw Star count. The objective is to build a repeatable funnel:

**Visitor → Understand → Try → Get value → Star → Issue/Discussion → Contributor**

## Metrics to track weekly

Record these at the same time each week from GitHub/PyPI analytics where available:

- GitHub unique visitors;
- repository clones;
- Stars gained;
- Star / unique visitor conversion;
- PyPI downloads (interpret cautiously; CI/bots can inflate them);
- new issues and Discussions from external users;
- external pull requests;
- number of people who successfully complete the quickstart;
- traffic sources for launch posts when the platform exposes them.

Do not optimize Stars without watching trial and contribution signals.

---

## Days 1–3 — Fix conversion before buying attention

- Merge the Growth V2 repository/README changes after CI passes.
- Update GitHub About description and Topics using `.growth/github-settings-v2.md`.
- Confirm README hero, GIF, live report, PyPI link, Roadmap, Security, and Contributing links all resolve.
- Check the repository logged out / in an incognito window to see the first-time visitor experience.
- Verify `pip install datasentry-ai` and the README's first commands from a clean environment.
- Capture a baseline of traffic, clones, Stars, and PyPI downloads.

Success criterion: a new user can answer these within 30 seconds:

1. What problem does DataSentry solve?
2. Why is it different from an LLM wrapper or rule-only validator?
3. How do I try it?
4. What does the output look like?

## Days 4–7 — Make the project easy to talk about

Create/update three reusable assets:

1. **10–15 second CLI demo** — scan → evidence-backed issue → score.
2. **15–25 second repair demo** — propose → preview → apply copy → verify/diff.
3. **One social preview image** — DataSentry + one-line positioning + `Detect → Explain → Repair → Verify`.

Refresh launch copy around the product problem rather than the release number. Recommended headline pattern:

> DataSentry — automatically find and safely repair bad data locally

Avoid leading with `39 detectors`, MCP tool count, ADR count, worker pools, or internal architecture.

## Week 2 — Earn targeted discovery

### Technical article 1

Publish a practical article:

> Why I don't let an LLM decide whether data is bad

Core argument:

- deterministic detection;
- evidence samples/ratios/confidence;
- AI as proposal generation;
- human approval;
- reversible repair.

### Technical article 2

Publish a workflow article:

> From dirty CSV to verified repair: closing the data-quality loop

Use the real demo dataset and commands so the article is reproducible.

### Distribution

Share only where the content fits the community rules:

- Hacker News Show HN after the README/quickstart is stable;
- relevant data-engineering communities;
- LinkedIn/X for developer-network reach;
- Chinese developer communities using the existing `.growth` drafts;
- MCP directories/awesome lists for the real `datasentry mcp` integration.

Do not cross-post identical promotional copy everywhere. Adapt the post to each community and lead with technical value.

## Week 3 — Turn integrations into distribution

Prioritize the public issues already created:

- MCP client setup recipes;
- dbt integration example;
- Airflow integration example;
- reusable GitHub Action for quality gates.

The GitHub Action is strategically important because every public repository that uses it can become a discovery surface for DataSentry.

If the Action is not ready, do not advertise an `uses:` line in README as if it exists. Keep it on the Roadmap until it is shipped and tested.

## Week 4 — Build social proof and contributor loops

### Add a showcase path

Create a Discussion or document where users can submit:

- what they scan;
- which interface they use;
- a public repository/integration link when available;
- a short result or lesson learned.

Do not invent “Who's using DataSentry?” logos before real adoption exists.

### Maintain good first issues

Keep 3–6 genuinely bounded newcomer tasks open. A good first issue should include:

- context;
- expected files/area;
- acceptance criteria;
- commands to validate the work;
- a scope small enough to finish without learning the whole architecture.

### Release communication

For the next meaningful release, write release notes as a user story:

1. one-sentence outcome;
2. 3–5 highlights;
3. demo/screenshot when applicable;
4. upgrade command;
5. compatibility/security notes;
6. full changelog link.

Do not use “Full Changelog” as the entire release body.

---

## Experiment backlog

Run one experiment at a time where possible so traffic changes are interpretable:

- README hero wording A/B over separate weeks;
- CLI GIF vs repair GIF above the fold;
- Show HN title variants;
- benchmark article vs AI-safety article;
- integration launch (MCP/GitHub Action) vs general project launch.

## Guardrails

- Never buy Stars or use Star-exchange communities.
- Do not manufacture testimonials, adopters, benchmark wins, or competitor claims.
- Do not publish performance comparisons without hardware/methodology metadata.
- Do not let growth work weaken the human-approval and reversible-repair product invariants.
- Prefer a smaller number of users who actually run DataSentry over a large spike of low-intent traffic.

## 30-day review

At the end of the month, answer:

- Which source produced the most users who actually cloned/installed the project?
- Which README section receives the most questions or confusion?
- Which use case generated external issues or PRs?
- Are people adopting detection only, repair, CI gates, or MCP?
- Which missing integration repeatedly blocks adoption?

Use those answers to choose the next product milestone. Do not default to increasing detector count unless user evidence points there.
