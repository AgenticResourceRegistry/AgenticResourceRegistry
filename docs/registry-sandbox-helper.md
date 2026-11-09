---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Publish a sandbox configuration"
description: "Declare a local runtime, review its artifact and limits, and reuse it through an agent."
---

A sandbox component declares an execution runtime/artifact. The registry stores and distributes its configuration; it does not build your image, install every runtime, or turn an arbitrary agent into an operating-system sandbox.

## Before you start

Choose the runtime, a reviewed artifact/version, required local binary/daemon, intended command, permissions, data inputs, and actual isolation policy. Source URL/ref/path fields help review; they do not build or publish an image.

For a small Docker evaluation, this example only prints a marker and needs no repository data:

```bash
observal registry sandbox submit --name component-echo --version 1.0.0 --description 'A bounded Docker echo example' --runtime-type docker --image alpine:3 --entrypoint 'echo registry-component-check' --network-policy none --resource-limits '{"timeout":10,"memory_mb":128,"cpu_count":1}' --harness claude-code --draft --output json
```

`alpine:3` is an evaluation example, not an immutable production artifact. Select an approved digest/tag for shared deployment. Do not assume a plain Python image includes `pytest`, or that a runtime automatically sees the host project.

## Inspect and submit

```bash
observal registry sandbox show YOUR_NAMESPACE/component-echo --output json
observal registry sandbox submit --submit LISTING_UUID --output json
```

Review the exact runtime type, image/artifact, entrypoint, resource limits, network policy, runtime config, and source. Follow the [publication gate](/guides/review-and-publish). An approved configuration is not proof the artifact exists or isolation works on a consumer machine.

## Add it to an agent

```bash
observal agent add sandbox COMPONENT_UUID --version COMPONENT_VERSION --dir ./my-agent
observal agent build --dir ./my-agent --output json
```

Use the returned approved UUID/version. There is no standalone registry sandbox install command. An agent pull adds an `observal-sandbox` MCP entry exposing sandbox tools; inspect that generated configuration and its requirements before applying the agent install.

Activate the target harness and use the tool name shown by its actual MCP tool list, not a guessed local-name mapping. Give it a bounded command appropriate to the reviewed artifact.

## Verify execution and limits

Confirm the runtime runs the intended command and returns its actual status/output. A failed command, missing binary, timeout, or truncated logs are not a successful project test. The basic Docker dispatcher does not automatically mount the host checkout; an image/tool that cannot see your files cannot test them merely because the agent was pulled in that directory.

Docker `none` disables container networking; `restricted` currently falls back to no-network mode, not a general allowlist profile. Docker memory/CPU settings are passed to its daemon. Other runtimes have their own local requirements and do not acquire equivalent network/resource isolation merely because those metadata fields exist.

Inspect [the runtime reference](/sandboxes) and validate the actual platform. Runtime access, image trust, host permissions, and MCP permissions are separate boundaries.

## Next

[Release a new sandbox version](/guides/version-components), re-test its artifact, then update and publish the consuming agent's pins deliberately. A declared source path or a marker-only Docker smoke test is not a complete security or project-execution certification.
