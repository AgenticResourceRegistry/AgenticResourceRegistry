---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "CLI Authentication with SSO"
---

This guide covers authenticating the Observal CLI when your deployment uses
SSO (SAML, OIDC, or OAuth) for login. The CLI uses the
[OAuth 2.0 Device Authorization Grant (RFC 8628)](https://datatracker.ietf.org/doc/html/rfc8628)
to authenticate through your browser without handling IdP credentials directly.

---

## How It Works

The device flow lets a CLI (which has no browser) authenticate by delegating
the login to a browser session where SSO is available.

```
CLI                         Server                     Browser
 |                            |                           |
 |-- POST /device/authorize ->|                           |
 |<- device_code + user_code -|                           |
 |                            |                           |
 |  (displays URL + code)     |                           |
 |  (opens browser) --------->|                           |
 |                            |     user visits URL       |
 |                            |<-- logs in via SSO -------|
 |                            |<-- enters user_code ------|
 |                            |-- POST /device/confirm -->|
 |                            |                           |
 |-- POST /device/token ----->|                           |
 |<- access_token + refresh --|                           |
```

1. The CLI requests a device code from the server.
2. The server returns a short `user_code` (e.g., `BCDF-GHJK`) and a
   `verification_uri` pointing to the Observal web UI.
3. The CLI opens the browser automatically and displays the URL + code in
   the terminal as a fallback.
4. The user logs in via their normal SSO flow (SAML, OIDC, or OAuth) in
   the browser.
5. After login, the user enters the `user_code` on the device verification
   page to approve the CLI.
6. The CLI, which has been polling in the background, receives JWT tokens
   and saves them to `~/.observal/config.json`.

The device code expires after 10 minutes. If the user does not approve
within that window, the CLI exits with an error.

---

## Usage

### Interactive SSO Login

```bash
observal auth login --sso
```

When the server enables more than one SSO provider, `--sso` asks which one to
use in an interactive terminal. JSON mode and non-interactive input never ask and
fall back to the server's default, so name the provider up front with
`--provider` (`oidc`, `saml`, `google` or `github`) there:

```bash
observal auth login --sso --provider google
```

Or omit `--sso` and pick a provider from the interactive menu, which lists
every provider the server enables:

```bash
observal auth login
# Connected.
#   [1] CLI email/username + password
#   [2] Web sign-in
#   [3] OIDC SSO
#   [4] Google
#   [5] SAML SSO
#   Login method: 4
```

### SSO-Only Deployments

When `deployment.sso_only=true` is set on the server, the CLI automatically uses the
device flow and skips the login-method menu, so no `--sso` flag is needed. If several SSO
providers are enabled, a terminal session still asks which one to use; pass `--provider google`
(or `github`, `saml`) to skip that prompt. With a single enabled provider, no prompt appears:

```bash
observal auth login --server https://observal.company.com
# Connected.
# To sign in, open this URL in your browser:
#   https://observal.company.com/device
#   Then enter code: BCDF-GHJK
# Browser opened automatically.
# Waiting for authorization........ authorized!
# Logged in as Jane Doe (jane@company.com) [user]
```

### Non-Interactive / CI

For CI/CD pipelines and scripts where no browser is available, use the
`OBSERVAL_TOKEN` environment variable instead of the device flow:

```bash
export OBSERVAL_TOKEN="your-api-token-here"
observal agent list
```

The CLI checks `OBSERVAL_TOKEN` before reading `~/.observal/config.json`.
Generate API tokens from the Observal web UI under your user settings.

---

## Server Configuration

The device flow endpoints are always available so any deployment with SSO can use them.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/auth/device/authorize` | POST | Request a device code + user code |
| `/api/v1/auth/device/token` | POST | Poll for token (CLI uses this) |
| `/api/v1/auth/device/confirm` | POST | Approve a device code (browser calls this) |

### Rate Limits

- `/device/authorize`: 5 requests per minute per IP
- `/device/token`: 10 requests per minute per IP

The CLI polls `/device/token` every 5 seconds by default (configurable via the
`interval` field in the authorize response).

---

## User Code Format

User codes are 8 characters in `XXXX-XXXX` format, using an unambiguous
alphabet that excludes characters easily confused in terminal fonts:

- Excluded: `0`, `O`, `1`, `I`, `L`, `A`, `E`, `U`
- Included: `B C D F G H J K M N P Q R S T V W X Z 2 3 4 5 6 7 8 9`

Codes are case-insensitive: `bcdf-ghjk` and `BCDF-GHJK` both work.

---

## Troubleshooting

### "Device authorization failed (429)"

You have hit the rate limit. Wait 60 seconds and try again.

### "Device code expired"

The 10-minute window elapsed before the code was approved in the browser.
Run `observal auth login --sso` again to get a fresh code.

### Browser does not open

If the CLI cannot open a browser (e.g., SSH session, WSL without browser
forwarding), manually copy the URL from the terminal into a browser on any
device that can reach the Observal server.

### "SSO (opens browser)" option not shown

The CLI queries `/api/v1/config/public` to detect SSO availability. If the
option does not appear:

- Verify that SAML or OIDC is configured on the server.
- Check that the `/api/v1/config/public` response includes
  `"sso_enabled": true` or `"saml_enabled": true`.

### Already authenticated but want to re-login via SSO

```bash
observal auth logout
observal auth login --sso
```
