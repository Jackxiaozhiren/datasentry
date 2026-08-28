## What changed?

Describe the user-visible or engineering change in a few sentences.

## Why?

What problem or workflow does this solve? Link an issue when one exists.

## Evidence

Include the smallest useful evidence for the change:

- tests added/updated;
- CLI/API example;
- screenshot/GIF for UI changes;
- benchmark before/after for performance-sensitive changes;
- minimal reproduction for bug fixes.

## Safety checklist

- [ ] I did not add secrets, credentials, or real customer data.
- [ ] State-changing repair behavior still requires the intended approval path.
- [ ] Repair changes do not overwrite the original source file.
- [ ] New LLM behavior does not move deterministic detection into the model.
- [ ] New logs/errors do not expose DSNs, secrets, or sensitive values.

## Engineering checklist

- [ ] `make check` passes locally, or I explained why it cannot be run.
- [ ] New behavior has focused tests.
- [ ] Documentation is updated for user-visible changes.
- [ ] Architecture-level changes include/update an ADR when appropriate.
- [ ] Performance-sensitive changes include reproducible measurements.

## Notes for reviewers

Call out compatibility changes, migration concerns, follow-up work, or areas where you especially want review.
