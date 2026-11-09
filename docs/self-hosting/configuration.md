---
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Configure your registry"
description: "Separate boot configuration, runtime settings, and externally managed credentials."
---

Configure infrastructure before starting the server, then configure access and product behavior through the registry's settings. This guide distinguishes those two mechanisms so a saved value is not mistaken for an applied change.

## Before you change anything

Identify the deployment artifact, image/source revision, Compose project, effective environment, and mounted credential files. Back up configuration and secrets before replacing them. A file visible on the host is not automatically visible inside a container.

| Mechanism | Examples | How changes take effect |
| --- | --- | --- |
| Boot/process configuration | Database URLs, encryption secret, signing algorithm, CORS environment | Recreate or restart the affected processes with the new environment/mounts. |
| Runtime settings | Registration, frontend URL, review policy, retention, provider configuration | Stored in the settings database/cache; some clients and policies are initialized at startup and require a restart. |
| Externally managed files | Supported secret and SAML key inputs | Loaded into process memory from mounted files; replace safely and restart affected consumers. |

The source Compose `.env` file supplies container environment values. A packaged installation also has generated service-specific credential files. Do not copy one artifact's mounts or bindings into another without checking them.

## Required for production

- Use a strong, unique `SECRET_KEY` or `SECRET_KEY_FILE`. It protects persisted encrypted values; it is not the JWT signing key. Preserve it with your backups.
- Replace demo database credentials and ensure application connection URLs match the database's actual credentials.
- Serve the intended public URL over TLS and set `deployment.frontend_url` to that exact origin for callbacks and user-facing links.
- Configure the API's actual CORS environment and verify browser requests, not just the value displayed in an admin form.
- Remove or explicitly disable demo credentials in the effective API environment before a fresh production boot. Deleting `.env` lines alone is insufficient when Compose supplies fallback values.
- Review registration, public-registry visibility, trusted proxies, usage reporting, and retention before allowing team access.

Use [authentication and SSO](/self-hosting/authentication), [ports and volumes](/self-hosting/ports-and-volumes), and [usage reporting](/self-hosting/usage-reporting) for those decisions.

## Database connections

Source configuration starts with connection strings such as:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@observal-db:5432/observal
CLICKHOUSE_URL=clickhouse://default:clickhouse@observal-clickhouse:8123/observal
REDIS_URL=redis://observal-redis:6379
```

These are evaluation examples, not production credentials. `observal-db` and the other service names resolve inside the Compose network. Host clients connect through the intended published ports instead.

Changing a URL or password requires coordinating the database, API, worker, health checks, and mounted files. A changed API connection string does not change the database password by itself.

## Secret files

Supported secret inputs accept a `NAME_FILE` form:

```dotenv
SECRET_KEY_FILE=/run/secrets/secret_key
DATABASE_URL_FILE=/run/secrets/database_url
JWT_KEY_PASSWORD_FILE=/run/secrets/jwt_key_password
GIT_CLONE_TOKEN_FILE=/run/secrets/git_clone_token
OAUTH_CLIENT_SECRET_FILE=/run/secrets/oauth_client_secret
INSIGHTS_API_KEY_FILE=/run/secrets/insights_api_key
```

Mount each file at the declared container path and grant read access only to the processes that need it. Do not supply both `NAME` and `NAME_FILE`; the reader rejects that ambiguity. Files must be UTF-8, at most 64 KiB, and may contain one trailing newline.

File-backed SSO/provider values are externally managed: their contents remain in memory instead of being imported into PostgreSQL/Redis, and the admin API cannot replace them. Direct legacy SSO environment values can be imported when the corresponding stored setting is empty; a non-empty stored value wins. This is not a general environment fallback for every runtime setting.

Replace mounted files atomically and restart all affected consumers. Changing `SECRET_KEY` needs a separately planned encrypted-value rotation/recovery procedure; do not regenerate it as routine maintenance.

## URLs and CORS

`deployment.frontend_url` is a runtime setting used to build authentication callbacks and links. Set it through Admin Settings or, for a non-secret value:

```bash
observal admin set deployment.frontend_url https://registry.example.com
```

Replace the example URL. Test its actual browser/callback behavior after the required restart.

The inspected API middleware reads **`CORS_ALLOWED_ORIGINS` from process environment**. It does not read `deployment.cors_origins` in that CORS builder. Do not assume changing the similarly named admin setting changes the live middleware:

```dotenv
CORS_ALLOWED_ORIGINS=https://registry.example.com
```

For Compose, recreate the API with the updated environment, then test the intended origin and an unwanted origin. Plain `docker compose restart` does not replace a container's environment from an edited `.env` file.

Likewise, a packaged `FRONTEND_URL` environment entry is not a replacement for verifying the runtime `deployment.frontend_url` used by authentication.

## Authentication and runtime policy

Configure password/SSO access through `deployment.sso_only`, and self-registration separately through `auth.self_registration_enabled`. Both default to false; false SSO-only means password login is allowed, not that registration is enabled.

[Review and publication](/guides/review-and-publish) use their own policy. Public listing visibility, author draft installs, and teamspace permissions are separate controls.

### Rate limiting

Use the runtime keys `security.rate_limit_auth` and `security.rate_limit_auth_strict`. They default to `10/minute` and `5/minute`; request decorators may bind values when the API starts. Restart and verify the relevant behavior after changing a startup-bound limit. Do not present `RATE_LIMIT_AUTH` as the authoritative settings mechanism.

### ClickHouse retention

Use `data.retention_days`, separate from the scheduled `retention.*` application policies. `DATA_RETENTION_DAYS` is not the current boot-setting source. A stored setting is not proof that a storage TTL was applied.

In the inspected source Compose path, `SKIP_DDL_ON_STARTUP=true` skips the runtime ClickHouse initializer that applies this setting. The fresh operator rehearsal's `session_events` table had no TTL clause despite the configured default of 90 days. Do not rely on that default or promise that an API restart changes the table. Verify the actual table definition and use a reviewed policy-application/migration procedure before making retention guarantees. See [data retention](/self-hosting/data-retention).

## Demo accounts

API startup can seed configured demo identities when there are no real users. Source Compose supplies `DEMO_*` fallback values in its service environment; merely removing entries from `.env` does not remove those fallbacks.

For a fresh non-demo source deployment, apply an explicit API environment override like this as part of your reviewed deployment configuration:

```yaml
services:
  observal-api:
    environment:
      DEMO_SUPER_ADMIN_EMAIL: ""
      DEMO_SUPER_ADMIN_PASSWORD: ""
      DEMO_ADMIN_EMAIL: ""
      DEMO_ADMIN_PASSWORD: ""
      DEMO_REVIEWER_EMAIL: ""
      DEMO_REVIEWER_PASSWORD: ""
      DEMO_USER_EMAIL: ""
      DEMO_USER_PASSWORD: ""
```

Include the override when creating the API container. Verify the effective environment without printing passwords, then complete first-admin setup on a restricted network.

The inspected seeding path does not consult `SEED_DEMO_ACCOUNTS`; do not rely on that flag alone. Clearing credentials prevents new seeding but does not delete existing demo accounts. Review existing accounts and remove them through the authorized user-management workflow before opening access.

## JWT keys

`JWT_SIGNING_ALGORITHM` accepts ES256 or RS256. `JWT_KEY_DIR` defaults to `~/.observal/keys` outside Docker; source Compose sets `/data/keys` for the API and persists it through `apidata`. Back up the complete application data and encryption configuration, not just a guessed key filename.

Normal key rotation retains old public verification keys for a bounded retention period; it is not equivalent to immediate token revocation. See [JWT signing keys](/self-hosting/authentication#jwt-signing-keys).

## Git operations

`GIT_CLONE_TOKEN_FILE` supplies a token only to exact permitted HTTPS authorities in `GIT_CLONE_TOKEN_HOSTS`. Review provider-specific username, timeout, and private-host policy before enabling authenticated analysis. Validation and mirror services have distinct checks; a setting that permits an internal URL is not a blanket removal of SSRF protection.

```dotenv
GIT_CLONE_TOKEN_FILE=/run/secrets/git_clone_token
GIT_CLONE_TOKEN_HOSTS=github.com
GIT_CLONE_TOKEN_USER=x-access-token
GIT_CLONE_TIMEOUT=120
```

## Client configuration is separate

`OBSERVAL_SERVER_URL` and client JWT/token-file inputs belong on developer machines, not in server `.env` as authentication setup. Historical names such as `OBSERVAL_API_KEY` are CLI aliases for token input, not an `X-API-Key` server authentication scheme. See [environment reference](/reference/environment-variables) and [CLI authentication](/cli/auth).

## Verify and continue

Check health through the user-facing load balancer, verify the applied settings using authorized read operations, and test an ordinary user's sign-in and agent access. A successful settings save or HTTP 200 alone does not establish a healthy, correctly configured deployment.

Next: [authentication and SSO](/self-hosting/authentication), [backup and restore](/self-hosting/backup-and-restore), then [planned upgrades](/self-hosting/upgrades).
