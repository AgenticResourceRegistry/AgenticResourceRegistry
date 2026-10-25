---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Deployment Settings"
---

Core deployment configuration that affects how Observal is reachable, how authentication redirects work, and which origins are allowed to make requests.

## SSO Only Mode

Disable all password-based authentication, forcing users to log in via SAML or OAuth.

**Affects:** Login page, registration, password reset. When enabled, the password form is hidden, registration is blocked, and admin password reset is unavailable.

| Value | Effect |
|-------|--------|
| `false` (default) | Both password and SSO login are available |
| `true` | Only SSO login is permitted; password endpoints return 403 |

**When to enable:** Your deployment mandates SSO for all access. Ensure SAML/OAuth is fully configured and tested before enabling, or you will lock everyone out.

**Recovery:** If locked out, set `deployment.sso_only` to `false` directly in the database (`dynamic_settings` table) and restart the API.

## Frontend URL

The base URL where users access the Observal web UI in their browser.

**Affects:** OAuth/SAML redirect URIs, device authorization confirmation links, email notification links, and CORS origin validation.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Auto-detected from incoming request `Host` header |
| `https://app.example.com` | All redirect URIs and links use this exact origin |
| `https://observal.internal:3000` | For non-standard ports or internal deployments |

**When to set:** Always set explicitly in production. Auto-detection works for development but is unreliable behind reverse proxies or CDNs.

**Common mistakes:**
- Trailing slash (`https://app.example.com/`) will cause redirect URI mismatches
- Using `http://` when your proxy terminates TLS will break OAuth callbacks
- Not matching the exact hostname users type (e.g. `www.` vs bare domain)

## Public API URL

The externally-reachable URL of the Observal API server.

**Affects:** CLI auto-configuration during `observal auth login`, telemetry endpoint discovery, webhook callback URLs, and inter-service communication references.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Inferred from incoming request headers |
| `https://api.example.com` | CLI and SDK use this for all API calls |
| `https://observal.example.com/api` | When API is path-routed behind the same domain |

**When to set:** When the API is behind a reverse proxy with a different external hostname than the container sees internally. Without this, the CLI may try to connect to an internal Docker hostname.

## OTLP Endpoint Override

Override the OpenTelemetry HTTP endpoint for telemetry ingestion.

**Affects:** Where the CLI and harness hooks send telemetry data. Normally this is the same as the public API URL (at `/api/v1/ingest`), but can be split to a dedicated collector for high-volume deployments.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Telemetry is sent to the main API at `/api/v1/ingest` |
| `https://otel.example.com` | Dedicated collector endpoint; reduces load on the main API |

**When to set:** High-traffic deployments where telemetry volume would overwhelm the main API, or when you want to route telemetry through a separate network path for isolation.

## CORS Origins

Origins allowed to make cross-origin browser requests to the API.

**Affects:** Browser-based requests from the web UI. If the frontend is served from a different origin than the API, CORS must include it. Requests from unlisted origins receive a CORS error and are blocked by the browser.

| Value | Effect |
|-------|--------|
| _(empty)_ (default) | Only same-origin requests are allowed |
| `https://app.example.com` | Single frontend origin |
| `https://app.example.com,https://admin.example.com` | Multiple origins (comma-separated) |
| `*` | Allow all origins (not recommended for production) |

**When to set:** When the frontend URL differs from the API URL (which is almost always in production). Typically set to match your Frontend URL value.

## Leaderboard

Show or hide the Leaderboard. Toggle it from **Settings > Interface**. The Leaderboard is deprecated and will be removed in a future release.

**Affects:** The Leaderboard entry in the sidebar and command menu, the "Leaderboard" link on the home page, the `/leaderboard` page, and the `/api/v1/overview/leaderboard` and `/api/v1/overview/component-leaderboard` endpoints. Ranking data is kept either way.

| Value | Effect |
|-------|--------|
| `false` (default) | Entry points are hidden, `/leaderboard` redirects to the home page, and both endpoints return 404 |
| `true` | The Leaderboard is available and shows a deprecation notice |
