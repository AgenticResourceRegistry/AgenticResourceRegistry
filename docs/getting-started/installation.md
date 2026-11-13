---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Install the CLI"
description: "Install the observal command on your machine and verify it works."
---

The `observal` CLI connects your machine to Agentic Resource Registry. Use it to browse resources, pull agents, and manage what you have installed.

Installing the CLI does **not** deploy a registry server. To evaluate a server locally, use [Try a registry locally](/getting-started/local-evaluation). For a team deployment, use [Self-hosting](/self-hosting/index).

<Note>
  These guides target v1.17.0. Install that exact release below; unpinned installers install the latest release, which can differ from these guides. [Check the version guidance](/reference/documentation-version) before choosing an installation.
</Note>

## Recommended: install the v1.17.0 Python CLI

Requires Python 3.11 or newer and uv:

```bash
uv tool install 'observal-cli==1.17.0' --with 'typer<0.29'
observal --version
```

Expect `observal 1.17.0`. This is the published distribution, not a source install. Each coding tool still needs its own authentication.

<Warning>
  Typer 0.29.0 and later break the published v1.17.0 package: without the constraint every command fails with `AttributeError: 'TyperOption' object has no attribute 'type'`, because the package does not cap Typer. Keep `--with 'typer<0.29'` until a release that fixes the dependency range. The standalone binary below bundles its dependencies and is not affected.
</Warning>

## Install a standalone binary

The standalone binary does not require Python. The v1.17.0 macOS ARM64 binary and installer were checked in an isolated home: the installer verified the release checksum, and `observal --version` and `observal config path --output json` both succeeded. The Linux and Windows binaries were not exercised for these guides. Choose your operating system:

<Tabs>
  <Tab title="macOS / Linux">

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/v1.17.0/install.sh -o install-observal.sh
# Review the downloaded script before executing it.
bash install-observal.sh --version 1.17.0
```

Without a version selector, the installer defaults to the latest release. The shown invocation explicitly selects **1.17.0**; `/usr/local/bin` is the default installation directory. Use `--bin-dir` for a reviewed writable location. A missing `--bin-dir` directory is created, using `sudo` only if needed.

  </Tab>
  <Tab title="Windows">

Run in PowerShell:

```powershell
$env:OBSERVAL_VERSION = '1.17.0'
irm https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/v1.17.0/install.ps1 | iex
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

<a id="alternative-install-with-python"></a>

## Other Python installation options

If your environment uses pipx, install `pipx install 'observal-cli==1.17.0' --pip-args='typer<0.29'`. The recommended uv path above is the same published CLI; do not repeat installation into multiple environments without reviewing conflicts. Prefer isolated tools over application dependencies.

The optional `migrate` extra adds dependencies for the server data-migration commands; it is not needed to pull agents:

```bash
uv tool install 'observal-cli[migrate]==1.17.0' --with 'typer<0.29'
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
