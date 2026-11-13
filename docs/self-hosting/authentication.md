---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.work@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Set up authentication and SSO"
description: "Establish the first administrator, configure identity, and verify access without locking users out."
---

Choose how people sign in, establish a real administrator, and test access before enabling a restrictive policy. Registry authentication is separate from each developer's coding-tool/model account.

## Authentication modes

Authenticated API requests use **JWT bearer access tokens**. The server does not provide an `X-API-Key` authentication path or user-generated API-key login.

| Method | Configuration | Important distinction |
| --- | --- | --- |
| Email/username and password | Allowed when `deployment.sso_only=false` | Does not automatically enable public registration. |
| Public self-registration | `auth.self_registration_enabled=true`, with password auth allowed | Creates ordinary user accounts, not administrators. |
| Generic OIDC | `oauth.client_id`, `oauth.client_secret`, `oauth.server_metadata_url` | Client is initialized at API startup; restart after changes. |
| Google sign-in | `google.*` settings | Separate provider with its own callback and domain policy. |
| GitHub sign-in | `github.*` settings | OAuth application credentials, not a personal access token. |
| SAML | Reviewed `saml.*`/SP configuration | Provider setup, key material, provisioning, and policy must agree. |

The login page should expose only the enabled methods. Test the actual method with an ordinary account rather than treating a saved configuration as acceptance.


## The first administrator

Restrict network access **before** starting an uninitialized server. The CLI's empty-server login flow collects administrator details and submits them to `/api/v1/auth/init`; this is different from the separate localhost-only `/api/v1/auth/bootstrap` endpoint. Do not infer that every initialization path is restricted to localhost.

```bash
observal auth login --server http://localhost --no-setup
observal auth whoami
```

Use your actual restricted evaluation URL. A fresh CLI initialization creates an `admin`, not an automatic `super_admin`. If demo accounts already exist, this is not an empty server: sign in using the intended authorized account and replace evaluation access deliberately.

Confirm the real account's role and recovery path before removing demo access. Some policies, including usage-reporting consent, require super-admin authority; do not assume first-admin initialization grants every administrative operation. [Configuration](/self-hosting/configuration#demo-accounts) explains why removing `.env` demo lines alone may not prevent source Compose seeding.

## Self registration

Registration defaults to off. An authorized administrator can enable it through Admin Settings or the corresponding runtime setting:

```bash
observal admin set auth.self_registration_enabled true
```

New accounts have the `user` role. Test registration and role restrictions with a disposable ordinary account before enabling it for a team. Setting `deployment.sso_only=true` also blocks the password-registration path.

## SSO-only mode

Set `deployment.sso_only=true` under **Admin → SSO → Access policy** only after a real user has successfully signed in through your intended provider and an administrator has a tested recovery route.

The guarded password login, registration, and CLI initialization paths are disabled by SSO-only policy. The separate bootstrap route has its own local/empty-server checks; SSO-only is not a substitute for securing first initialization.

Do not disable working access first and then discover that the IdP callback or user assignment is wrong.

## OAuth / OIDC SSO

In **Admin → SSO**, configure the three `oauth.*` client settings and the runtime `deployment.frontend_url`. Restart the API to rebuild its OAuth client. Register the exact callback at the IdP:

```text
https://YOUR_REGISTRY/api/v1/auth/oauth/callback
```

The origin must match `deployment.frontend_url`, not an unrelated environment entry. Generic OIDC requests `openid email profile groups`; the provider must return the identity claims your access/provisioning policy requires. An OAuth login does not automatically promote the first provider user to admin.

<a id="oauth-client-id" />

### OIDC Client ID

The identifier issued for this registry's IdP application.

<a id="oauth-client-secret" />

### OIDC Client Secret

A credential for that application. Enter it through the protected settings UI or a supported mounted secret file. Do not put it in a command argument, screenshot, or issue.

<a id="oauth-server-metadata-url" />

### OIDC Discovery URL

The provider's discovery endpoint for the intended tenant. Check application type, assignment, issuer, callback, and claims against that provider's current setup instructions. Use [the OIDC setup guide](/self-hosting/oidc-setup) for the workflow; saving values is not proof the login exchange succeeds.

<a id="google-oauth" />

## Google sign-in

Google is separate from the generic OIDC slot. Configure `google.client_id`, `google.client_secret`, and the policy below, then restart the API. Its callback is:

```text
https://YOUR_REGISTRY/api/v1/auth/oauth/google/callback
```

Use a Google OAuth web application for that callback. The server checks verified email; provisioned users are not automatically administrators.

<a id="google-allowed-domains" />

### Allowed Google domains

`google.allowed_domains` restricts eligible email domains. An empty policy allows a broader set of Google identities, including personal accounts. Verify both an allowed and a refused account before opening access.

<a id="github-oauth" />

## GitHub sign-in

Configure `github.client_id` and **the OAuth App's client secret**, not a `ghp_`/`github_pat_` personal access token. Restart the API after changes. The callback is:

```text
https://YOUR_REGISTRY/api/v1/auth/oauth/github/callback
```

The server exchanges the authorization code and checks verified email returned by GitHub. Model/CLI access and membership policy are separate from successful OAuth consent.

<a id="github-allowed-orgs" />

### Allowed GitHub organizations

`github.allowed_orgs` lists eligible organizations. When set, the requested OAuth scopes include `read:org` and active membership is checked. Confirm application authorization and a real allowed/denied identity; an empty allowlist permits broader sign-in.

## SAML and externally managed material

Follow the [SAML setup guide](/self-hosting/saml-setup) and configure the SP/IdP identifiers, endpoints, certificates, provisioning, and default role together. A metadata download alone does not verify a browser assertion exchange.

Mounted SP material uses this pair:

```dotenv
SAML_SP_PRIVATE_KEY_FILE=/run/secrets/saml_sp_private_key
SAML_SP_X509_CERT_FILE=/run/secrets/saml_sp_x509_cert
```

Both files must be configured and readable by the consuming API. Externally managed material overrides generated database keys and cannot be replaced through the admin API. Rotation needs a coordinated provider change and process restart. See [secret files](/self-hosting/configuration#secret-files).

## JWT signing keys

ES256 is the default; RS256 is also accepted. In source Compose, JWT signing files live at `/data/keys` in `apidata`. Preserve the encryption configuration and key directory with [consistent backups](/self-hosting/backup-and-restore).

Key initialization is synchronized across API processes. Normal rotation or an algorithm change retains old public verification keys for a bounded period while new tokens use the current key. That is **not immediate revocation**. Token expiry, account/session revocation, and retired-key retention still apply.

Do not delete signing files as routine maintenance or assume key rotation revokes every prior token. A compromised key requires a separately reviewed incident/recovery procedure. Losing key material can force affected users to sign in again; preserving only PostgreSQL is insufficient recovery.

## JWT bearer authentication for scripts

Use a supported credential or browser/device flow to obtain access/refresh tokens. The CLI stores them in its sensitive local configuration and handles supported refresh behavior. Avoid copying tokens into source files or shared shell history.

For CLI-style JSON requests, [use `observal api`](/cli/api) where appropriate; it also supplies the client-version metadata expected on guarded paths. A valid JWT alone does not satisfy every deployment/version check.

`POST /api/v1/auth/token/revoke` revokes the supplied refresh token; already-issued access tokens can remain valid until expiry. Logout uses the account/session revocation mechanism and can sign out CLI, browser, and hooks. See [CLI authentication](/cli/auth).

## Role-based access control

Server roles and teamspace permissions both matter. Ordinary users submit/install within their scope; eligible reviewers inspect and publish; administrators manage permitted users/settings; some operations require super-admin authority. None of those labels proves access to every private record or exempts a resource from its policy.

Use [review/publication](/guides/review-and-publish), [teamspaces](/use-cases/teamspaces), and the [admin reference](/cli/admin) rather than treating a role table as the full authorization contract.

## Recovery and verification

- Test browser login and CLI device login through the actual public origin.
- Verify an ordinary user's registry access and a refused access case.
- Check callback URI, issuer, assignment, verified-email/organization/domain policy, and expected role on failure.
- Redis is part of authentication/revocation; a healthy UI alone is not proof authentication works when that dependency is unavailable.
- For a forgotten local password, use the authorized admin reset workflow. Deliver a generated password securely and require the user's change; do not publish it in logs or screenshots.

## Next

[Configure the registry](/self-hosting/configuration), [review its network exposure](/self-hosting/ports-and-volumes), and [rehearse backup/recovery](/self-hosting/backup-and-restore) before restricting access or upgrading.
