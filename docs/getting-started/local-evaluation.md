---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Try a registry locally"
description: "Run a local source evaluation instance, then create and pull an agent."
---

Use this path if you do not have a registry to connect to. You will run the server locally, install a matching CLI from the same checkout, and continue to the first-agent workflow.

This is a **source evaluation setup**, not a production deployment or a disposable reset command. It creates persistent Docker volumes; reusing an existing checkout can reuse its data.

## Before you start

You need Git, Docker Engine 24 or newer with Compose v2, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). You also need a supported coding tool and its own model access to run an agent.

Check Docker:

```bash
docker version
docker compose version
```

See [server requirements](/self-hosting/requirements) for resource requirements and [ports and volumes](/self-hosting/ports-and-volumes) if you already run local databases.

<Warning>
  The evaluation configuration contains known demo passwords, and the Compose stack publishes some ports on all network interfaces. Do not expose it to an untrusted network. Use [production deployment](/self-hosting/production-deploy) for shared or internet-accessible instances.
</Warning>

## Start the stack

Use a new checkout for this evaluation:

```bash
git clone https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
cd AgenticResourceRegistry
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build -d
```

The stack starts the API, web UI, databases, Redis, worker, and load balancer. The init service runs migrations and exits. Monitor startup instead of assuming a fixed wait time:

```bash
docker compose -f docker/docker-compose.yml ps -a
curl http://localhost/health
```

With default ports, open `http://localhost` in your browser. The health response should report `status: ok`. An HTTP 200 with `status: degraded` is not a fully healthy stack; inspect the failing dependency and [troubleshooting guide](/self-hosting/troubleshooting).

## Install a matching CLI

From this checkout:

```bash
uv tool install --editable .
observal --version
```

This aligns the CLI source with the server you just built. It also replaces an existing `uv` tool installation of the same package if `uv` permits the requested change; read any conflict message before forcing a replacement. If you need to preserve another CLI installation, use a separate evaluation environment.

## Sign in as an ordinary user

```bash
observal auth login --server http://localhost --no-setup
```

Choose CLI credentials. On a fresh evaluation stack with the default demo accounts, use `user@demo.example` and enter `user-changeme` at the password prompt. This keeps the agent workflow representative of a normal user rather than hiding permission checks behind an administrator account.

Demo accounts are not guaranteed on an existing database. If they are absent, use the account setup your instance requires. Do not reset a database to recover a missing demo login.

```bash
observal auth whoami
observal agent list
```

An empty agent list is expected if nothing has been published yet.

## Create, publish, and pull an agent

Continue with [Pull your first agent](/getting-started/quickstart), using its [no-agents-yet instructions](/getting-started/quickstart#no-agents-yet) if needed.

The default configuration does not promise that authors can install pending agents. For this evaluation, complete the review using the separate demo reviewer account (`reviewer@demo.example`, password `reviewer-changeme`) in the web UI. Inspect the submission, approve it, and publish it if the review gate still requires publication. Return to your ordinary user account to list and pull the approved agent.

Use the registry UI's actual review link or number. Do not assume a fixed review number or that every CLI/server release returns the same review metadata.

## Stop the evaluation

From the repository checkout:

```bash
docker compose -f docker/docker-compose.yml down
```

This stops the stack and preserves its volumes. Do not add `--volumes` unless you intentionally want to delete this evaluation's database contents and have checked that no valuable data is stored there.

## Next

[Pull your first agent](/getting-started/quickstart), or review [production deployment](/self-hosting/production-deploy) before making the registry available to a team.
