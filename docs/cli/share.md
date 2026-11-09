---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal share"
---

Create and consume expiring links for version-pinned Agents installed in a repository. Share links preserve Registry authorization: recipients must sign in to the same Agentic Resource Registry deployment and can only see and pull Agents available in their current public or teamspace scope.

## Create a share

For the teammate-to-teammate journey, use [Share agent configurations](/use-cases/share-agent-configs). The explicit form is:

```bash
observal share create --dir ./service --agent platform-tools/team-reviewer --expires-days 7 --output json
```

Replace the identity with a candidate installed in that repository. JSON creation requires an explicit `--agent` selection or `--all`; it does not prompt. The result includes a secret token/URL, expiry, and item count. `--title` is optional user-supplied metadata; do not put private repository information in it.

Run the command in a repository and select Agents interactively:

```bash
observal share
```

Share every tracked project Agent:

```bash
observal share --all --expires-days 7
```

Select explicit installed Agents:

```bash
observal share --agent platform/reviewer --agent platform/github-helper
```

The printed URL is not a web page. Open it only with `observal share open`; the web UI has no share route yet. The bare token also works if the URL's host does not match your configured server (`deployment.frontend_url`).

Links expire after 7 days by default. The allowed range is 1 through 30 days. Only project-scoped Agents recorded in the current Registry section of `~/.observal/lockfile.json` are candidates. The repository path, Git remote, harness names, configuration, prompts, and credentials are not uploaded.

## Inspect candidates

```bash
observal share candidates
observal share candidates --dir ./service --output json
```

Discovery checks every harness section in the active registry's lockfile section and deduplicates identical Agent/version pairs. An identity selected with `--agent` can match multiple installed versions; the flag is not a version selector. Use interactive pair selection when necessary, and inspect the manifest before installing.

## Open and pull

```bash
observal share open 'https://observal.example/shares/agents/<token>'
```

Agentic Resource Registry validates that the URL belongs to the configured deployment, fetches the manifest using the configured bearer token, and displays its creator and accessible Agents. It does not contact the host contained in the supplied URL. After selection and confirmation, the command asks for a target harness and delegates each installation to `observal agent pull`.

Use `--no-pull` to inspect without installing:

```bash
observal share open <token> --no-pull
```

Only currently accessible, approved Agent versions are returned. Inaccessible entries are reported as a count without exposing their identity. The creator and optional title remain manifest metadata, so the title must not reveal private resources.

`--output json` returns the manifest and never pulls, regardless of `--yes` or `--no-pull`. Its response includes the token: treat JSON as sensitive. In human mode, `--yes` selects every accessible entry without confirmation. The open command delegates exact-version pulls but has no explicit scope, strict-mode, or dry-run passthrough. For a reviewed strict plan, inspect the manifest and use matching explicit-version `agent pull --strict --dry-run` and `agent pull --strict` commands in the target directory. Do not assume an automatic multi-agent pull is atomic.

## Revoke

The creator or an administrator can revoke a link immediately:

```bash
observal share revoke <token>
observal share revoke <token> --yes --output json
```

Expired and revoked links cannot be opened. The server returns HTTP 410; the current CLI maps that to validation exit code 7. Revocation stops future opens, not previously completed installs or disclosure of credentials.

## Security properties

- Public links contain a 256-bit random opaque token, not Agent IDs or repository metadata.
- The server stores only a SHA-256 hash of the token.
- Links do not grant permissions; Registry visibility is evaluated on every open.
- Creation validates every Agent and exact semantic version in one transaction.
- URLs with credentials, query strings, fragments, foreign origins, control characters, or invalid paths are rejected.
- Pull commands use argument arrays rather than a shell, preventing values from becoming shell syntax.
