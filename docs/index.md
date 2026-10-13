---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Installation"
---

Observal has two parts: a **server** you self-host and a **CLI** installed on each developer machine.

## Install the server

The server runs as a Docker Compose stack (API, web UI, PostgreSQL, ClickHouse, Redis, worker, load balancer). Prometheus and Grafana are optional deployment overlays.

> \[!NOTE] Requires Docker Engine ≥ 24.0 with Compose v2 (`docker compose`, not `docker-compose`). Homebrew's Docker formula is outdated. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages. Verify with `docker version` and `docker compose version`.

**One-line install:**

```bash
curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install-server.sh | bash
```

This downloads a config package, runs guided setup (domain, secrets, ports), pulls container images from GHCR, and starts the full stack.

**From source** (for contributors):

```bash
git clone https://github.com/Observal/Observal.git && cd Observal
cp .env.example .env
make up
```

For deployment options, see [Self-Hosting](/self-hosting/docker-compose) and [Production deployment](/self-hosting/production-deploy).

## Install the CLI

The CLI is what you use to log in, instrument harness configs, pull agents, and query traces.

## Install (standalone binary)

The standalone binary is the simplest way to install. No Python required.

```bash
curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install.sh | bash
```

This downloads the latest release binary for your platform and places it on your `PATH`.

The installer downloads the release binary and checks its SHA-256 hash against the release checksums when available. If it cannot download the checksums, it warns and proceeds without verification; it does not create a key in `~/.observal/config.json`.

Verify it worked:

```bash
observal --version
```

## Alternative: install with Python

If you prefer to install via Python, use one of these methods. Requires Python 3.11 or newer.

**uv (recommended):**

```bash
uv tool install observal-cli
```

**pipx:**

```bash
pipx install observal-cli
```

**pip:**

```bash
pip install --user observal-cli
```

### Optional extras

The Python package includes the Docker SDK. The `migrate` extra adds `pyarrow` for `observal server migrate`:

```bash
uv tool install 'observal-cli[migrate]'
```

## Install from source (for contributors)

```bash
git clone https://github.com/Observal/Observal.git
cd Observal
uv tool install --editable .
```

## What gets installed

The Python package provides three entry points:

| Command                | Purpose                                                  |
| ---------------------- | -------------------------------------------------------- |
| `observal`             | The main CLI                                             |
| `observal-sandbox-run` | Sandbox runner invoked by Observal sandboxes             |
| `observal-sandbox-mcp` | MCP adapter used by sandbox execution                   |

You will almost never call the sandbox helpers directly. The CLI wires them into your harness config for you.

## Upgrade

```bash
observal self upgrade
```

## Uninstall

Standalone binary:

```bash
rm "$(which observal)"
```

Python install:

```bash
uv tool uninstall observal-cli
# or: pipx uninstall observal-cli
# or: pip uninstall observal-cli
```

Uninstalling the CLI does **not** remove your config (`~/.observal/`). Delete that folder if you want a clean slate:

```bash
rm -rf ~/.observal
```

## Next

-> [Quickstart](/getting-started/quickstart)
