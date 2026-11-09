---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Sandbox runtime reference"
description: "Distinguish registered metadata from actual local execution and isolation."
---

Use [Publish a sandbox configuration](/registry-sandbox-helper) for the author/reviewer workflow. A sandbox component describes a runtime; it does not deploy a hosted execution service or automatically sandbox every agent action.

## Runtime support

| Runtime | Required local capability | Boundary |
| --- | --- | --- |
| Docker | Docker daemon and the CLI's Docker SDK dependency | An image must be available/pullable and contain the intended command. |
| LXC/LXD | Local `lxc` tooling and compatible image | Uses LXC images, not arbitrary OCI references. |
| Firecracker | Local binary and configured kernel/root filesystem or config path | Requires its real VM/runtime setup. |
| WASM | Local WASI runtime such as configured `wasmtime`, plus module | A module is not a container image; runtime permissions differ. |

Registry-direct source fields do not build an OCI image or download every binary/artifact. Verify the machine and source/artifact trust before calling its tool.

## Versioned configuration

A release can carry `runtime_type`, `image`, `entrypoint`, `network_policy`, `resource_limits`, `runtime_config`, and source URL/ref/path. For Docker, `image` must be an OCI/Docker reference, not an arbitrary HTTP URL.

Source metadata helps review but is not itself a pinned executable artifact. Choose approved immutable refs/digests for actual shared runtime use. A basic Python image does not imply `pytest` or your repository is present.

## Tool execution

Agent installation can inject the sandbox MCP server, which advertises tools using the installed sandbox names. It dispatches the chosen command to the local runner and returns actual output/error state. Use its real tool list; do not invent a name from an unrelated display label.

The current basic Docker dispatcher does not automatically mount the host project. An echo smoke test only establishes a small runtime path, not project access or effectiveness. Output can be truncated, and timeout/missing-runtime/error states are not success.

## Network and resource limits

Docker `none`, `host`, and `bridge` map to corresponding Docker network modes. The current `restricted` Docker policy falls back to `none`; it is not an implemented arbitrary network allowlist profile. Memory and CPU limits are passed to Docker.

Other dispatchers have different requirements and do not inherit identical networking/resource protection from the presence of a registry field. Inspect actual artifact/runtime config and test limits on that platform. Docker daemon access is privileged; a registry review or a sandbox label is not independent attestation of host safety.

## Installation is through an agent

There is no standalone registry sandbox install. Add its exact component release to an agent, build/review the composition, inspect a strict pull preview, then verify the tool in the target harness. [Agent/component versioning](/guides/version-components) controls whether existing pins move.

No obsolete `observal-cli[sandbox]` dependency extra is required by the current package definition; the Docker SDK is a core CLI dependency. Missing local runtimes still need real installation, not a metadata-only workaround.
