<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Governance and identity

## Contents

- Settings and diagnostics
- Users
- Review queue
- Recommendations
- Security and audit
- SAML and SCIM

## Settings and diagnostics

```bash
observal admin settings --output json
observal admin set KEY VALUE --output json
observal admin diagnostics --output json
observal admin trace-privacy --output json
observal admin trace-privacy-set true --output json
observal admin cache-clear --output json
```

Read current settings before mutation. The server redacts sensitive values, and update output must not echo supplied secrets.

## Users

```bash
observal admin users --output json
observal admin create-user user@example.com 'Example User' --role user --output json
observal admin reset-password user@example.com --generate --output json
observal admin set-role user@example.com admin --output json
observal admin delete-user user@example.com --force --output json
```

Valid roles are `super_admin`, `admin`, `reviewer`, and `user`. Creation and reset can return a one-time password. Treat the entire response as secret and disclose it only to the intended user when explicitly requested. Verify role changes with the user list.

## Review queue

List and select by UUID:

```bash
observal admin review list --output json
observal admin review list --type mcp --output json
observal admin review list --tab agents --output json
observal admin review list --team-id TEAM_UUID --output json
observal admin review show REVIEW_UUID --output json
```

Review only the requested item after inspecting details:

```bash
observal admin review approve REVIEW_UUID --output json
observal admin review approve AGENT_UUID --agent --output json
observal admin review approve BUNDLE_UUID --bundle --output json
observal admin review reject REVIEW_UUID --reason 'Not reproducible' --output json
```

Complete skill folders are reviewed per exact version. `review show` prints the version UUID, revision and files; `approve` lists the files and binds the decision to that revision, so pass `--revision REVISION` to approve exactly what was inspected (or `--yes` to accept the files listed for the run). A Git-to-folder conversion also needs `--ack-git-base`. Use `--version-id UUID` when several versions are pending. `reject` takes `--reason` as usual.

```bash
observal admin review show NAMESPACE/SLUG --output json
observal admin review approve NAMESPACE/SLUG --revision REVISION --output json
```

Component types include `mcp`, `skill`, `hook`, `prompt`, and `sandbox`. Agent and bundle selectors are mutually exclusive. Verify returned status and do not act on unrelated queue items.

## Recommendations

Admins can mark an agent or component as recommended. It adds a registry badge and `obs:recommended: true` in discovery; it never changes relevance scores.

```bash
observal admin recommend agent NAMESPACE/SLUG --output json
observal admin recommend mcp NAMESPACE/SLUG --unset --output json
```

Types are `agent`, `mcp`, `skill`, `hook`, `prompt`, and `sandbox`. Verify `is_recommended` in the response.

## Security and audit

```bash
observal admin security-events --limit 50 --offset 0 --output json
observal admin audit-log --limit 100 --offset 0 --output json
observal admin audit-log --actor user@example.com --source server --output json
observal admin audit-log-export --file audit.json --output json
```

Use the narrowest filters that answer the investigation. Treat event details as sensitive. Export destinations must be explicit, and overwriting an existing file requires the documented force flag.

## SAML and SCIM

```bash
observal admin saml-config --output json
observal admin saml-config-set --idp-entity-id ID --idp-sso-url URL --idp-x509-cert "$(cat idp-cert.pem)" --active --output json
observal admin saml-config-delete --force --output json
observal admin scim-tokens --output json
observal admin scim-token-create --description 'Okta' --output json
observal admin scim-token-revoke TOKEN_UUID --force --output json
```

Every SAML update requires entity ID, SSO URL, and certificate. SCIM creation returns a bearer token once. Do not repeat it after initial delivery. Verify active SAML state and SCIM token metadata without exposing secrets.
