---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Agentic Resource Registry SSO, SCIM, and Audit API Reference"
---

For the core CLI reference, see [docs/cli/index.md](/cli). For setup instructions, see [docs/self-hosting/index.md](/self-hosting).

---

## SSO Configuration

Use [authentication and SSO](/self-hosting/authentication) for supported identity flows, boot-time versus runtime settings, restart requirements, and SSO-only behavior. This reference is not a second configuration recipe. Server authentication uses JWT bearer access tokens; SCIM provisioning has its own token contract below. Neither is an `X-API-Key` login path.

---

## Audit Log API

Agentic Resource Registry logs admin and write operations to ClickHouse. Audit logs are queryable via the API and exportable as CSV.

### `GET /api/v1/admin/audit-log`

Query audit log entries. Requires admin role.

| Parameter | Type | Description |
|-----------|------|-------------|
| `actor` | string | Filter by actor name, username, or email |
| `action` | string | Filter by action (e.g. `create`, `delete`, `approve`) |
| `resource_type` | string | Filter by resource type (e.g. `mcp`, `agent`, `user`) |
| `start_date` | datetime | Start date filter |
| `end_date` | datetime | End date filter |
| `limit` | int | Max results (1-500, default 50) |
| `offset` | int | Pagination offset |

Provision an owner-restricted header file through your approved secret mechanism, containing `Authorization: Bearer <JWT_ACCESS_TOKEN>`. Do not commit it or place the token directly in command arguments:

```bash
curl --fail --silent --show-error \
  --header @/run/secrets/registry_authorization_header \
  'https://registry.example.com/api/v1/admin/audit-log?limit=20'
```

### `GET /api/v1/admin/audit-log/export`

Export audit logs as CSV by default; `format=json` selects JSON. Actor/action/resource, sensitivity, outcome, source, and date filters are supported. List pagination is not an export bound. Treat exports as sensitive data and select the intended scope.

```bash
umask 077
EXPORT_DIR="$HOME/registry-audit-exports"
mkdir -p "$EXPORT_DIR"
chmod 700 "$EXPORT_DIR"
curl --fail --silent --show-error \
  --header @/run/secrets/registry_authorization_header \
  'https://registry.example.com/api/v1/admin/audit-log/export?format=csv&actor=REQUESTED_ACTOR' \
  --output "$EXPORT_DIR/audit_log.csv"
chmod 600 "$EXPORT_DIR/audit_log.csv"
```

Replace the URL/filter with your approved deployment and scope; a successful authenticated export is not a complete audit-retention or compliance certification.

---

## SCIM 2.0 Provisioning

SCIM endpoints allow your IdP (Okta, Azure AD, etc.) to automatically provision and deprovision users.

Authentication uses a shared bearer token stored as a SHA-256 hash in the scim_tokens table.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/scim/Users` | List provisioned users |
| `POST` | `/api/v1/scim/Users` | Create a user from IdP |
| `GET` | `/api/v1/scim/Users/{id}` | Get a specific user |
| `PUT` | `/api/v1/scim/Users/{id}` | Update a user |
| `DELETE` | `/api/v1/scim/Users/{id}` | Deprovision a user |

For detailed setup instructions, see [scim-setup.md](/self-hosting/scim-setup).

---

## SAML 2.0 SSO

SAML endpoints for identity providers that don't support OIDC.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/sso/saml/login` | SP-initiated login |
| `POST` | `/api/v1/sso/saml/acs` | Receives IdP response |
| `GET` | `/api/v1/sso/saml/metadata` | SP metadata XML |

For detailed setup instructions, see [saml-setup.md](/self-hosting/saml-setup).

---

## CLI SSO Authentication

When SSO is the only login method, the CLI uses the OAuth 2.0 Device
Authorization Grant (RFC 8628) to authenticate through a browser.

```bash
observal auth login --sso
```

For CI/CD without a browser, provision an approved JWT access token using `OBSERVAL_ACCESS_TOKEN_FILE`; token expiry, revocation, and least privilege still apply. Registry tokens are not model-provider or SCIM credentials.

For detailed instructions, see [cli-sso.md](/self-hosting/cli-sso).

---

## SSO Configuration Guard

SSO login and SCIM routes return a configuration error when required identity-provider settings are incomplete. SAML SP metadata remains available so administrators can configure the IdP.

When `deployment.sso_only=true`, password authentication is disabled. Users can be provisioned via OIDC, SAML JIT provisioning, SCIM, or the admin panel. Public self-registration remains controlled by `auth.self_registration_enabled`.
