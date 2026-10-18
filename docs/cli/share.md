---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "observal share"
---

Create and consume expiring links for version-pinned Agents installed in a repository. Share links preserve Registry authorization: recipients must sign in to the same Observal deployment and can only see and pull Agents available in their current public or teamspace scope.

## Create a share

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

Discovery checks every harness section in the lockfile and deduplicates identical Agent/version pairs.

## Open and pull

```bash
observal share open 'https://observal.example/shares/agents/<token>'
```

Observal validates that the URL belongs to the configured deployment, fetches the manifest using the configured bearer token, and displays its creator and accessible Agents. It does not contact the host contained in the supplied URL. After selection and confirmation, the command asks for a target harness and delegates each installation to `observal agent pull`.

Use `--no-pull` to inspect without installing:

```bash
observal share open <token> --no-pull
```

Only currently accessible, approved Agent versions are returned. Inaccessible entries are reported as a count without exposing their identity.

## Revoke

The creator or an administrator can revoke a link immediately:

```bash
observal share revoke <token>
observal share revoke <token> --yes --output json
```

Expired and revoked links cannot be opened.

## Security properties

- Public links contain a 256-bit random opaque token, not Agent IDs or repository metadata.
- The server stores only a SHA-256 hash of the token.
- Links do not grant permissions; Registry visibility is evaluated on every open.
- Creation validates every Agent and exact semantic version in one transaction.
- URLs with credentials, query strings, fragments, foreign origins, control characters, or invalid paths are rejected.
- Pull commands use argument arrays rather than a shell, preventing values from becoming shell syntax.
