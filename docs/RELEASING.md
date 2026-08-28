# Releasing DataSentry

DataSentry publishes two Python distributions from one repository:

- `datasentry-ai` — CLI/API/UI/MCP application package;
- `datasentry-core` — engine/models/connectors/detectors package.

A repository commit is tested as one workspace, but PyPI resolves these distributions independently. Release metadata must therefore preserve a compatible pair.

## Non-negotiable rules

### 1. Never reuse a PyPI version

PyPI artifacts are immutable. If source code in a distribution has changed since its last upload, bump that distribution's version before publishing again.

Do not rely on `skip-existing` for a multi-package release. Silently skipping one distribution can produce a new application wheel that resolves against stale core contents.

### 2. Bump core when its public/runtime API changes

If `datasentry-ai` imports a core symbol that is not present in the latest published `datasentry-core`, the core version must be bumped and published before/with the application release.

For the current pre-1.0 core line, `datasentry-ai` uses a bounded dependency such as:

```text
datasentry-core>=0.8.0,<0.9.0
```

The lower bound must equal the minimum core release that contains all APIs used by that app release.

### 3. One release tag identifies the application release

The publish workflow is tag-only. A tag must match the root `datasentry-ai` version exactly:

```text
pyproject.toml: version = "1.0.1"
tag:            v1.0.1
```

A mismatched tag fails before build/upload.

### 4. Publish the pair atomically from the same commit

The tag must point to the commit that contains both package versions and their dependency relationship. The workflow builds both distributions from that exact checkout and publishes the artifacts in one release job.

### 5. Test the exact release wheels together

Workspace tests are necessary but not sufficient. Before upload, the release workflow creates a fresh virtual environment and installs the exact wheels from `dist/` together.

At minimum it verifies:

```bash
datasentry --version
python -c "from datasentry import DataSentry"
python -c "from datasentry_core.models.repair import RepairVerifyReport"
```

Add further import/runtime smoke checks when new cross-package boundaries become load-bearing.

## Release checklist

1. Decide the new `datasentry-ai` version.
2. Check whether core contents/API changed since the latest core PyPI release.
3. If core changed, bump `packages/core/pyproject.toml`.
4. Update the root `datasentry-core` requirement lower bound/range.
5. Run the complete CI gate.
6. Confirm the wheel isolated-install smoke passes.
7. Merge the release PR.
8. Create the application tag on the merge commit, for example:

   ```bash
   git checkout main
   git pull --ff-only
   git tag v1.0.1
   git push origin v1.0.1
   ```

9. Watch **Publish to PyPI**. It must pass all preflight/build/wheel-smoke steps before upload.
10. Verify a truly external install:

    ```bash
    python -m venv /tmp/datasentry-pypi-smoke
    /tmp/datasentry-pypi-smoke/bin/pip install datasentry-ai==1.0.1
    /tmp/datasentry-pypi-smoke/bin/datasentry --version
    ```

11. Create/publish GitHub Release notes for the tag.
12. Only after the external install succeeds should downstream examples/actions be updated to pin the new version.

## Version-availability preflight

Before uploading anything, the workflow queries PyPI for both target distribution versions. If either already exists, the entire release fails before upload.

This is intentional. A release should fail closed rather than create a mixed pair.

## Why repository CI alone is insufficient

The normal CI workspace resolves `datasentry-core` from the local workspace. That proves the source tree is internally compatible, but it cannot prove a clean `pip install datasentry-ai` will resolve compatible already-published artifacts.

For that reason DataSentry uses three distinct checks:

```text
workspace tests
      ↓
exact built-wheel pair smoke
      ↓
post-publish clean PyPI smoke
```

The final check is the closest representation of what a new user experiences.
