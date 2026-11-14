---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal self"
---

Inspect or change the installed CLI version.

All four workflows support `--output table|json`. JSON upgrade, downgrade, and rollback require `--force` and never prompt.

## Commands

| Command | Purpose |
| --- | --- |
| `upgrade` | Install the latest or a specified newer CLI release |
| `downgrade` | List releases or install an older release |
| `rollback` | Restore the standalone binary saved before the last version change |
| `status` | Show current version, install method, and update availability |

The destructive uninstall workflow has been removed.

## Automatic sync with the server

A server accepts only a CLI on its own exact version, so the CLI keeps itself there. You do not run a command for this.

| | |
| --- | --- |
| When | At `observal auth login`, before credentials are asked for, and on the first request of any later command that finds a mismatch (for example after the server was upgraded). |
| What | The CLI installs the server's version over itself, an upgrade or a downgrade, with the tool that owns the installation: uv, pipx, pip, or the standalone binary. It then re-runs the command, unless the command already sent a request to the server or read piped input (see Re-run below): then check what it already changed before running it again. An editable or source install is replaced like any other. |
| Prompts | None, and there is no setting to turn it off. Progress is printed to stderr; in JSON mode the sync is silent. |
| Other copies | Every other `observal` on `PATH` is brought to the same version with its own installer. A copy no installer owns, such as a system package, is named on stderr and left alone. |
| Development servers | `X.Y.Z-dev.N` is not on PyPI. The CLI builds the commit the server reports as `build_commit` in `GET /api/v1/config/version` (a server built from a checkout with local changes reports none). For a server that reports none, it finds the newest commit on `main` that still declares that version. Either way the commit must be on `main` or a `release/*` branch and must declare the server's version; if no such commit is found, nothing is installed. This needs `git`; when it is missing the error says so and names the install command for the platform (for example `winget install --id Git.Git -e` on Windows). It takes about a minute. |
| Dependencies | When the server is older than the CLI, dependencies are capped at the versions the CLI already runs with. Older releases declared open-ended dependencies and can break on a dependency release newer than they are. The cap is dropped if the older version cannot be installed under it. |
| Start check | After installing, the CLI loads a command from the new installation. If that fails, the sync is reported as failed instead of re-running into a crash. |
| Windows with uv | The running `observal.exe` cannot be replaced, so the package is updated inside uv's tool environment and the launcher is kept. `uv tool list` shows the new version; uv's record of the install source is refreshed by the next `uv tool install`. |
| Interrupted | The installer runs apart from the command, with its output in files rather than pipes: Ctrl+C waits for it to finish, and a harness that kills the command at its timeout does not stop it. An install that outlasts the CLI's own wait (120 s, 300 s from source) is left running; the command fails and asks you to run it again once it finishes. `uv tool install --force` and `pip install` remove the old CLI before writing the new one, so stopping them part-way would leave no CLI. On Linux and macOS the upgrade lock stays held until the installer finishes. |
| Re-run | A command is re-run only when nothing of it has happened yet. One that already sent a request to the server, or read piped input (for example `observal api POST ... < body.json`, whose re-run would find its input empty), is synced but not re-run: it fails with exit code 10 and asks you to run it again. |
| Older versions | A server older than 1.18 makes the CLI install a version that cannot sync itself. The sync says so; after that server's next upgrade, run `observal self upgrade --version <server version> --force` once. |
| Exempt | `observal self ...`, so a manual version change is never fought, and `observal server ...`, which manages a local server whose version may be changing. Hooks, the delegation runner and MCP servers (including `observal delegate mcp`) never sync: they report the mismatch, and the next `observal` command syncs. |
| Record | `~/.observal/last_sync.json` holds the version that was replaced, the commit the server reported, and the source actually installed. |

The sync runs once per command. If the CLI still does not match afterwards, or the installation cannot be changed (it is managed by apt, dnf or Homebrew, a development build was requested for a standalone binary, or the installer failed), the command fails with exit code 10 and the error states the reason and the command to run.

The server only names a version and a commit. The repository is fixed in the CLI, or set locally with `update_check_repo`. That alone would not stop a server from choosing what code runs: GitHub serves the head commit of every open pull request, and commits that exist only in forks, under the repository's address. So a commit is built only when it is reachable from `main` or a `release/*` branch, which the CLI checks against a commit-only copy of that history in `~/.observal/cache/source.git`. A server can still choose any version or commit that was merged.

## Status

```bash
observal self status --output json
```

JSON returns:

```json
{
  "current_version": "2.4.0",
  "install_method": "uv_tool",
  "path": "/home/user/.local/bin/observal",
  "writable": true,
  "managed_by": "uv",
  "github_available": true,
  "latest_version": "2.5.0",
  "update_available": true
}
```

The command always checks GitHub. An unreachable GitHub service remains a successful local status result with `github_available: false`, `latest_version: null`, and `update_available: null`.

## Upgrade

Install the latest stable release:

```bash
observal self upgrade --force --output json
```

Install a specific release:

```bash
observal self upgrade --version 2.5.0 --force --output json
```

Include prereleases when resolving the latest version:

```bash
observal self upgrade --pre --force --output json
```

A completed JSON result contains the prior version, target version, install method, and executable path. When the current version already matches the target, status is `up_to_date` and no installation occurs.

The command rejects invalid versions and targets older than the current version. System-package installations must be upgraded through their package manager.

## Downgrade

List available releases:

```bash
observal self downgrade --list --output json
```

JSON returns `current_version` and release `items`, each with a `current` boolean.

Install an older release:

```bash
observal self downgrade --version 2.4.0 --force --output json
```

`--list` and `--version` are mutually exclusive. The target must be older than the current version and at least the CLI version floor. System-package installations must use their package manager.

Releases before 1.10.4 are pinned by disabling their legacy automatic-update setting. The JSON result reports this as `automatic_updates_disabled`.

## Rollback

```bash
observal self rollback --force --output json
```

Rollback is available only for standalone binary installations with `~/.observal/bin/observal.prev`. It acquires the same version-change lock as upgrade and downgrade, copies the backup to a temporary file, restores executable permissions, and atomically replaces the current binary.

Package-managed installations must install their previous version through the package manager.

## Installation safety

Upgrade and downgrade:

1. Acquire a process lock so version changes cannot overlap.
2. Use the detected installation method.
3. Verify standalone binary downloads against the published SHA-256 checksum.
4. Replace standalone binaries atomically and retain the previous binary for rollback.
5. Execute the installed binary and verify that it reports the requested version.

Human mode may explicitly confirm an unsigned binary. JSON mode never prompts and refuses standalone installation when the release has no published checksum.

## Exit codes

| Code | Meaning |
| --- | --- |
| 5 | Rollback backup not found |
| 6 | Package-managed install or another version change holds the lock |
| 7 | Invalid version, direction, conflicting mode, or missing `--force` in JSON mode |
| 9 | GitHub, download, installer, checksum, verification, or filesystem failure |

## Related

* [`observal server`](/cli/server): manage server stack versions separately
* [Upgrades](/self-hosting/upgrades): server and CLI upgrade procedures
* [Environment variables](/reference/environment-variables): update-check settings
