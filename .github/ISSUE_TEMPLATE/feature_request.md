---
name: Feature request / detector idea
about: New capability or detector — describe the data shape and expected behavior
title: "[feature] "
labels: enhancement
---

**What's the problem?**
What can't you do today that you'd like to do? A real scenario beats a
wishlist.

**Data shape**
If this is about a detector or rule, describe the data:
- column type / values (e.g. "strings like '2026-08-16T12:00:00Z'")
- what "bad" looks like (examples of values that should be flagged)
- how common the bad values are (roughly)

**Expected behavior**
What should DataSentry report or do? (issue type, severity, confidence?)

**Workarounds**
What do you do today instead?

**Notes**
- Detector ideas are welcome — see `docs/00-设计裁决记录-ADR.md` for how
  decisions get recorded, and `CONTRIBUTING.md` for how to implement one.
- LLM-related asks: remember detection itself is deterministic by design;
  LLM assistance is layered on top (translate rules / explain issues).