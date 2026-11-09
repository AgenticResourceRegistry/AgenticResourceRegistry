---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Try a registry locally"
description: "Run a local source evaluation instance, then create and pull an agent."
---

Use this path if you do not have a registry to connect to. You will run the server locally, install a matching CLI from the same checkout, and continue to the first-agent workflow.

This is a **source evaluation setup**, not a production deployment or a disposable reset command. It creates persistent Docker volumes; reusing an existing checkout can reuse its data.

## Before you start

You need Git, Docker Engine 28 or newer with Compose 2.24.4 or newer, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). You also need a supported coding tool and its own model access to run an agent.

The evaluation uses known demo credentials and loopback-published ports. [Docker documents that Engine releases older than 28.0.0 can expose localhost-published ports to peers on the same L2 network](https://docs.docker.com/engine/network/port-publishing/#publishing-ports), so the source feature minimum of Engine 25 is not sufficient for this local security boundary. Use a supported, patched Engine 28+ release. This setup assumes ordinary NAT bridge/default routing and a trusted Docker host; do not enable direct/routed or unprotected networking for a demo registry, and do not treat host bindings as an operating-system sandbox.

Check Docker:

```bash
docker version
docker compose version
```

See [server requirements](/self-hosting/requirements) for resource requirements and [ports and volumes](/self-hosting/ports-and-volumes) if you already run local databases.

<Warning>
  The evaluation configuration contains known demo passwords. The base Compose file publishes LB/web broadly; this guide's required overlay replaces those bindings with loopback. Do not remove it or expose the instance through an untrusted tunnel/proxy. Use [production deployment](/self-hosting/production-deploy) for shared or internet-accessible instances.
</Warning>

## Start the stack

Use a new checkout and project-specific local image tags. Run setup in a dedicated shell; the `&&` chain stops before entering an existing directory or writing `.env` if cloning fails:

```bash
git clone --branch v1.16.0 --depth 1 https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git &&
cd AgenticResourceRegistry &&
cp .env.example .env &&
export EVAL_PROJECT="arr-eval-$(date -u +%Y%m%d%H%M%S)-$$" &&
cat > compose.evaluation.yml <<'YAML'
services:
  observal-init:
    image: "arr-eval-api:${EVAL_PROJECT}"
  observal-api:
    image: "arr-eval-api:${EVAL_PROJECT}"
  observal-worker:
    image: "arr-eval-api:${EVAL_PROJECT}"
  observal-web:
    image: "arr-eval-web:${EVAL_PROJECT}"
    ports: !override
      - "127.0.0.1:${WEB_HOST_PORT:-3002}:3000"
  observal-lb:
    ports: !override
      - "127.0.0.1:${LB_HOST_PORT:-8080}:80"
YAML
```

Only continue after setup succeeds. The overlay isolates the API/web image tags as well as the container and volume identities; the tagged base file otherwise builds shared `:latest` tags. Do not omit this overlay on later commands. Record `EVAL_PROJECT`, the ports, and the overlay path so a new shell can restore them rather than generate a new project for `down`.

Choose available host ports and keep the selected LB URL for health, browser, and login commands:

```bash
# Change these values before starting if another process owns a port.
export LB_HOST_PORT=8080 WEB_HOST_PORT=3002 POSTGRES_HOST_PORT=5433
export REDIS_HOST_PORT=6380 CLICKHOUSE_HOST_PORT=8124
EVAL_URL="http://localhost:${LB_HOST_PORT}"
dc() {
  : "${EVAL_PROJECT:?Restore the recorded evaluation project before running Compose}"
  docker compose -p "$EVAL_PROJECT" -f docker/docker-compose.yml -f compose.evaluation.yml "$@"
}
docker compose ls
docker ps --format '{{.Names}}\t{{.Ports}}'
dc config --images
```

Confirm the project is not an existing deployment and that API/init/worker/web images use `arr-eval-*` tags with your recorded project, not shared `:latest` tags. The required `!override` port replacements keep LB/web on loopback; Compose 2.24.4+ is required so base broad bindings are replaced, not appended. Do not remove the tags or substitute an older merge behavior. The database/Redis/ClickHouse base ports are also loopback. Use the [restricted single-node setup](/self-hosting/single-node-deploy) for a reviewed shared deployment.

Validate the actual merged bindings without printing the complete credential-bearing configuration. This checks every published service port, including any accidentally retained broad binding:

```bash
if dc config --format json | python3 -c '
import json
import sys
config = json.load(sys.stdin)
bad = [(name, port.get("published"))
       for name, service in config["services"].items()
       for port in service.get("ports", [])
       if port.get("host_ip") != "127.0.0.1"]
if bad:
    raise SystemExit(f"Non-loopback published ports: {bad}. Correct the evaluation overlay; do not start.")
print("All configured published ports bind to loopback.")
'
then
  EVAL_BINDINGS_OK=1
else
  EVAL_BINDINGS_OK=0
  printf '%s\n' 'Evaluation binding validation failed; do not start.' >&2
fi
```

The following block starts the stack **only if** the merged-binding validation and port preflight both succeed:

```bash
if [ "${EVAL_BINDINGS_OK:-0}" = 1 ] && python3 - <<'PY'
import os
import socket
names = ("LB_HOST_PORT", "WEB_HOST_PORT", "POSTGRES_HOST_PORT", "REDIS_HOST_PORT", "CLICKHOUSE_HOST_PORT")
ports = [int(os.environ[name]) for name in names]
busy = []
for port in ports:
    with socket.socket() as connection:
        connection.settimeout(0.2)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            busy.append(port)
if busy:
    raise SystemExit(f"Ports already responding: {busy}. Review port remapping; do not stop unrelated stacks.")
print("No listeners detected on the checked loopback ports; verify effective bindings before up.")
PY
then
  dc up --build -d
else
  printf '%s\n' 'Evaluation startup aborted by port preflight.' >&2
fi
```

This TCP check is not a reservation or proof that every interface is free. If Docker reports a conflict, stop and select available exported port values; do not stop another stack. Recompute `EVAL_URL` after changing `LB_HOST_PORT`, and rerun merged-binding validation and port preflight after any port/overlay change before every start or recreation. A unique project separates containers/volumes, and the overlay separates application image tags; neither alone reserves host ports.

The stack starts the API, web UI, databases, Redis, worker, and load balancer. Init runs migrations and exits. Monitor startup instead of assuming a fixed wait time:

```bash
dc ps -a
curl "$EVAL_URL/health"
```

Open the same `EVAL_URL` in your browser (with the shown LB port, `http://localhost:8080`). The health body should report `status: ok`; HTTP 200 with `status: degraded` is not full health. Check dependency errors and [troubleshooting](/self-hosting/troubleshooting). For this evaluation, translate that page's Compose commands to `dc`: remove its `docker compose` prefix and base `-f docker/docker-compose.yml` flag, retaining only the subcommand/options. `dc` already supplies the recorded project and both files. Use `EVAL_URL` for its URL checks too; base-only commands can inspect or recreate another stack.

## Install a matching CLI

From this checkout:

```bash
uv tool install --editable .
observal --version
```

This aligns the CLI source with the server you just built. It also replaces an existing `uv` tool installation of the same package if `uv` permits the requested change; read any conflict message before forcing a replacement. If you need to preserve another CLI installation, use a separate evaluation environment.

## Sign in as an ordinary user

```bash
observal auth login --server "$EVAL_URL" --no-setup
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

The default configuration does not promise that authors can install pending agents. For this evaluation, open a separate browser session for the demo reviewer account (`reviewer@demo.example`, password `reviewer-changeme`) and follow [Review and publish a submission](/guides/review-and-publish). Inspect **Files changed**, submit an **Approve** verdict through **Review changes**, and check the **Publication gate**. If publication is still required, select **Publish vVERSION** and confirm. Do not treat an approval badge as proof that publication completed.

Return to the ordinary user account and confirm the agent appears in `observal agent list`. Copy its actual qualified name and approved version into the first-agent walkthrough.

Use the registry UI's actual review link or number. Do not assume a fixed review number or that every CLI/server release returns the same review metadata.

## Stop the evaluation

From the recorded checkout, with `EVAL_PROJECT` and the `dc` function/overlay restored:

```bash
dc down
```

This stops the stack and preserves its volumes. Do not add `--volumes` unless you intentionally want to delete this evaluation's database contents and have checked that no valuable data is stored there.

## Next

[Pull your first agent](/getting-started/quickstart), or review [production deployment](/self-hosting/production-deploy) before making the registry available to a team.
