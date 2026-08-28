# DataSentry roadmap

This roadmap is directional rather than a promise of dates. Priorities are chosen around one product goal:

> Make it easy to discover bad data, understand the evidence, repair it safely, and prove the repair worked.

## Current focus

### Adoption and first-run experience

- [ ] Evaluate Python 3.11 compatibility without weakening current test/type guarantees.
- [ ] Add a first-class GitHub Action for pull-request data-quality gates.
- [ ] Publish benchmark results with hardware and methodology metadata.
- [ ] Add copy-paste examples for CSV, PostgreSQL, CI, and MCP workflows.
- [ ] Improve the Web UI onboarding path from first scan → issue → evidence → repair → verify.

### Integrations

- [ ] dbt example project.
- [ ] Airflow example DAG.
- [ ] GitHub Actions example and status output.
- [ ] MCP setup recipes for common MCP-capable clients.
- [ ] Evaluate additional warehouse/database connectors based on user demand.

### Community extensibility

- [ ] Document the detector plugin lifecycle end-to-end.
- [ ] Publish a minimal “build your first detector” tutorial.
- [ ] Establish community-requested detector and connector issue templates.
- [ ] Create a showcase for external integrations and adopters.

## Product direction

### Detection and evidence

- Keep the deterministic detection core independent from LLM availability.
- Improve evidence quality before increasing detector count for its own sake.
- Add detectors when they represent common, explainable failure modes with testable semantics.

### Repair and verification

- Expand repair operations only when they remain previewable, auditable, and reversible.
- Improve before/after evidence and regression explanations.
- Keep source-overwrite protection as a hard invariant.

### Reliability and scale

- Track performance regression through reproducible benchmark gates.
- Improve large-file and sampled-scan ergonomics.
- Evolve distributed execution where real workloads justify the complexity.

## Exploring

These items are intentionally not committed releases yet:

- Polars/DataFrame-native workflows.
- Additional cloud warehouses.
- Community plugin discovery/registry.
- Data quality badges generated from CI results.
- Richer lineage/context exchange with external catalog systems.

## Non-goals

DataSentry is not trying to become:

- a general-purpose enterprise metadata catalog;
- an autonomous AI system that silently mutates source data;
- a replacement for every rule/contract framework;
- a benchmark project optimized for headline numbers at the expense of evidence or correctness.

## How priorities are chosen

GitHub issues and Discussions are the preferred place to propose roadmap changes. Concrete use cases, reproducible examples, and external adoption signals carry more weight than feature-count expansion.

If you want to contribute, look for issues labeled `good first issue` or `help wanted`, or open a feature request describing the workflow you are trying to complete.
