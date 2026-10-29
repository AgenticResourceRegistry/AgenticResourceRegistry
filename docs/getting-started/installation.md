---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Installation"
---

Agentic Resource Registry has two parts: a **server** you self-host and a **CLI** installed on each developer machine.

## Install the server

The server runs as a Docker Compose stack (API, web UI, PostgreSQL, ClickHouse, Redis, worker, load balancer). Prometheus and Grafana are optional deployment overlays.

> [!NOTE]
> Requires Docker Engine ≥ 24.0 with Compose v2 (`docker compose`, not `docker-compose`). Homebrew's Docker formula is outdated. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages. Verify with `docker version` and `docker compose version`.

**One-line install:**

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install-server.sh | bash
```

This downloads an Agentic Resource Registry release package, asks for the frontend URL and bind address, generates secret files, pulls the Agentic Resource Registry container images from GHCR, and starts the stack. With the default answers:

- Open the dashboard at `http://localhost:3000`; set the **Frontend URL** prompt to the address you will actually open in your browser. This configures the frontend/CORS origin, **not** the published port.
- Use `http://localhost:8000` as the CLI server URL. Check the API with `curl http://localhost:8000/health`. If you change the bind address or `API_HOST_PORT`, use the server URL printed by setup instead; the CLI command remains `observal`.
- Log in as `super@demo.example` using the generated password printed by setup (also stored in `secrets/demo_super_admin_password` under the install directory: `~/.observal` on macOS, `/opt/observal` on Linux). Change it after logging in.

Self-registration is off by default. To give someone a personal account after logging in as an administrator, run `observal admin create-user alice@example.com 'Alice Smith' --role user`. See [user management](/cli/admin) and [self-registration settings](/self-hosting/authentication) for other options. The [quickstart](/getting-started/quickstart) uses the **source Compose stack**, which instead serves the API at `http://localhost` and uses example passwords from `.env.example`.

**From source** (for contributors):

```bash
git clone https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git && cd AgenticResourceRegistry
cp .env.example .env
make up
```

For deployment options, see [Self-Hosting](/self-hosting/docker-compose) and [Production deployment](/self-hosting/production-deploy).

## Install the CLI

The CLI is what you use to log in, instrument harness configs, pull agents, and query traces.

## Install (standalone binary)

The standalone binary is the simplest way to install. No Python required.

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.ps1 | iex
```

This downloads the latest release binary for your platform and places it on your `PATH`. On Windows it installs to `%LOCALAPPDATA%\Programs\Observal` and updates your user `PATH` (open a new terminal afterwards). Set `OBSERVAL_VERSION` or `OBSERVAL_BIN_DIR` before running to pin a version or change the install directory.

Connecting to a server your team already runs? See the [Setup guide](/getting-started/onboarding).

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
git clone https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
cd AgenticResourceRegistry
uv tool install --editable .
```

## What gets installed

The Python package provides three entry points:

| Command                | Purpose                                              |
| ---------------------- | ---------------------------------------------------- |
| `observal`             | The main CLI                                         |
| `observal-sandbox-run` | Sandbox runner invoked by Agentic Resource Registry sandboxes         |
| `observal-sandbox-mcp` | MCP adapter used by sandbox execution               |

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
