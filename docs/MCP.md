# DataSentry MCP setup

DataSentry ships a local **stdio MCP server** so MCP-capable AI clients can call the same data-quality capabilities exposed by the CLI and SDK.

```bash
pip install datasentry-ai
datasentry mcp --project /path/to/project
```

`--project` selects the DataSentry workspace. If it is omitted, DataSentry uses the current working directory.

> **Safety boundary**
>
> Detection and scoring remain deterministic. Some MCP tools are read-only, while repair/application and scheduler-management tools can create state. Keep client-side tool confirmation enabled for state-changing tools. DataSentry repairs write to repaired copies with rollback artifacts; they do not overwrite the original source file.

## Official MCP Registry metadata

DataSentry's canonical MCP Registry name is:

```text
io.github.Jackxiaozhiren/datasentry
```

The GitHub owner segment is intentionally case-preserving. The official Registry's GitHub OIDC authorization compares this namespace case-sensitively, so it must match the canonical GitHub login `Jackxiaozhiren` exactly.

The repository keeps the install metadata in [`server.json`](../server.json). It describes the PyPI package, stdio transport, and the equivalent `uvx` launch path for MCP clients. Release metadata tests require the Registry version to stay aligned with the `datasentry-ai` package version and require the PyPI ownership marker in the project README.

The release workflow publishes the MCP metadata only **after** the matching PyPI wheels have been published successfully, using GitHub OIDC for Registry authentication. This ordering keeps Registry discovery tied to an installable, ownership-verifiable package instead of advertising an unreleased version.

## Before configuring a client

Confirm that the executable is available:

```bash
datasentry --version
datasentry mcp --help
```

For desktop clients, an **absolute executable path is the most reliable option** because GUI applications may not inherit the same `PATH` as your terminal.

macOS / Linux:

```bash
which datasentry
```

Windows:

```powershell
where.exe datasentry
```

Use the returned path as `command` if `"datasentry"` is not discovered by the client.

## VS Code

Current VS Code MCP configuration uses a `servers` object. For a workspace-scoped configuration, create `.vscode/mcp.json`:

```json
{
  "servers": {
    "datasentry": {
      "type": "stdio",
      "command": "datasentry",
      "args": ["mcp", "--project", "${workspaceFolder}"]
    }
  }
}
```

If VS Code cannot find `datasentry`, replace `"datasentry"` with the absolute path returned by `which datasentry` or `where.exe datasentry`.

A safe first prompt:

```text
Use DataSentry to scan orders.csv. Summarize the quality score and high-severity
issues with their evidence. Do not apply any repairs.
```

A second read-only prompt:

```text
Compare the two latest scans of orders and explain any schema or quality drift.
Do not change data or scheduled jobs.
```

VS Code can sandbox local MCP servers on supported platforms. Review the filesystem policy carefully before enabling it for DataSentry. Do not combine a permissive filesystem policy with automatic approval of state-changing tools unless that is explicitly what you intend.

## Claude Desktop

Claude Desktop now supports Desktop Extensions, but DataSentry currently ships a normal stdio MCP server rather than a packaged `.mcpb` extension. The local JSON configuration mechanism remains useful for this setup.

Locate `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add a DataSentry entry while preserving any existing servers:

```json
{
  "mcpServers": {
    "datasentry": {
      "command": "/absolute/path/to/datasentry",
      "args": [
        "mcp",
        "--project",
        "/absolute/path/to/your/project"
      ]
    }
  }
}
```

Use absolute paths for both the executable and project directory. Fully quit and reopen Claude Desktop after editing the configuration.

A safe first prompt:

```text
Scan /absolute/path/to/your/project/orders.csv with DataSentry. Show the quality
score and the evidence for high-severity issues. Do not apply repairs.
```

If the server does not appear, check Claude Desktop's MCP/developer logs and verify the same command runs successfully in a terminal.

### Future packaging

A `.mcpb` Desktop Extension would reduce setup friction and make local installation easier. It is a distribution improvement, not a prerequisite for using DataSentry MCP today. Until one is released, do not download unofficial DataSentry desktop-extension packages from unknown sources.

## What to try first

The most useful initial agent workflow is intentionally read-only:

```text
scan_file → list_issues → quality_score
```

After you trust the findings, use the normal DataSentry repair loop deliberately:

```text
propose → preview → apply to a copy → verify → rollback if needed
```

For autonomous agents, prefer read-only analysis by default. Require explicit user approval before invoking repair/application, PII restoration, scheduler mutation, or other state-changing tools.

## Troubleshooting

### Client says the command is not found

Use the absolute executable path instead of `"datasentry"`.

### The server starts but cannot see the expected scans

Make sure `--project` points to the same workspace you use with the CLI. A different project path means a different `.datasentry/` metadata store.

### Paths work in the terminal but not in a desktop client

Use absolute paths. Desktop applications often have a different working directory and environment from an interactive shell.

### Database/cloud credentials are missing

Do not paste secrets directly into prompts. Configure credentials using the same DataSentry secret/environment mechanisms used by the CLI, and remember that desktop applications may not inherit shell environment variables.

### Debug the server directly

Run:

```bash
datasentry mcp --project /absolute/path/to/project
```

An MCP server waits for JSON-RPC messages on stdin, so appearing idle is expected. Diagnostic output must not be written to stdout because stdout is the MCP transport.

## Client documentation

MCP host configuration formats evolve independently of DataSentry. If a client changes its configuration UI or file format, use that client's current documentation and keep the DataSentry launch command equivalent to:

```bash
datasentry mcp --project /absolute/path/to/project
```
