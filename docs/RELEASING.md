# Releasing DataSentry

DataSentry publishes two Python distributions from one repository:

- `datasentry-ai` — CLI/API/UI/MCP application package;
- `datasentry-core` — engine/models/connectors/detectors package.

A repository commit is tested as one workspace, but PyPI resolves these distributions independently. Release metadata must therefore preserve a compatible pair.

DataSentry also publishes its MCP server metadata to the **official MCP Registry**. Registry publication is downstream of PyPI: the Registry must never advertise a package version that is not yet installable and ownership-verifiable from PyPI.

## Non-negotiable rules

### 1. Never reuse a PyPI version

PyPI artifacts are immutable. If source code in a distribution has changed since its last upload, bump that distribution's version before publishing again.

Do not rely on `skip-existing` for a multi-package release. Silently skipping one distribution can produce a new application wheel that resolves against stale core contents.

### 2. Bump core when its public/runtime API changes

If `datasentry-ai` imports a core symbol that is not present in the latest published `datasentry-core`, the core version must be bumped and published before/with the application release.

For the current pre-1.0 core line, `datasentry-ai` uses a bounded dependency such as:

```text
datasentry-core>=0.8.2,<0.9.0
```

The lower bound must equal the minimum core release that contains all APIs used by that app release.

The current release workflow publishes both distributions together and refuses to reuse either PyPI version, so a release PR must choose an unpublished version for each package participating in that workflow.

### 3. One release tag identifies the application release

The publish workflow is tag-only. A tag must match the root `datasentry-ai` version exactly:

```text
pyproject.toml: version = "1.0.3"
tag:            v1.0.3
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

### 6. Keep MCP Registry metadata aligned with the app release

The canonical Registry server name is:

```text
io.github.jackxiaozhiren/datasentry
```

For every MCP-enabled release:

- `server.json.version` must equal the root `datasentry-ai` version;
- the PyPI package entry in `server.json` must use the same version;
- the `uvx --from datasentry-ai==<version> datasentry mcp` launch metadata must use the same version;
- the packaged root README must contain the exact ownership marker:

  ```html
  <!-- mcp-name: io.github.jackxiaozhiren/datasentry -->
  ```

`tests/test_release_metadata.py` enforces these relationships, and `.github/workflows/mcp-registry.yml` runs the official `mcp-publisher validate` service on relevant pull requests.

### 7. Publish MCP metadata only after PyPI succeeds

The tag workflow has two ordered jobs:

```text
build + verify + publish PyPI wheels
                ↓
publish server.json to official MCP Registry
```

The Registry job uses GitHub Actions OIDC (`id-token: write`) rather than a long-lived Registry token. `mcp-publisher` is version-pinned and its downloaded archive is SHA-256 verified before execution.

If PyPI publication fails, the Registry job must not run. If Registry publication fails after PyPI succeeds, do not reuse or replace the published PyPI versions; fix the Registry metadata/workflow with a new release version if the package metadata itself must change.

## Release checklist

1. Decide the new `datasentry-ai` version.
2. Check whether core contents/API changed since the latest core PyPI release and whether the current two-package workflow requires a new core version.
3. Set an unpublished `packages/core/pyproject.toml` version when the core package will be published.
4. Update the root `datasentry-core` requirement lower bound/range.
5. Update `server.json` to the exact application version and verify its PyPI/`uvx` metadata.
6. Confirm the root README contains the exact `mcp-name` ownership marker.
7. Run the complete CI gate and the **MCP Registry metadata** validation workflow.
8. Confirm the wheel isolated-install smoke passes.
9. Merge the release PR.
10. Create the application tag on the merge commit, for example:

   ```bash
   git checkout main
   git pull --ff-only
   git tag v1.0.3
   git push origin v1.0.3
   ```

11. Watch **Publish to PyPI**. The first job must pass all preflight/build/wheel-smoke steps before upload.
12. Confirm the dependent **Publish MCP metadata to official Registry** job also succeeds.
13. Verify a truly external install:

    ```bash
    python -m venv /tmp/datasentry-pypi-smoke
    /tmp/datasentry-pypi-smoke/bin/pip install datasentry-ai==1.0.3
    /tmp/datasentry-pypi-smoke/bin/datasentry --version
    ```

14. Verify the released DataSentry server version appears under `io.github.jackxiaozhiren/datasentry` in the official MCP Registry.
15. Create/publish GitHub Release notes for the tag if the tag was created outside the Releases UI.
16. Only after the external install succeeds should downstream examples/actions be updated to pin the new version.

## Version-availability preflight

Before uploading anything, the workflow queries PyPI for both target distribution versions. If either already exists, the entire release fails before upload.

This is intentional. A release should fail closed rather than create a mixed pair.

## Why repository CI alone is insufficient

The normal CI workspace resolves `datasentry-core` from the local workspace. That proves the source tree is internally compatible, but it cannot prove a clean `pip install datasentry-ai` will resolve compatible already-published artifacts.

For that reason DataSentry uses four distinct checks:

```text
workspace tests
      ↓
exact built-wheel pair smoke
      ↓
post-publish clean PyPI smoke
      ↓
official MCP Registry ownership/discovery
```

The last two checks represent what new users and MCP clients actually consume outside the repository.
