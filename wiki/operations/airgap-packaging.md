# Air-Gapped & Offline Packaging

In enterprise, defense, and high-security environments, systems often operate in **air-gapped networks** completely isolated from the public internet. The Multi-Agent Orchestrator Platform includes an automated packaging pipeline in [package_offline.py](file:///d:/MultiAgentOrchestrator/package_offline.py) that produces fully self-contained, zero-dependency deployment bundles.

---

## 1. Bundle Anatomy

Running `python package_offline.py` on an internet-connected build machine generates `dist/MultiAgentOrchestrator_bundle/` (and a `.zip` archive):

```text
MultiAgentOrchestrator_bundle/
├── app/                       # Application source code
├── conf.toml                  # Configuration file (copied from conf.example.toml)
├── wheels/                    # Offline pip wheel archive
├── python_runtime/            # Portable CPython distribution
├── node_runtime/              # Standalone node.exe binary (no npm needed)
├── mcp_node/                  # Pre-installed Node MCP servers
├── mcp_sandbox/               # AirgappedPySandbox Python code runner
├── workspace/                 # Initialized workspace directory & git repository
├── install_wheels_offline.bat # Re-installation verification utility
├── open_browser.py            # Waits for the port to answer, then opens the default browser
└── run_offline.bat | ps1      # One-click launcher with auto-injected environment variables
```

---

## 2. Key Packaging Mechanisms

### 2.1. Pre-Installation into Portable Runtime
A common failure mode of offline bundles is collecting wheels without verifying that they can be successfully installed and imported in the target runtime.

`package_offline.py` executes:
1. Downloads wheels into `wheels/` using `pip download`.
2. **Installs the wheels directly into `python_runtime/`**.
3. Runs smoke-test imports on all critical packages:
   ```python
   # Verified imports inside the bundle runtime:
   import fastapi, uvicorn, nicegui, litellm, mcp, sqlalchemy, aiosqlite, jupyter_client
   ```
   If any import fails, the packaging script halts immediately, preventing the distribution of a broken bundle.

### 2.2. Version Constraints & MCP 2.x Protection
The vendored `AirgappedPySandbox` server's dependency list specifies `mcp>=1.2.0` without an upper bound. In an unconstrained build, `pip` downloads `mcp 2.x`.

> [!CAUTION]
> **MCP 2.x Breaking Change**: MCP 2.0 removed `mcp.server.fastmcp` (renaming it to `MCPServer`), which breaks `AirgappedPySandbox` at startup.

To protect against this, `package_offline.py` enforces a `constraints.txt` rule:
$$\text{mcp} \ge 1.29.0, < 2.0.0$$
This guarantees that all installed MCP components maintain full API compatibility.

### 2.3. Zero-Dependency Node Runtime
The official Node MCP servers (`filesystem`, `memory`, `sequential-thinking`) are compiled into pure JavaScript (`dist/index.js`) without native C++ addons.

### 2.4. Sandbox Kernel Library Packaging
The Python code execution sandbox (`AirgappedPySandbox`) runs user and agent scripts within an IPython kernel. In an air-gapped environment without internet access, attempting to import common data science packages inside the sandbox would fail.
`package_offline.py` inspects `mcp_sandbox/requirements-kernel.txt` and downloads wheels for:
- `ipykernel`, `pandas`, `numpy`, `matplotlib`, `seaborn`, `scipy`, `sympy`
These wheels are stored in `wheels/` and installed by `install_wheels_offline.bat`.

### 2.5. Workspace Pre-initialization
The packager automatically initializes `workspace/` as an empty Git repository (`git init`), commits `.gitkeep`, and configures local `user.name` and `user.email`. This ensures the `mcp-server-git` server starts up immediately without "not a git repository" errors.

---

## 3. Launcher Automation (`run_offline.bat` / `.ps1`)

When deployed to an air-gapped target machine, users run `run_offline.bat` or `run_offline.ps1`. The launcher automatically computes absolute paths and injects the following environment variables before starting the server:

```bat
@echo off
set "BUNDLE_ROOT=%~dp0"
set "PYTHON_BIN=%BUNDLE_ROOT%python_runtime\python.exe"
set "NODE_BIN=%BUNDLE_ROOT%node_runtime\node.exe"
set "MCP_NODE_HOME=%BUNDLE_ROOT%mcp_node"
set "MCP_SANDBOX_HOME=%BUNDLE_ROOT%mcp_sandbox"
set "WORKSPACE_DIR=%BUNDLE_ROOT%workspace"
set "SANDBOX_KERNEL_PYTHON=%PYTHON_BIN%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

"%PYTHON_BIN%" -m app.main %*
```

### Automatic Browser Launch
Before handing the console over to the server, the launcher starts `open_browser.py` in the
background:

```bat
if exist "%~dp0open_browser.py" start "" /b "%PYTHON_BIN%" open_browser.py %*
"%PYTHON_BIN%" -m app.main %*
```

The waiter polls the TCP port and opens the default browser only once the server answers —
opening it immediately would land the user on a connection-refused page. It lives outside the
server process on purpose:

- With `debug = true`, uvicorn runs in reload mode and re-executes the lifespan on every file
  change. Opening from there would spawn a tab on each save.
- The server must keep the console so logs are visible and Ctrl+C stops it. Waiting is the
  launcher's job, not the server's.

The address comes from `conf.toml [app]`, overridden by the same `--host` / `--port` arguments
that were forwarded to `app.main`, so a custom port always opens the right URL. A wildcard bind
address (`0.0.0.0`) is rewritten to `127.0.0.1` — it is a bind address, not a reachable one.
Set `MAO_NO_BROWSER=1` to opt out, `MAO_BROWSER_TIMEOUT` to change the 90-second wait.

Launchers are generated artifacts, not source. To refresh them on an existing installation
without rebuilding the whole bundle:

```powershell
python package_offline.py --launchers-only "C:\path\to\MultiAgentOrchestrator_bundle"
```

`apply_update.ps1` runs this automatically after copying the sources.

### Parameter Forwarding & Encoding
- **CLI Parameter Forwarding**: Both `run_offline.ps1` (`$args`) and `run_offline.bat` (`%*`) pass all command-line arguments directly to `app.main`. Users can run `.\run_offline.ps1 --port 9000` to override the bound port dynamically.
- **UTF-8 BOM Protection**: `run_offline.ps1` is saved with UTF-8 BOM (`utf-8-sig`) and configures `[Console]::OutputEncoding = UTF8`, preventing PowerShell parser errors on Korean Windows systems.
- **Zero Configuration Drift**: Because paths and settings are injected via environment variables, [conf.toml](file:///d:/MultiAgentOrchestrator/conf.toml) requires **zero manual adjustments** when moving between environments.


---

## 4. Source-Only Updates (`package_source.py`)

The full bundle is hundreds of megabytes because it carries a portable CPython, `node.exe`,
the pip wheel archive, and the installed MCP servers. None of that changes when you fix a
bug in `app/`. Re-transferring it means re-doing the transfer review from scratch every time.

[`package_source.py`](file:///d:/MultiAgentOrchestrator/package_source.py) packages **only
source and configuration** — roughly 200 KB — to be applied on top of an already-transferred
bundle.

```powershell
python package_source.py                       # dist\MultiAgentOrchestrator_source_YYYYMMDD.zip
python package_source.py --no-tests --no-docs  # app/ + config only, smaller still
```

### What goes in

| Included | Excluded |
| :--- | :--- |
| `app/` | `python_runtime/`, `node_runtime/` |
| `tests/`, `wiki/` (opt-out) | `wheels/`, `mcp_node/`, `mcp_sandbox/` |
| `conf.example.toml`, `.env.example`, `requirements.txt` | `workspace/`, `multiagent.db` |
| `setup_mcp.py`, `package_offline.py|ps1`, `package_source.py|ps1` | `dist/`, `.git/`, `__pycache__/` |
| `README.md`, `CLAUDE.md` | |

The include list is an **allow-list**, not a deny-list. With a deny-list, a directory added
later rides along silently; with an allow-list it is simply absent, and absence is visible.

### Three things the script refuses to do

1. **Overwrite the target's `conf.toml`.** The local file is shipped as `conf.toml.new`.
   The deployed one holds that network's real endpoints; replacing it would point every
   agent at nothing.
2. **Ship an oversized file.** Anything above `--max-file-mb` (default 2 MB) aborts the run.
   A source package has no business containing a megabyte-scale file — if one appears, a
   runtime artifact leaked into the tree.
3. **Ship something that looks like a credential.** API keys, tokens, and private-key headers
   are scanned for and abort the run (`--allow-secrets` to override). `conf.toml` is
   gitignored, so nothing stops someone from pasting a real key into it, and a transfer
   review is the wrong place to discover that.

### Applying on the target

The package carries `apply_update.ps1` and `README_SOURCE.md`:

```powershell
.\apply_update.ps1 -Target "C:\Apps\MultiAgentOrchestrator_bundle"
```

It backs up `app/`, `conf.toml`, and `requirements.txt` into `_backup_<timestamp>/`, then
**replaces `app/` wholesale** rather than copying file by file — otherwise a module deleted
in this release stays behind on the target and keeps getting imported. `conf.toml` is left
alone; if it differs from `conf.toml.new`, the script says so and points at both.

`MANIFEST.txt` lists SHA-256 per file, for the transfer record and for verifying the
extracted tree on the far side.

### When a source-only update is not enough

If `requirements.txt` changed, a package the runtime does not have was added, and the source
package alone will not run. Compare against the backup after applying:

```powershell
Compare-Object (Get-Content _backup_*
equirements.txt) (Get-Content requirements.txt)
```

Any difference means the full bundle has to be rebuilt and re-transferred.
