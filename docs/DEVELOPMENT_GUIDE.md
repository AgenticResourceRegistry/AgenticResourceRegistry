<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Development Guide

> [!IMPORTANT]
> **Discord is the primary place to ask questions.** Join at [discord.observal.io](https://discord.observal.io).
>
> * **#contributing**, setup help, workflow questions, anything about the contribution process
> * **#bug**, discuss bugs before filing a GitHub issue
> * **#feature-requests**, pitch ideas and get early feedback before writing code
>
> GitHub issues and PRs are for concrete, actionable items. Exploratory discussion belongs on Discord first.

> Parts of this guide structure were inspired by the [AnkiDroid Development Guide](https://github.com/ankidroid/Anki-Android/wiki/Development-Guide). Attribution given with thanks, check them out if you want another welcoming OSS project to contribute to.

***

## Table of Contents

* [Community Standards](DEVELOPMENT_GUIDE.md#community-standards)
* [Prerequisites](DEVELOPMENT_GUIDE.md#prerequisites)
* [Git Setup](DEVELOPMENT_GUIDE.md#git-setup)
  * [SSH authentication](DEVELOPMENT_GUIDE.md#ssh-authentication)
  * [Git identity](DEVELOPMENT_GUIDE.md#git-identity)
  * [Fork and clone](DEVELOPMENT_GUIDE.md#fork-and-clone)
* [First-time Setup](DEVELOPMENT_GUIDE.md#first-time-setup)
  * [Install pre-commit hooks](DEVELOPMENT_GUIDE.md#install-pre-commit-hooks)
  * [Start the Docker stack](DEVELOPMENT_GUIDE.md#start-the-docker-stack)
  * [Install the CLI](DEVELOPMENT_GUIDE.md#install-the-cli)
  * [Demo accounts](DEVELOPMENT_GUIDE.md#demo-accounts)
* [Make Targets Reference](DEVELOPMENT_GUIDE.md#make-targets-reference)
* [Architecture Overview](DEVELOPMENT_GUIDE.md#architecture-overview)
* [Git Workflow](DEVELOPMENT_GUIDE.md#git-workflow)
  * [Making a new branch](DEVELOPMENT_GUIDE.md#making-a-new-branch)
  * [Keeping your branch up to date](DEVELOPMENT_GUIDE.md#keeping-your-branch-up-to-date)
  * [Dealing with merge conflicts](DEVELOPMENT_GUIDE.md#dealing-with-merge-conflicts)
  * [Submitting a pull request](DEVELOPMENT_GUIDE.md#submitting-a-pull-request)
  * [After review feedback](DEVELOPMENT_GUIDE.md#after-review-feedback)
* [Working on the Backend](DEVELOPMENT_GUIDE.md#working-on-the-backend)
  * [Running tests](DEVELOPMENT_GUIDE.md#running-tests)
  * [Testing conventions](DEVELOPMENT_GUIDE.md#testing-conventions)
  * [Running a single test](DEVELOPMENT_GUIDE.md#running-a-single-test)
  * [Code coverage](DEVELOPMENT_GUIDE.md#code-coverage)
  * [Adding a database migration](DEVELOPMENT_GUIDE.md#adding-a-database-migration)
  * [Connecting to PostgreSQL directly](DEVELOPMENT_GUIDE.md#connecting-to-postgresql-directly)
  * [Connecting to ClickHouse directly](DEVELOPMENT_GUIDE.md#connecting-to-clickhouse-directly)
  * [Debugging the API](DEVELOPMENT_GUIDE.md#debugging-the-api)
* [Working on the Frontend](DEVELOPMENT_GUIDE.md#working-on-the-frontend)
  * [Running the frontend in isolation](DEVELOPMENT_GUIDE.md#running-the-frontend-in-isolation)
  * [Design system](DEVELOPMENT_GUIDE.md#design-system)
  * [Adding a new API endpoint to the frontend](DEVELOPMENT_GUIDE.md#adding-a-new-api-endpoint-to-the-frontend)
  * [Screenshots for UI changes](DEVELOPMENT_GUIDE.md#screenshots-for-ui-changes)
* [Working on the CLI](DEVELOPMENT_GUIDE.md#working-on-the-cli)
  * [Reinstalling after changes](DEVELOPMENT_GUIDE.md#reinstalling-after-changes)
* [Harness Recommendations](DEVELOPMENT_GUIDE.md#harness-recommendations)
  * [VS Code](DEVELOPMENT_GUIDE.md#vs-code)
  * [PyCharm / IntelliJ](DEVELOPMENT_GUIDE.md#pycharm--intellij)
* [Pre-commit Hooks](DEVELOPMENT_GUIDE.md#pre-commit-hooks)
* [Pull Request Checklist](DEVELOPMENT_GUIDE.md#pull-request-checklist)
* [Getting Help](DEVELOPMENT_GUIDE.md#getting-help)

***

## Community Standards

We are a small, active community and we take the quality of interactions seriously.

> [!WARNING]
> The following will result in a **moderator warning**. A second violation results in a **temporary or permanent ban** from the repository and Discord:
>
> * Pinging contributors, maintainers, or reviewers unnecessarily (outside of a direct reply on your own open PR or issue)
> * Submitting low-effort or unreviewed PRs (slop), including unreviewed AI output or autonomous agent submissions
> * Violating the [Code of Conduct](https://github.com/Observal/Observal/blob/main/CODE_OF_CONDUCT.md) in any channel
> * Harassing reviewers over merge timelines

Maintainers volunteer their time. Treat them accordingly.

### A note on autonomous coding agents

**Autonomous coding agents (Devin, SWE-agent, OpenHands, etc.) are not permitted to submit PRs.** This is a legal constraint, not a quality judgment. The US Copyright Office's 2025 guidance confirms that purely AI-generated code has no copyright owner, which breaks our CLA and our ability to license and enforce the project. See the [AI Policy](../AI_POLICY.md) for the full explanation.

***

## Prerequisites

| Tool    | Version             | Install                                                                   |
| ------- | ------------------- | ------------------------------------------------------------------------- |
| Docker  | 24+ with Compose v2 | [docs.docker.com](https://docs.docker.com/get-docker/)                    |
| uv      | latest              | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                        |
| Node.js | 22.6+               | Via [nvm](https://github.com/nvm-sh/nvm) or [mise](https://mise.jdx.dev/) |
| pnpm    | 10+                 | `npm install -g pnpm`                                                     |
| Git     | 2.28+               | [git-scm.com](https://git-scm.com/)                                       |

Verify your Docker installation supports Compose v2:

```bash
docker compose version
# should print: Docker Compose version v2.x.x
```

If you see `docker-compose: command not found` or a v1 version, upgrade Docker Desktop or install the Compose plugin separately.

***

## Git Setup

### SSH authentication

Using SSH is strongly recommended, it avoids password prompts and is required if you enable 2FA on GitHub.

```bash
# Generate a key (skip if you already have one at ~/.ssh/id_ed25519)
ssh-keygen -t ed25519 -C "your@email.com"

# Copy the public key to your clipboard
cat ~/.ssh/id_ed25519.pub
```

Add the output to **GitHub → Settings → SSH and GPG keys → New SSH key**.

Test it:

```bash
ssh -T git@github.com
# Hi your-username! You've successfully authenticated...
```

### Git identity

Set your identity so it matches your GitHub account:

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

If you want to sign commits with GPG as well (optional but appreciated):

```bash
git config --global commit.gpgsign true
git config --global user.signingkey YOUR_GPG_KEY_ID
```

### Fork and clone

You only need to do this once. For every subsequent contribution, just create a new branch on your existing fork.

1. Fork the repository on GitHub (top-right **Fork** button).
2. Clone your fork using SSH:

```bash
git clone git@github.com:YOUR-USERNAME/Observal.git
cd Observal
```

3. Add the upstream remote so you can pull in changes from the main repo:

```bash
git remote add upstream https://github.com/Observal/Observal.git
```

4. Verify your remotes:

```bash
git remote -v
# origin    git@github.com:YOUR-USERNAME/Observal.git (fetch)
# origin    git@github.com:YOUR-USERNAME/Observal.git (push)
# upstream  https://github.com/Observal/Observal.git (fetch)
# upstream  https://github.com/Observal/Observal.git (push)
```

***

## First-time Setup

### Install pre-commit hooks

Do this **before your first commit**. The hooks run ruff, format checkers, secret scanners, SPDX header injection, and migration chain validation automatically.

```bash
make hooks
```

### Start the Docker stack

```bash
cp .env.example .env
make up
```

The first build takes several minutes while Docker downloads and compiles images. Subsequent starts are fast.

Watch progress with:

```bash
make logs
```

For code, dependency, and Compose changes, use:

```bash
make rebuild
```

`make rebuild-fast` is the same target under another name. It is not a separate dev profile and does not enable hot reload. It builds only the two app images that contain local code:

* `observal-api`, shared by the API, init, and worker services
* `observal-web`, used by the web service

Then it starts the full stack with the freshly built images.

Wait until all services are healthy:

```bash
docker compose -f docker/docker-compose.yml ps
# All services should show "healthy" or "running"
```

**Default endpoints:**

| Service              | URL                     |
| -------------------- | ----------------------- |
| LB (all traffic)     | `http://localhost`      |
| Web UI (direct)      | `http://localhost:3000` |
| Prometheus, optional | `http://localhost:9090` |
| Grafana, optional    | `http://localhost:3001` |
| ClickHouse HTTP      | `http://localhost:8123` |

### Install the CLI

```bash
uv tool install --editable .
```

### Log in

```bash
observal auth login
```

On a fresh server, this bootstraps the admin account automatically. Use `super@demo.example` / `super-changeme` for the super\_admin account.

### Demo accounts

Seeded automatically on first startup:

| Email                | Password         | Role         |
| -------------------- | ---------------- | ------------ |
| `super@demo.example` | `super-changeme` | super\_admin |
| `admin@demo.example` | `admin-changeme` | admin        |
| `dev@demo.example`   | `dev-changeme`   | developer    |
| `user@demo.example`  | `user-changeme`  | user         |

***

## Make Targets Reference

```bash
make up            # start the full Docker stack
make down          # stop the stack
make rebuild       # rebuild app images and restart (alias: make rebuild-fast)
make logs          # tail logs from all services
make test          # run the test suite quickly
make test-v        # verbose test output
make lint          # ruff check + hadolint
make format        # ruff format (auto-fix)
make check         # pre-commit on all files
make hooks         # install pre-commit hooks
make reset         # nuke all volumes and rebuild from scratch (destructive)
```

### Which rebuild target should I use?

| Situation | Target | Notes |
| --- | --- | --- |
| Any code, dependency, schema, init, worker, or Compose change | `make rebuild` | `make rebuild-fast` is the same target. Rebuilds the shared API image (API, init, worker) and the web image, then recreates changed services. |
| Cache looks stale or the stack behaves unexpectedly | `make rebuild-clean` | Rebuilds from scratch without the Docker cache and removes volumes. |

`make down` stops containers started by `make rebuild`.

***

## Architecture Overview

Observal is a monorepo:

```
observal-server/    FastAPI backend (Python)
observal_cli/       CLI and session exporters (Python)
web/                Next.js 16 / React 19 frontend (TypeScript)
tests/              Shared test suite (~1500 tests, 96 files)
docker/             Docker Compose and Dockerfiles
docs/               Documentation
scripts/            Dev tooling scripts
```

**Databases:**

* **PostgreSQL**, relational data (users, agents, registry, feedback)
* **ClickHouse**, session events, aggregates, audit events, and security events

They are not interchangeable. Never write telemetry to Postgres or relational data to ClickHouse.

**Supporting services:** Redis (pub/sub + arq job queue), arq worker, nginx reverse proxy. Prometheus and Grafana are optional.

See [AGENTS.md](https://github.com/Observal/Observal/blob/main/AGENTS.md) for a complete map of every important file and service.

***

## Git Workflow

### Making a new branch

Always branch from the latest `main`. Never commit directly to `main`.

```bash
git checkout main
git pull upstream main
git checkout -b feature/my-feature
# or: fix/my-bug, docs/my-doc
```

### Keeping your branch up to date

If `main` has moved forward while you were working, rebase your branch onto it:

```bash
git checkout main
git pull upstream main
git checkout feature/my-feature
git rebase main
```

Rebasing keeps history linear and makes your PR easier to review than a merge commit would.

### Dealing with merge conflicts

Conflicts arise when the same file was changed in both `main` and your branch. During `git rebase main`, git will pause and show you:

```
CONFLICT (content): Merge conflict in some/file.py
```

Open the file and look for the three Git conflict-marker lines: seven less-than signs followed by `HEAD`, seven equals signs, and seven greater-than signs followed by the other branch name. The text between them contains the competing changes.

Edit the file to the correct final state (keeping whichever changes are right, or combining them), then:

```bash
git add some/file.py
git rebase --continue
```

If you get confused and want to start over:

```bash
git rebase --abort
```

After resolving and pushing, use `--force-with-lease` rather than `--force`, it's safer:

```bash
git push origin feature/my-feature --force-with-lease
```

### Submitting a pull request

1. Make sure your branch is rebased on the latest `main` (see above).
2.  Push to your fork:

    ```bash
    git push origin feature/my-feature
    ```
3. GitHub will show a banner on your fork offering to open a PR. Click it, or go to the [Observal repository](https://github.com/Observal/Observal) directly.
4. Fill in the PR template completely. PRs that do not follow the template will be closed.
5. Link the related issue if one exists (`Fixes #123` in the PR body closes it automatically on merge).

### After review feedback

If a reviewer requests changes, make them on the same branch and push again. Do not open a new PR. If your changes are small fixes to an existing commit, amend rather than adding new commits:

```bash
git add the-changed-file.py
git commit --amend --no-edit
git push origin feature/my-feature --force-with-lease
```

***

## Working on the Backend

### Running tests

Tests mock all external services. Docker does not need to be running.

```bash
make test
make test-v
make test-eval-completeness
make test-adversarial
make test-all
# or directly:
cd observal-server
uv run --with pytest --with pytest-asyncio --with hypothesis --with pyarrow pytest ../tests/ -q
```

### Testing conventions

New Python tests should follow the [Testing Guide](testing/Testing_Guide.md). The current suite has mixed historical patterns, so do not rewrite old tests only for style. When adding or touching tests, prefer the clean pattern documented there: one behavior area per file, small local helper factories, hermetic CLI and API test setup, explicit async mocks, and behavior-focused assertions.

### Running a single test

```bash
cd observal-server
uv run pytest ../tests/test_registry_types.py -q
# or a specific test function:
uv run pytest ../tests/test_registry_types.py::TestMcpRoutes::test_submit_mcp -v
```

### Code coverage

Coverage is collected automatically on the `3.13` matrix run in CI. To generate it locally:

```bash
cd observal-server
uv run pytest ../tests/ --cov=../observal_cli --cov=. --cov-report=html -q
# open htmlcov/index.html in your browser
```

### Adding a database migration

```bash
bash scripts/new_migration.sh "describe_your_change"
```

Edit the generated file in `observal-server/alembic/versions/`. Then verify the chain is intact:

```bash
python3 scripts/check_migrations.py
```

> [!CAUTION]
> Never edit an existing migration file. Always create a new one. A broken migration chain blocks CI and prevents the server from starting.

Apply migrations to your local stack:

```bash
docker compose -f docker/docker-compose.yml run --rm init
```

### Connecting to PostgreSQL directly

```bash
docker compose -f docker/docker-compose.yml exec db \
  psql -U observal -d observal
```

Useful for inspecting tables, running manual queries, or verifying migration results.

### Connecting to ClickHouse directly

ClickHouse exposes an HTTP interface. Use the Play UI in your browser:

```
http://localhost:8123/play
```

Or query from the terminal:

```bash
curl -s "http://localhost:8123/?query=SELECT+count()+FROM+session_events" \
  -u "default:"
```

Or connect with the CLI client:

```bash
docker compose -f docker/docker-compose.yml exec clickhouse \
  clickhouse-client --user default
```

### Debugging the API

The API server runs with `--reload` in development mode. Log output is available via:

```bash
docker compose -f docker/docker-compose.yml logs -f observal-api
```

To add a breakpoint, use `breakpoint()` in Python code, the debugger will pause in the container's stdout. Or attach a remote debugger by adding `debugpy` to your dev dependencies and exposing a debug port.

The OpenAPI docs are available at (API port, not through the LB which blocks these paths):

* `http://localhost:8000/docs` (Swagger UI, requires direct API port access)
* `http://localhost:8000/redoc` (ReDoc, requires direct API port access)

> Note: The nginx LB blocks `/docs`, `/redoc`, and `/openapi.json` in production. For local dev, expose the API port directly or use `docker compose exec`.

***

## Working on the Frontend

### Running the frontend in isolation

```bash
cd web
pnpm install
pnpm dev
```

Create `web/.env.local` with:

```bash
NEXT_PUBLIC_API_URL=http://localhost
```

The frontend proxies all `/api/v1/*` calls to the backend URL set by `NEXT_PUBLIC_API_URL`.

### Design system

| Token         | Value                                           |
| ------------- | ----------------------------------------------- |
| Color space   | OKLCH                                           |
| Themes        | `light`, `dark`, `midnight`, `forest`, `sunset` |
| Display font  | Archivo                                         |
| Body font     | Albert Sans                                     |
| Code font     | JetBrains Mono                                  |
| Spacing base  | 4pt                                             |
| Components    | shadcn/ui                                       |
| Charts        | Recharts                                        |
| Data fetching | TanStack Query                                  |
| Tables        | TanStack Table                                  |

All themes are defined in `web/src/app/globals.css` as CSS custom properties. Semantic tokens (`background`, `foreground`, `card`, `border`, `primary`, `secondary`, `accent`, `destructive`, `success`, `warning`, `info`) map to OKLCH values per theme.

When adding new UI, use the semantic tokens, never hardcode colors. Check all five themes look correct before submitting.

### Adding a new API endpoint to the frontend

1. Add the TypeScript response type to `web/src/lib/types.ts`.
2. Add the fetch call to `web/src/lib/api.ts`.
3. Add the TanStack Query hook to `web/src/hooks/use-api.ts`.
4. Use the hook in your component.

### Screenshots for UI changes

> [!IMPORTANT]
> Any PR that touches the web frontend must include **screenshots of all affected screens** in the PR description. This is required regardless of how small the change is. Attach screenshots directly to the PR body, not as review comments.

***

## Working on the CLI

### Reinstalling after changes

The CLI is installed as a uv tool. Changes to source files are reflected immediately because it is installed in editable mode (`--editable`). If you add new entry points or change `pyproject.toml`, reinstall:

```bash
uv tool install --editable . --reinstall
```

### Testing session delivery

Run a harness session with hooks installed, then reconcile and inspect exporter status:

```bash
observal reconcile --dry-run
observal auth status
observal ops telemetry status
```

Session records remain in the local outbox until the server acknowledges them.

***

## Harness Recommendations

### VS Code

Install these extensions for the best experience:

* **Python** (`ms-python.python`), LSP, type checking
* **Pylance** (`ms-python.vscode-pylance`), fast type inference
* **Ruff** (`charliermarsh.ruff`), linting and formatting on save
* **ESLint** (`dbaeumer.vscode-eslint`), JavaScript/TypeScript linting
* **Prettier** (`esbenp.prettier-vscode`), TypeScript formatting
* **Docker** (`ms-azuretools.vscode-docker`), container management

Recommended `settings.json` additions:

```json
{
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true
    },
    "[typescript]": {
        "editor.defaultFormatter": "esbenp.prettier-vscode",
        "editor.formatOnSave": true
    },
    "python.analysis.typeCheckingMode": "basic"
}
```

### PyCharm / IntelliJ

1. Open the project root as a PyCharm project.
2. Set the Python interpreter to the uv-managed venv: `observal-server/.venv/bin/python`.
3. Mark `observal-server` as the sources root.
4. Install the **Ruff** plugin for in-editor linting.
5. Enable **File Watchers** to run `ruff format` on save.

***

## Pre-commit Hooks

Install once with `make hooks`. The hooks run automatically on every `git commit`:

| Hook                                       | What it does                                            |
| ------------------------------------------ | ------------------------------------------------------- |
| `ruff`                                     | Lints Python, auto-fixes what it can                    |
| `ruff-format`                              | Formats Python in place                                 |
| `trailing-whitespace`                      | Strips trailing whitespace                              |
| `end-of-file-fixer`                        | Ensures files end with a newline                        |
| `check-yaml` / `check-toml` / `check-json` | Validates config file syntax                            |
| `check-added-large-files`                  | Blocks files over 500KB                                 |
| `check-merge-conflict`                     | Blocks leftover `<<<<<<<` markers                       |
| `detect-private-key`                       | Scans for private key material                          |
| `no-commit-to-branch`                      | Blocks direct commits to `main`                         |
| `check-secrets`                            | Scans staged content for API keys, tokens, `.env` files |
| `check-migrations`                         | Validates Alembic migration chain integrity             |
| `spdx-update`                              | Adds your SPDX copyright line to staged files           |
| `hadolint-docker`                          | Lints Dockerfiles                                       |

If a hook fails, fix the reported issue and commit again. To bypass in an emergency (not recommended):

```bash
git commit --no-verify -m "your message"
```

***

## Pull Request Checklist

Before opening a PR:

* [ ] `make test` passes locally
* [ ] `make lint` passes with no errors
* [ ] Branch is rebased on the latest `main`
* [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/) format
* [ ] All commits are signed off with SPDX headers (pre-commit hook handles this automatically)
* [ ] PR template is filled in completely and reviewed by the accountable human contributor
* [ ] CHANGELOG.md updated under `[Unreleased]` for user-facing changes
* [ ] Frontend changes include screenshots in the PR body
* [ ] AI-assisted contributions labelled with tool name and version (see [AI Policy](../AI_POLICY.md))
* [ ] No unattended agent submissions without meaningful human direction and review (see [AI Policy](../AI_POLICY.md))

***

## Getting Help

If you are stuck, the best place to ask is **#contributing** on [Discord](https://discord.observal.io). Search the channel history first, most common questions have been answered there already.

For bugs use **#bug**. For feature ideas use **#feature-requests**.

> [!NOTE]
> Do not open a GitHub issue just to ask a question. Issues are for confirmed bugs and accepted feature requests. Questions belong on Discord.
