---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Sandboxes"
---

Sandboxes are versioned execution environments registered in Agentic Resource Registry. When an agent has a sandbox component, Agentic Resource Registry installs an `observal-sandbox` MCP server that exposes one callable tool per sandbox.

## Runtime support

| Runtime | Artifact field | Local requirement | Notes |
|---------|----------------|-------------------|-------|
| `docker` | `image` | Docker daemon + Python Docker SDK | Supports any Docker/OCI image the local daemon can pull and run, for example `python:3.12-slim` or `ghcr.io/org/runner:1.0.0`. |
| `lxc` | `image` | local `lxc`/LXD CLI | Uses LXC/LXD image refs, not arbitrary OCI image refs. |
| `firecracker` | `runtime_config` | local `firecracker` binary | Requires `runtime_config.config_path` or `kernel_image_path` + `rootfs_path`. |
| `wasm` | `image` or `runtime_config.module` | local `wasmtime` or configured WASI runtime | Runs a WASI module, not a container image. |

Docker is the common path. The other runtimes are local-runtime dispatchers: Agentic Resource Registry stores and installs the metadata, but the developer machine must have the corresponding runtime and artifact already available.

## Versioned fields

A sandbox version stores:

| Field | Description |
|-------|-------------|
| `runtime_type` | `docker`, `lxc`, `firecracker`, or `wasm` |
| `image` | Docker/OCI image, LXC image ref, or WASM module path/ref |
| `resource_limits` | JSON object such as `{"timeout": 60, "memory_mb": 512, "cpu_count": 1}` |
| `network_policy` | `none`, `host`, `bridge`, or `restricted` |
| `entrypoint` | Default command when the agent does not pass one |
| `runtime_config` | Runtime-specific JSON for Firecracker/WASM/LXC extras |
| `source_url`, `source_ref`, `sandbox_path` | Source metadata for humans/reviewers |

`source_url` + `source_ref` + `sandbox_path` can point at a Dockerfile or sandbox source tree, but Agentic Resource Registry does not build or publish images from that path today. Publish a built image/artifact ref in `image`.

Local OCI setup example:

```bash
git clone https://github.com/acme/agent-sandboxes
cd agent-sandboxes/sandboxes/python-pytest
docker build -t ghcr.io/acme/python-pytest:1.0.0 .
```

Then submit:

```json
{
  "runtime_type": "docker",
  "image": "ghcr.io/acme/python-pytest:1.0.0",
  "source_url": "https://github.com/acme/agent-sandboxes",
  "source_ref": "main",
  "sandbox_path": "sandboxes/python-pytest"
}
```

## How it works

```text
observal agent pull my-agent --harness claude-code
    │
    ├── Registers "observal-sandbox" MCP server
    │   └── Exposes run_sandbox_<name> as a callable tool
    │
    └── Agent calls run_sandbox_python_pytest(command="pytest tests/")
        └── MCP server → observal-sandbox-run → local runtime → output
```

## Submit a sandbox

```bash
observal registry sandbox submit \
  --name python-pytest \
  --version 1.0.0 \
  --description "Run Python tests" \
  --runtime-type docker \
  --image python:3.12-slim \
  --resource-limits '{"timeout":60,"memory_mb":512}' \
  --entrypoint "pytest" \
  --output json
```

Sandbox submission returns the direct server result in JSON mode. Standalone Sandbox installation is not supported. Add the returned sandbox UUID to an agent with `observal agent add sandbox <sandbox-uuid>`.

From JSON:

```json
{
  "name": "python-pytest",
  "version": "1.0.0",
  "description": "Run Python tests in an isolated container",
  "owner": "your-name",
  "runtime_type": "docker",
  "image": "python:3.12-slim",
  "resource_limits": {"timeout": 60, "memory_mb": 512},
  "network_policy": "none",
  "entrypoint": "pytest"
}
```

## Publish a new sandbox version

```bash
observal registry version publish sandbox python-pytest \
  --version 1.1.0 \
  --description "Move to Python 3.12 slim" \
  --extra '{"runtime_type":"docker","image":"python:3.12-slim","resource_limits":{"timeout":60}}'
```

Like skills and MCPs, a new sandbox version is submitted for review. Approval moves the listing's `latest_version_id` to the approved version; older versions remain available for agents that pinned them.

## Manual runner examples

Docker:

```bash
observal-sandbox-run \
  --sandbox-id s-123 \
  --runtime-type docker \
  --image python:3.12-slim \
  --timeout 60 \
  --network-policy none \
  --command "python -c 'print(42)'"
```

WASM:

```bash
observal-sandbox-run \
  --sandbox-id s-123 \
  --runtime-type wasm \
  --image ./runner.wasm \
  --command "--help"
```

## Security notes

- Docker `network_policy: "none"` maps to Docker's no-network mode.
- Docker `memory_mb` and `cpu_count` are passed to the local Docker daemon.
- Non-Docker isolation is only as strong as the local runtime configuration.
- No registry-side Dockerfile build service exists yet; use prebuilt image/artifact refs.
