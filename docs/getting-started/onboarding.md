---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Join your team's registry"
description: "Connect your machine to an existing registry without deploying a server."
---

Use this guide when your team already runs Agentic Resource Registry. You will install the CLI, sign in, and check that you can browse agents.

## Before you start

Ask your administrator for the registry URL and the sign-in method your team uses. You also need an account or an invitation. A registry may require your team's network or VPN; that is deployment-specific.

You do not need Docker to join an existing registry. You do need a supported coding tool and its own model access to run an agent later.

## Install the CLI

Follow [Install the CLI](/getting-started/installation), then check:

```bash
observal --version
```

The command should print an `observal` version. The public product name is Agentic Resource Registry; its CLI command remains `observal`.

## Sign in

Replace the example URL with the URL your administrator provided:

```bash
observal auth login --server https://registry.example.com --no-setup
```

Choose **Web sign-in** if you normally sign in through your browser. Approve the device code using your account. If your team uses direct credentials, choose the CLI email/username and password option instead.

`--no-setup` skips post-login harness setup so you can choose what to install next. Without it, human-mode login can install bundled skills and session instrumentation in detected coding tools and run setup checks. See the [authentication reference](/cli/auth#login) for those side effects.

<Note>
  Registry sign-in and coding-tool sign-in are separate. Authenticating the CLI does not log you in to Claude Code or another model provider.
</Note>

## Verify access

```bash
observal auth whoami
observal agent list
```

`whoami` should show your account. `agent list` shows approved agents visible to you. An empty list can be valid: the registry may be new, your team may not have published an agent yet, or you may need access to a teamspace.

The CLI saves its registry connection and credentials in `~/.observal/config.json`. Treat this file as sensitive; do not commit or share it.

## If sign-in fails

| What you see | What to do |
| --- | --- |
| `observal: command not found` | Open a new terminal and check the [installation guide](/getting-started/installation#troubleshooting). |
| Connection failure | Confirm the registry URL and any required VPN. Check `https://YOUR-REGISTRY/health`; replace the hostname first. |
| CLI/server version mismatch | Follow the exact upgrade or downgrade command printed by the CLI, then check `observal --version` again. |
| No account or registration disabled | Ask your administrator for an invitation or access. Do not deploy another server just to join this one. |
| No agents in the list | Ask for a published agent and the correct teamspace membership, or follow the [empty-registry path](/getting-started/quickstart#no-agents-yet). |

## Next

[Find, pull, and activate your first agent](/getting-started/quickstart).
