# Detecting data quality issues with LLM-assisted tooling

I write data quality checks for a living, and the tooling has split into two camps: rule engines that demand a config file before they'll tell you anything, and hosted platforms that want your data in their cloud before they'll look at it. Both miss the case that comes up most often: a CSV in a repo, a DuckDB file on a laptop, a table in a Postgres box you can reach. That data is dirty, and you want to know how, right now, without standing up infrastructure.

So I built a local-first data quality tool called DataSentry. This post walks through how it detects problems — and where I draw the line between what a computer should compute and what an LLM should suggest.

## Detection is statistics. AI is an accessory.

The design rule I kept coming back to: **detection should be deterministic, and AI should only assist**. A missing-value detector doesn't need an LLM — it needs a count and a ratio. An outlier detector needs a model and a threshold, not a vibe. If you ask an agent "are there duplicate orders here?" and it answers from what it *thinks* about your file, that's not a data quality check, that's a hallucination with a question mark.

DataSentry runs 39 detectors over a file or table — missingness, date parsing, encodings, cross-field rules, cross-table foreign keys, exact and fuzzy duplicates, Isolation Forest and LOF outlier models. Every issue it raises carries an evidence chain: samples, affected ratio, confidence. Not a log line. Evidence.

Here's the whole thing on a 200-row CSV I deliberately polluted:

```bash
$ pip install datasentry-ai
$ datasentry scan orders.csv
```

```json
{
  "status": "completed",
  "row_count": 200,
  "issues_count": {"low": 6, "medium": 6, "high": 1},
  "total_issues": 13,
  "detector_runs": 39,
  "quality_score": 95.3
}
```

One high-severity issue in that dump. Here's what it looks like when you ask for the details:

```bash
$ datasentry issues show <issue_id>
```

```json
{
  "issue_type": "datetime_anomaly",
  "title": "Datetime anomaly in order_date",
  "description": "[invalid_date v1.0.0] invalid_date: 9 | [impossible_date v1.0.0] impossible_date: 5",
  "severity": "high",
  "confidence": 0.995,
  "affected_count": 9,
  "affected_ratio": 0.045,
  "false_positive_risk": "low",
  "evidence": [
    {"detector_id": "invalid_date", "description": "9 values fail ISO date pattern"},
    {"detector_id": "impossible_date", "description": "5 values are impossible dates"}
  ]
}
```

The claim is specific enough to check: nine values don't parse as ISO dates, five are dates that can't exist (hello, `2026-13-01`), confidence 99.5%. You can argue with the *threshold*, but you can't argue with the *count* — the rows are right there.

## The repair loop: propose, preview, apply, rollback

Detection is only half the job. The second design rule: **nothing changes data without a human approving it**. The repair flow is four explicit steps:

```bash
$ datasentry repair propose <issue_id> --file orders.csv
```

```json
{
  "proposed": true,
  "proposal_id": "prop_069d4f189890",
  "operation": "set_null",
  "target_columns": ["order_date"],
  "estimated_rows_changed": 14,
  "rationale": "set invalid values to NULL (missing semantics)"
}
```

The proposal states what would change and how many rows. `repair preview` re-runs the rules against the proposed change so you see the before/after before committing. `repair apply` writes a fingerprinted copy of the file and a rollback artifact:

```json
{
  "applied": true,
  "run_id": "rep_21a5dffb5d6f",
  "fingerprint_before": "90ba77cb...",
  "fingerprint_after": "0706d18f...",
  "rollback_artifact": ".datasentry/repairs/rep_21a5dffb5d6f.before.csv"
}
```

`repair rollback` restores from that artifact. The blast radius of any automated fix is one file, one run, fully reversible.

## Where the LLM actually earns its place

With the deterministic core as the source of truth, the LLM gets three jobs it's actually good at:

1. **Natural language → rule candidates.** `datasentry rules propose "order_date should always be a valid date" --file orders.csv` returns candidate rules with a preflight simulation against real data — before anything is saved. The LLM drafts; the preflight verifies; you approve.
2. **Explaining issues in context.** The evidence chain is structured, but a one-line summary in terms of your domain ("9 rows in the last 120 days have unparseable dates") is a genuinely useful translation layer.
3. **Running checks from an agent.** DataSentry ships an MCP server with 20 tools. Claude or any MCP-capable agent can call `scan`, `issues`, `repair` directly — and the same human-approval gate applies. The agent can *inspect* data, but it can't *modify* it without your explicit go-ahead.

Two safety properties matter here. First, **the LLM never sees raw PII**: values are redacted into an encrypted vault before any prompt, with key rotation. Second, **the tool works with no LLM at all** — the default provider is `null`, and detection, scoring, repair and scheduling all run fine. The LLM is an upgrade, not a dependency:

```bash
$ datasentry llm status
{"provider": "null", "configured": false, "recent_calls": 0}
```

If you do want it, you can point it at local Ollama — nothing leaves the machine.

## What I'd tell you to watch out for

Honest limitations, since you'll hit them:

- **Outlier detectors are not universal.** Isolation Forest and LOF are tuned for tabular data. Feed them a weird distribution and they'll flag things you don't care about. That's exactly why the evidence chain exposes confidence and samples — the detector is allowed to be wrong, loudly and visibly.
- **Duplicate detection scales with your definition.** Exact duplicates are easy; fuzzy duplicates need a threshold you'll spend real time tuning.
- **"Confidence" is a claim about the detector, not a claim about your business.** 99.5% confidence means the pattern detector is sure about the pattern. It says nothing about whether the pattern matters to you. That's a judgment call, and it's yours — the tool just makes the evidence legible enough to judge.

## The split I keep coming back to

Statistics detect. LLMs translate and suggest. Humans approve. That split isn't a compromise — it's the property that makes each layer trustworthy in its role. The deterministic core is auditable because it's deterministic. The LLM is useful because it's expressive, and it's safe because it never holds the pen.

DataSentry is Apache-2.0, ~1200 tests at ~95% coverage. The MCP server is the piece I think people will find most surprising — an agent that runs real checks on your data, with real evidence, instead of answering from context.

- Repo: https://github.com/Jackxiaozhiren/datasentry
- Docs: https://jackxiaozhiren.github.io/datasentry/

If you try it, I'd like to hear two things: which detector you reach for first (the plugin API makes it trivial to add your own), and where the human-approval gate feels like friction rather than safety.
