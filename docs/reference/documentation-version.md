---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Documentation and software versions"
description: "Match these guides to Agentic Resource Registry v1.17.0 and distinguish release evidence from development checks."
---

These guides target **v1.17.0**, the stable release selected for this revision, published November 11, 2026. The target is the immutable tag at `9f0e0dfdff7af976bcc0d89a61aad899ad1730b5`, not a moving `main` or Docker `latest` alias.

The retargeting checks used the published release artifacts: the Python CLI `observal-cli==1.17.0`, the macOS ARM64 standalone binary and its installer, and the server archive, with checksums compared against the release's `checksums.txt`. Native tools, cloud deployments, identity providers, and untested operating systems retain their stated qualifications.

## Joining an existing registry

Ask your administrator which server release your team runs. Check your CLI with:

```bash
observal --version
```

Login checks CLI/server version compatibility and prints a matching upgrade or downgrade command when needed. Follow that instruction instead of assuming the newest CLI is the right one. For exact options, use the help output of that installed version.

Use `uv tool install 'observal-cli==1.17.0' --with 'typer<0.29'` for the selected release (see the Typer note below). Unpinned installers/tool installs can select a newer release later; inspect that version before applying these instructions.

<a id="evaluating-the-development-version"></a>

## Evaluating the selected source tag

The [local source-evaluation guide](/getting-started/local-evaluation) builds the server and installs the CLI from the same checkout. Check out `v1.17.0` explicitly. Do not build an unpinned `main` checkout and call it stable-release evidence.

A source install is for development evaluation, not a reason to upgrade a team server without its administrator's approval.

## Reading examples

An **agent version** such as `1.0.0` belongs to a resource, not the CLI or server. Select and inspect the resource release independently of the platform version. Hook-specification metadata is another distinct version: it records the managed hook layout, not a model or coding-tool release.

These guides distinguish code-checked behavior from completed live verification. Support for a harness does not certify every future version of that external tool.

## v1.17.0 compatibility boundaries

- **Python package and Typer:** the published package does not cap Typer, and Typer 0.29.0 and later make every command fail with `AttributeError: 'TyperOption' object has no attribute 'type'`. Install with `--with 'typer<0.29'` (uv) or `--pip-args='typer<0.29'` (pipx), or use the standalone binary.
- **Review controls:** edit locks, unresolved conversations, and open change requests are advisory. Approval is not bound to the revision you inspected and the approval response carries no revision, so read the actual gate and head revision and resolve conversations deliberately; see [review and publish](/guides/review-and-publish).
- **Cloud signing keys:** the stock AWS/GCP API templates use instance/task-private `/tmp/keys`, and `image_tag` defaults to `latest`. Require a tested shared/durable key configuration and an exact image tag before authenticated team deployment; see the [AWS](/self-hosting/aws-terraform#quickstart) and [GCP](/self-hosting/gcp-terraform#quickstart) prerequisites.
- **Redis recovery:** the backup recipe requires a separately validated Redis recovery/queue procedure and revocation reconciliation before restored APIs are exposed; missing revocation markers can re-enable old tokens. See [recovery](/self-hosting/backup-and-restore).
- **Server archive startup:** in an isolated check, the release archive's `setup.sh` ran under macOS Bash 3.2 with a stand-in `docker` and the packaged ClickHouse service started healthy from the generated credentials. Starting the API, web, and worker images was not repeated; this is not an installer or production deployment certification. See the [packaged-server check](/self-hosting/docker-compose#packaged-image-checkpoint).

The [pin-response limitation](/guides/version-components#known-install-response-limitation), [Inbox filter limit](/cli/inbox#list-items), retention and platform qualifications remain separate product issues, not fixes made by this documentation.

## Publication boundary

The software target is selected; Mintlify publishing configuration and human acceptance/publication authorization are still required. Core release-artifact checks do not certify every provider/harness, historical migration, cloud rollout, or full test suite. Do not infer that this draft has been merged or deployed.
