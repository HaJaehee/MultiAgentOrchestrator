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

Therefore, the bundle does **not** include npm, npx, or development toolchains. It downloads and bundles solely the standalone `node.exe` executable (~70 MB), ensuring a minimal footprint.

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

"%PYTHON_BIN%" -m app.main
```

Because paths are injected via environment variables, [conf.toml](file:///d:/MultiAgentOrchestrator/conf.toml) requires **zero manual adjustments** when moving between environments.
