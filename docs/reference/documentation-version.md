---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Documentation and software versions"
description: "Match these guides to Agentic Resource Registry v1.16.0 and distinguish release evidence from development checks."
---

These guides target **v1.16.0**, the latest stable release selected for this revision, published October 29, 2026. The target is the immutable tag at `66d0d7cadf18847f32546b11b403ab341b72d48b`, not a moving `main` or Docker `latest` alias.

The retargeting checks use the published Python CLI `observal-cli==1.16.0` and exact-version API/web images. Earlier 1.17-development rehearsals remain historical evidence, not v1.16.0 acceptance. Native tools, cloud deployments, identity providers, and untested operating systems retain their stated qualifications.

## Joining an existing registry

Ask your administrator which server release your team runs. Check your CLI with:

```bash
observal --version
```

Login checks CLI/server version compatibility and prints a matching upgrade or downgrade command when needed. Follow that instruction instead of assuming the newest CLI is the right one. For exact options, use the help output of that installed version.

Use `uv tool install 'observal-cli==1.16.0'` for the selected release. Unpinned installers/tool installs can select a newer release later; inspect that version before applying these instructions.

<a id="evaluating-the-development-version"></a>

## Evaluating the selected source tag

The [local source-evaluation guide](/getting-started/local-evaluation) builds the server and installs the CLI from the same checkout. Check out `v1.16.0` explicitly. Do not build an unpinned `main` checkout and call it stable-release evidence.

A source install is for development evaluation, not a reason to upgrade a team server without its administrator's approval.

## Reading examples

An **agent version** such as `1.0.0` belongs to a resource, not the CLI or server. Select and inspect the resource release independently of the platform version. Hook-specification metadata is another distinct version: it records the managed hook layout, not a model or coding-tool release.

These guides distinguish code-checked behavior from completed live verification. Support for a harness does not certify every future version of that external tool.

## v1.16.0 compatibility boundaries

- **Install compatibility:** the release predates automatic declared-harness refusal/inference and has no `--allow-unsupported-harness` pull flag. Inspect support and component capability yourself; success is not proof of compatibility.
- **Credential reporting/TOML:** later development fixes strengthen credential-reflection redaction and structural/concurrent TOML writes. Do not assume those guarantees in v1.16.0. Review configuration, errors, and logs before sharing; use benign fixtures, not real secrets, for reporting probes.
- **Pi/Codex/OpenCode attribution:** later fixes change Pi directory binding, Codex recovery attribution, and OpenCode no-agent delivery. Do not apply the newer attribution contract to historical release sessions.
- **Skill editing:** the release CLI does not automatically carry a revision from `start-edit` into generic skill edits. Preserve explicit revision/version preconditions in folder workflows; do not assume later guarded edit behavior.
- **Review controls:** v1.16.0 has PR-style review, but not the later linked change-request/withdrawal controls. Read the actual gate and revision; resolve advisory conversations deliberately rather than inferring a newer policy/UI.
- **Cloud signing keys:** stock v1.16.0 AWS/GCP API templates use instance/task-private `/tmp/keys`. Require a tested shared/durable key configuration before authenticated team deployment; see the [AWS](/self-hosting/aws-terraform#quickstart) and [GCP](/self-hosting/gcp-terraform#quickstart) prerequisites.
- **Redis recovery:** the backup recipe requires a separately validated Redis recovery/queue procedure and revocation reconciliation before restored APIs are exposed; missing revocation markers can re-enable old tokens. See [recovery](/self-hosting/backup-and-restore).
- **Server archive startup:** the artifact rehearsal used explicit file-backed credentials, generated ClickHouse password-hash XML, and a separate user-setup override for the read-only configuration directory. This is not an unmodified installer/deployment certification; [inspect the packaged startup checkpoint](/self-hosting/docker-compose#packaged-image-checkpoint).
- **Standalone macOS artifact:** `--version` succeeds, but ordinary commands in an isolated home failed bundled-skill synchronization because the release binary lacks those resources. Use the pinned Python distribution for the verified workflow. Other standalone platforms were not certified by that macOS check.

The [pin-response limitation](/guides/version-components#known-install-response-limitation), [Inbox filter limit](/cli/inbox#list-items), retention and platform qualifications remain separate product issues, not fixes made by this documentation.

## Publication boundary

The software target is selected; Mintlify publishing configuration and human acceptance/publication authorization are still required. Core release-artifact checks do not certify every provider/harness, historical migration, cloud rollout, or full test suite. Do not infer that this draft has been merged or deployed.
