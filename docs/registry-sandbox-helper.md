---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Sandbox helper"
---

Use sandbox components when an agent needs a reviewed runtime for running commands through an MCP tool.

## What to fill in

| Field | What it means | Example |
|-------|---------------|---------|
| Runtime type | Local runtime used by `observal-sandbox-run` | `docker` |
| Image / artifact ref | Docker image, LXC image ref, WASM module path, or runtime artifact | `python:3.12-slim` |
| Entrypoint | Default command when the agent does not pass one | `pytest` |
| Network policy | Docker network mode or runtime policy hint | `none` |
| Resource limits JSON | Timeout, memory, and CPU settings | `{"timeout": 60, "memory_mb": 512, "cpu_count": 1}` |
| Runtime config JSON | Runtime-specific settings for LXC, Firecracker, or WASM | `{}` for Docker |
| Source URL | Optional repo for review and provenance | `https://github.com/docker-library/python` |
| Source ref | Branch, tag, or commit for that source repo | `master` |
| Sandbox path | Directory in the source repo where the Dockerfile or runtime config lives | `3.12/slim-bookworm` |

If you do not know the source fields, leave them blank. They help reviewers understand where an image or runtime config came from. They do not build the image.

## Local OCI setup git link

Use source fields when the sandbox image comes from a Dockerfile, Containerfile, or Compose build in a repo:

- `source_url`: the git repo to inspect, for example `https://github.com/acme/agent-sandboxes`
- `source_ref`: the branch, tag, or commit, for example `main`, `v1.2.0`, or a commit SHA
- `sandbox_path`: the directory inside that repo, for example `sandboxes/python-pytest`
- `image`: the image tag users must build or pull, for example `ghcr.io/acme/python-pytest:1.0.0`

Example repo layout:

```text
agent-sandboxes/
  sandboxes/python-pytest/Dockerfile
  sandboxes/node-tests/Dockerfile
  sandboxes/go-tests/Dockerfile
```

Example setup instructions for users:

```bash
git clone https://github.com/acme/agent-sandboxes
cd agent-sandboxes/sandboxes/python-pytest
docker build -t ghcr.io/acme/python-pytest:1.0.0 .
```

## OCI sandbox examples

### Python test runner

```json
{
  "runtime_type": "docker",
  "image": "python:3.12-slim",
  "entrypoint": "pytest",
  "network_policy": "none",
  "resource_limits": {"timeout": 60, "memory_mb": 512, "cpu_count": 1},
  "runtime_config": {},
  "source_url": "https://github.com/docker-library/python",
  "source_ref": "master",
  "sandbox_path": "3.12/slim-bookworm"
}
```

### Node build runner

```json
{
  "runtime_type": "docker",
  "image": "node:22-alpine",
  "entrypoint": "npm test",
  "network_policy": "none",
  "resource_limits": {"timeout": 120, "memory_mb": 1024, "cpu_count": 2},
  "runtime_config": {},
  "source_url": "https://github.com/nodejs/docker-node",
  "source_ref": "main",
  "sandbox_path": "22/alpine3.22"
}
```

### Go test runner

```json
{
  "runtime_type": "docker",
  "image": "golang:1.24-alpine",
  "entrypoint": "go test ./...",
  "network_policy": "none",
  "resource_limits": {"timeout": 180, "memory_mb": 1024, "cpu_count": 2},
  "runtime_config": {},
  "source_url": "https://github.com/docker-library/golang",
  "source_ref": "master",
  "sandbox_path": "1.24/alpine3.21"
}
```

## Non-Docker runtime config examples

```json
{"profile":"default"}
```

```json
{"kernel_image_path":"/opt/firecracker/vmlinux","rootfs_path":"/opt/firecracker/rootfs.ext4"}
```

```json
{"module":"./runner.wasm","preopen_dirs":["."]}
```

## CLI example

Submit a sandbox from a JSON file like the examples above:

```bash
observal registry sandbox submit --from-file sandbox.json
```

## Sources

- [Docker Official Images](https://github.com/docker-library/official-images)
