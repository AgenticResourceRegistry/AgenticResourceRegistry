---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "OIDC / OAuth 2.0 SSO Setup Guide"
---

This guide walks you through configuring OpenID Connect (OIDC) / OAuth 2.0
Single Sign-On for Observal. It covers environment variable
configuration, per-IdP setup instructions, and troubleshooting common issues.

---

## 1. Prerequisites

Before you begin, make sure the following requirements are met:

- **HTTPS is required.** The Observal instance must be served over HTTPS.
  OAuth 2.0 redirect URIs must use HTTPS, and IdPs will reject callback URLs
  that use plain HTTP.
- **IdP admin access.** You need administrator privileges on your identity
  provider (Okta, Azure AD / Entra ID, Google Workspace, or another
  OIDC-compliant IdP) to create and configure an OAuth application.

---

## 2. Observal Settings

Configure OIDC SSO in **Admin → SSO → SSO settings**.

| Setting | Description |
|---|---|
| `oauth.client_id` | The client ID issued by your IdP when you register the Observal application. |
| `oauth.client_secret` | The client secret issued by your IdP. Treat this as a password and store it securely. |
| `oauth.server_metadata_url` | The OIDC discovery URL for your IdP. This is the `.well-known/openid-configuration` endpoint. |
| `deployment.sso_only` | When set to `true`, disables password-based authentication entirely. Only SSO login is allowed. See [SSO-Only Mode](#6-sso-only-mode). |
| `deployment.frontend_url` | The public URL of your Observal instance. Used to construct the OAuth callback URL registered with your IdP. |

OIDC client changes require an API restart before normal user login uses them. Existing `OAUTH_*` env vars are imported once on startup when the dynamic setting is not already present.

### Callback URL

When registering Observal with your IdP, use the following redirect URI:

```
{FRONTEND_URL}/api/v1/auth/oauth/callback
```

For example, if `FRONTEND_URL=https://observal.example.com`, the callback URL is:

```
https://observal.example.com/api/v1/auth/oauth/callback
```

This value must match exactly what is configured in your IdP. Any mismatch
(trailing slash, HTTP vs HTTPS, wrong path) will cause the OAuth flow to fail.

### Minimal Example

With these three settings saved and the API restarted, OIDC SSO is ready.

---

## 3. IdP-Specific Setup

### 3.1 Okta

1. In the Okta admin console, go to **Applications > Create App Integration**.
2. Select **OIDC - OpenID Connect** and then **Web Application**. Click
   **Next**.
3. Fill in the application settings:
   - **App integration name:** "Observal"
   - **Sign-in redirect URIs:**
     `https://observal.example.com/api/v1/auth/oauth/callback`
   - **Sign-out redirect URIs:**
     `https://observal.example.com/login`
4. Under **Assignments**, choose to limit access to specific groups or allow
   everyone in your organization, then click **Save**.
5. On the application's **General** tab, copy:
   - **Client ID** to `oauth.client_id`
   - **Client secret** to `oauth.client_secret`
6. Set `oauth.server_metadata_url` to your Okta domain's discovery URL:
   ```
   https://{your-okta-domain}/.well-known/openid-configuration
   ```
   Replace `{your-okta-domain}` with your Okta domain, e.g.,
   `https://your-company.okta.com/.well-known/openid-configuration`.
7. Assign users or groups to the Okta application under the **Assignments**
   tab.

### 3.2 Azure AD (Microsoft Entra ID)

1. In the Azure portal, go to **Microsoft Entra ID > App registrations** and
   click **New registration**.
2. Fill in the registration details:
   - **Name:** "Observal"
   - **Supported account types:** Choose the appropriate option for your
     organization (typically "Accounts in this organizational directory only").
   - **Redirect URI:** Select **Web** and enter
     `https://observal.example.com/api/v1/auth/oauth/callback`
3. Click **Register**.
4. Under **Certificates & secrets**, click **New client secret**. Enter a
   description and choose an expiry period, then click **Add**. Copy the
   **Value** immediately (it is only shown once):
   - Client secret value to `oauth.client_secret`
5. On the application **Overview** page, copy:
   - **Application (client) ID** to `oauth.client_id`
6. Set `oauth.server_metadata_url` using your tenant ID:
   ```
   https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration
   ```
   Replace `{tenant-id}` with the **Directory (tenant) ID** shown on the
   Overview page.
7. Under **API permissions**, verify that `openid`, `email`, and `profile`
   permissions are present. These are included by default for new registrations.
   If missing, click **Add a permission > Microsoft Graph > Delegated
   permissions** and add them.
8. Under **Enterprise applications**, find the Observal application and go to
   **Users and groups** to assign access to specific users or groups.

### 3.3 Google Workspace

1. In the [Google Cloud Console](https://console.cloud.google.com), navigate to
   **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. If prompted to configure the OAuth consent screen first, do so:
   - Set **User type** to **Internal** (restricts to your organization).
   - Fill in the required app name and contact fields.
   - Add the `email`, `profile`, and `openid` scopes.
4. Back on the **Create OAuth client ID** page, set:
   - **Application type:** Web application
   - **Name:** "Observal"
   - **Authorized redirect URIs:**
     `https://observal.example.com/api/v1/auth/oauth/callback`
5. Click **Create**. Copy:
   - **Client ID** to `oauth.client_id`
   - **Client secret** to `oauth.client_secret`
6. Set `oauth.server_metadata_url` to Google's fixed discovery URL:
   ```
   https://accounts.google.com/.well-known/openid-configuration
   ```
7. To restrict the application to specific users or organizational units, go to
   the [Google Admin console](https://admin.google.com). Under **Apps > Web and
   mobile apps**, you can locate the app and configure which organizational
   units have access.

---

## 4. How the Flow Works

The OIDC authorization code flow used by Observal works as follows:

1. The user visits the Observal login page and clicks **Sign in with SSO**.
2. Observal redirects the browser to the IdP's authorization endpoint with
   an OIDC authorization code request.
3. The IdP authenticates the user (prompting for credentials or MFA if no
   existing session is present).
4. After successful login, the IdP redirects the browser back to:
   ```
   /api/v1/auth/oauth/callback
   ```
   with an authorization code in the query string.
5. Observal exchanges the authorization code for an ID token and access token
   by calling the IdP's token endpoint (server-to-server, not via browser).
6. Observal extracts the user's email and name from the ID token claims.
7. If the user already exists, their session is created and they are logged in.
   If the user is new, an account is created (Just-In-Time provisioning) and
   they are logged in.
8. Observal issues a JWT and redirects the browser to the Observal dashboard.

---

## 5. SSO-Only Mode

Setting `deployment.sso_only=true` locks down Observal to IdP authentication exclusively:

- **Password login is disabled** across the web UI, CLI, and API. Users who
  previously had password-based accounts can no longer use them.
- **The "Create User" button is hidden** in the admin panel. User accounts must
  come from the IdP via OIDC JIT provisioning or SCIM.
- **Manual user creation via the admin panel is disabled.** Admins cannot
  create accounts directly; accounts are provisioned through the IdP.
- **The CLI uses the device authorization flow** (RFC 8628) automatically when
  SSO-only mode is detected. See [cli-sso.md](/self-hosting/cli-sso) for instructions.
- **CI/CD pipelines** should use `OBSERVAL_TOKEN` (an API token generated from
  the web UI) rather than username/password authentication.

---

## 6. Choosing Between OIDC and SAML

Both OIDC and SAML achieve SSO. Use this table to decide which protocol fits
your organization:

| Aspect | OIDC / OAuth 2.0 | SAML 2.0 |
|---|---|---|
| Protocol | REST / JSON | XML |
| Setup complexity | Simpler (3 dynamic settings) | More involved (certificates, metadata XML) |
| IdP support | All modern IdPs | Older enterprise IdPs, legacy infrastructure |
| Use when | IdP supports OIDC (Okta, Azure AD, Google) | IdP only supports SAML, or signed assertions are required by policy |
| Provisioning | JIT only (on first login) | JIT only (combine with SCIM for full lifecycle) |

**Recommendation:** Use OIDC unless your deployment requires SAML for
compliance or existing infrastructure reasons. Either protocol can be combined
with [SCIM provisioning](/self-hosting/scim-setup) for automated user lifecycle management
(pre-provisioning and deprovisioning).

---

## 7. Troubleshooting

### "Sign in with SSO" button is not shown

**Cause:** The server is not reporting SSO as enabled.

**Fix:** Call `/api/v1/config/public` and check that it returns
`"sso_enabled": true`. If it does not:

- Verify all three OAuth variables (`oauth.client_id`, `oauth.client_secret`,
  `oauth.server_metadata_url`) are set and the server has been restarted.

### "SSO sign-in failed" after redirect back from IdP

**Cause:** There are several possible reasons.

**Common causes and fixes:**

- **Callback URL mismatch.** The redirect URI registered in your IdP must
  exactly match `{FRONTEND_URL}/api/v1/auth/oauth/callback`. Check for
  trailing slashes, HTTP vs HTTPS differences, or typos.
- **Incorrect client secret.** Re-copy the client secret from your IdP and
  update `oauth.client_secret`. Secrets often contain special characters that
  can be truncated by shell escaping.
- **Metadata URL unreachable.** Observal fetches the discovery document at
  startup. If the URL is unreachable from the server (firewall, private IdP),
  the token exchange will fail. Verify the URL is accessible from the Observal
  host.

### "No email in token"

**Cause:** The IdP is not including an `email` claim in the ID token.

**Fix:** Ensure the `email` scope is requested. Verify in your IdP that the
application is granted the `email` (and `profile`) scopes. Some IdPs require
you to explicitly add these in the OAuth consent screen or application
permissions.

### 403 Forbidden on callback

**Cause:** The user is deactivated or does not satisfy the configured identity-provider policy.

**Fix:**

- Check whether the user's account exists and is active in the Observal admin
  panel.

### General Debugging Tips

- **Check server logs.** Set `LOG_LEVEL=debug` to see the full OAuth exchange,
  including the token endpoint response and ID token claims.
- **Inspect the discovery document.** Fetch the `oauth.server_metadata_url`
  directly to confirm it is reachable and returns a valid JSON document with
  `authorization_endpoint` and `token_endpoint` fields.
- **Verify the IdP application assignment.** Some IdPs (Okta, Azure AD) require
  users to be explicitly assigned to the application before they can
  authenticate. Unassigned users will fail at the IdP before reaching Observal.
