---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Delegate work with A2A"
description: "Find an eligible agent, hand it a bounded task, and inspect the result."
---

Delegate when another agent is better suited to a bounded part of your task. A2A supplies a task model for handing work to another agent and following its result.

A **registry agent** is an installable configuration that runs through a supported headless harness. A **remote agent** is an existing service described by an Agent Card. Finding either one does not grant permission to run it or use its credentials.

## Before you start

You need registry access, an approved agent eligible for delegation, and permission to send the task's data. Local delegation also needs a supported harness installed and authenticated for model access. Remote delegation needs an accessible reviewed endpoint and the credentials its security scheme requires.

The registry's delegation setting and your harness's permissions can disable this path. Copilot in VS Code and Goose do not currently have the headless-run capability for local child delegation.

## Find an eligible agent

```bash
observal delegate find 'summarize project documentation' --output json
```

Inspect the returned resource and eligibility signals. Use the returned identifier, not an invented agent name. For a registry agent, review its release and components before running; for a remote agent, check the reviewed card, endpoint, security requirements, and what data you will send.

## Submit a bounded task

```bash
observal delegate run RESOURCE_IDENTIFIER 'Summarize README.md in three bullets. Do not change files.' --harness claude-code --wait 0 --output json
```

Replace the identifier and choose a task appropriate to the agent. Check `observal delegate run --help` for the exact supported options on your installed version.

The example explicitly selects Claude Code for a local run and returns without waiting for completion. For another local harness or a remote service, choose the matching options rather than assuming Claude Code runs the remote agent.

A local delegated agent runs in a separate workspace. That prevents ordinary workspace edits from automatically becoming your project changes, but it is **not** a general operating-system sandbox: a process can still reach resources its permissions allow. Do not send secrets or trust the prompt alone to enforce isolation.

Remote calls go directly from the client to the reviewed endpoint, not through a registry traffic proxy. Host-specific credential configuration is documented in the [delegation architecture](/adr/0002-a2a-delegation#decision-4-no-proxy-credentials-stay-with-the-caller).

## Follow the task

Use the returned task ID:

```bash
observal delegate status TASK_ID --output json
```

A queued/running task is not a completed result. Read the task state, errors, and artifacts. If it requires more input, use the supported reply workflow; to stop unwanted work, use `observal delegate cancel TASK_ID --output json`.

Review any returned patch before applying it to your project. A completed task proves that it returned a result, not that the result is correct or safe.

## If it fails

Check eligibility, harness/model authentication, project trust, remote credentials, and the reported task error. Do not bypass trust checks or send credentials to an unrelated host to recover. Discovery and install success do not prove a delegated run can execute.

For guards, workspace limitations, remote-card review, and protocol details, use the [A2A architecture decision](/adr/0002-a2a-delegation).
