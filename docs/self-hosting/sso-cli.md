---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Observal SSO, SCIM, and Audit API Reference"
---

For the core CLI reference, see [docs/cli/index.md](/cli). For setup instructions, see [docs/self-hosting/index.md](/self-hosting).

---

## SSO Configuration

| Setting | Default | Description |
|----------|---------|-------------|
| `oauth.client_id` | disabled | OAuth/OIDC client ID |
| `oauth.client_secret` | disabled | OAuth/OIDC client secret |
| `oauth.server_metadata_url` | disabled | OIDC discovery URL |
| `deployment.sso_only` | `false` | When true, disables password auth entirely; only SSO login allowed |
| `saml.idp_entity_id` | disabled | IdP entity ID for SAML SSO |
| `saml.idp_sso_url` | disabled | IdP single sign-on URL |
| `saml.idp_x509_cert` | disabled | IdP signing certificate (base64) |
| `saml.sp_entity_id` | auto | SP entity ID (derived from FRONTEND_URL if empty) |
| `saml.sp_acs_url` | auto | SP ACS URL (derived from FRONTEND_URL if empty) |
| `saml.jit_provisioning` | `true` | Auto-create users on first SAML login |
| `saml.default_role` | `user` | Default role for JIT-provisioned users |
| `saml.sp_key_encryption_password` | disabled | Encrypt SP private key at rest |

When `deployment.sso_only=true`, password-based login is disabled. All authentication goes through your configured identity provider.

---

## Audit Log API

Observal logs admin and write operations to ClickHouse. Audit logs are queryable via the API and exportable as CSV.

### `GET /api/v1/admin/audit-log`

Query audit log entries. Requires admin role.

| Parameter | Type | Description |
|-----------|------|-------------|
| `actor` | string | Filter by actor email |
| `action` | string | Filter by action (e.g. `create`, `delete`, `approve`) |
| `resource_type` | string | Filter by resource type (e.g. `mcp`, `agent`, `user`) |
| `start_date` | datetime | Start date filter |
| `end_date` | datetime | End date filter |
| `limit` | int | Max results (1-500, default 50) |
| `offset` | int | Pagination offset |

```bash
curl "http://localhost/api/v1/admin/audit-log?actor=admin@example.com&limit=20" \
  -H "X-API-Key: $OBSERVAL_KEY"
```

### `GET /api/v1/admin/audit-log/export`

Export audit logs as CSV. Same filters as the query endpoint.

```bash
curl "http://localhost/api/v1/admin/audit-log/export?start_date=2026-01-01" \
  -H "X-API-Key: $OBSERVAL_KEY" -o audit_log.csv
```

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

For CI/CD environments without a browser, set the `OBSERVAL_TOKEN`
environment variable instead.

For detailed instructions, see [cli-sso.md](/self-hosting/cli-sso).

---

## SSO Configuration Guard

SSO login and SCIM routes return a configuration error when required identity-provider settings are incomplete. SAML SP metadata remains available so administrators can configure the IdP.

When `deployment.sso_only=true`, password authentication is disabled. Users can be provisioned via OIDC, SAML JIT provisioning, SCIM, or the admin panel. Public self-registration remains controlled by `auth.self_registration_enabled`.
