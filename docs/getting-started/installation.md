---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Install the CLI"
description: "Install the observal command on your machine and verify it works."
---

The `observal` CLI connects your machine to Agentic Resource Registry. Use it to browse resources, pull agents, and manage what you have installed.

Installing the CLI does **not** deploy a registry server. To evaluate a server locally, use [Try a registry locally](/getting-started/local-evaluation). For a team deployment, use [Self-hosting](/self-hosting/index).

<Note>
  The installers below default to published releases, not necessarily the 1.17 development revision described by these guides. [Check the version guidance](/reference/documentation-version) before choosing an installation.
</Note>

## Install a standalone binary

The standalone binary does not require Python. Choose your operating system:

<Tabs>
  <Tab title="macOS / Linux">

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.sh | bash
```

The installer downloads the latest release for your platform and installs it to `/usr/local/bin` by default. To choose a version or directory, use the installer's `--version` and `--bin-dir` options.

  </Tab>
  <Tab title="Windows">

Run in PowerShell:

```powershell
irm https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.ps1 | iex
```

The installer uses `%LOCALAPPDATA%\Programs\Observal` by default and updates your user `PATH`. Open a new terminal after installation. Set `OBSERVAL_VERSION` or `OBSERVAL_BIN_DIR` before running to choose a version or directory.

  </Tab>
</Tabs>

<Warning>
  These commands download and execute an installer. Review the script first if your security policy requires it. The installers check release checksums when available, but warn and can proceed without verification if checksums cannot be downloaded. See [release verification](/security/release-verification) for stronger verification steps.
</Warning>

## Verify the installation

```bash
observal --version
```

You should see an `observal` version. If your registry requires a different CLI version, login prints the command to install it.

## Alternative: install with Python

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install observal-cli
```

You can also use `pipx install observal-cli`. Prefer an isolated tool install over adding CLI dependencies to an application environment.

The optional `migrate` extra adds dependencies for the server data-migration commands; it is not needed to pull agents:

```bash
uv tool install 'observal-cli[migrate]'
```

### Install from a checkout

For local source evaluation or contribution, run this from the repository root:

```bash
uv tool install --editable .
```

Use a checkout matching your server. A development server may not match the latest published CLI release.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| Command not found after installation | Open a new terminal. Check that the install directory is on `PATH`. |
| Cannot write to `/usr/local/bin` | Choose a writable directory with the Unix installer's `--bin-dir` option, then add it to `PATH`. |
| No binary for your platform | Use the Python installation method rather than assuming every OS/architecture has a release binary. |
| Login reports a version mismatch | Use the exact matching version requested by the server; a newer CLI is not necessarily compatible. |

## Upgrade

```bash
observal self upgrade
```

For a pinned team deployment, follow your administrator's version instructions rather than upgrading independently. See [CLI version management](/cli/self).

## Uninstall

For a Python tool install:

```bash
uv tool uninstall observal-cli
# If installed with pipx instead:
pipx uninstall observal-cli
```

For a standalone install, remove the `observal` executable from the directory where you installed it. On macOS/Linux, `command -v observal` shows the executable path. On Windows, use `Get-Command observal`.

Removing the CLI does not sign you out remotely or remove generated harness files. [Logout](/cli/auth#logout) and [harness cleanup](/cli/doctor) are separate operations. Keep the CLI installed until you have completed any cleanup you need.

## Next

[Join your team's registry](/getting-started/onboarding), or [try a registry locally](/getting-started/local-evaluation).
