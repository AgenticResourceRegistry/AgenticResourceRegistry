---
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Okta SSO Setup for Agentic Resource Registry (Managed Customer Instance)"
---

Step-by-step runbook for Agentic Resource Registry operators setting up **Okta OIDC** for a **new Agentic Resource Registry instance** deployed for another company (managed solution). Follow every step in order; each step assumes the previous one is complete.

This guide matches how Agentic Resource Registry’s server is implemented today:

- Protocol: **OIDC / OAuth 2.0** (authorization code flow)
- Callback path: `{Frontend URL from Settings}/api/v1/auth/oauth/callback`
- Scopes requested: `openid email profile groups`
- Groups are read from the **`groups` claim** in the ID token (exec dashboard department mapping)

---

## Before you start (checklist)

Collect this information **before** opening Okta:

| Item | Example | Where it is used |
|------|---------|------------------|
| Customer company name | `Acme Corp` | Okta app name, group naming |
| Agentic Resource Registry public URL (HTTPS) | `https://acme.observal.io` | Okta redirect URIs; **Settings → Frontend URL** in Agentic Resource Registry |
| Okta admin access | Super admin on customer’s Okta org (or your integrator org) | All Okta steps |
| List of users (email, name) | `alice@acme.com` | Directory > People |
| List of teams/departments | `engineering`, `product`, … | Directory > Groups |
| Who is the first Agentic Resource Registry admin | `admin@acme.com` | Promoted in Agentic Resource Registry after first SSO login |

**URL rules (critical):**

- Use **HTTPS** in production (Okta and Agentic Resource Registry both expect it).
- **No trailing slash** on the base URL: `https://acme.observal.io` not `https://acme.observal.io/`.
- The sign-in redirect URI must match **exactly** (scheme, host, path).

**Naming convention (recommended per customer):**

- Okta application: `Observal - Acme Corp`
- Groups: lowercase team names (`engineering`, `platform`) - these become department filters in Agentic Resource Registry

---

## Part A - Okta configuration

### Step 1 - Sign in to the Okta Admin Console

1. Open the customer’s Okta admin URL, for example:
   - `https://acme.okta.com/admin` or
   - `https://acme-admin.okta.com`
2. Sign in with an account that has **Super Administrator** (or equivalent) rights.
3. Confirm you are in the **correct Okta org** (customer tenant, not your internal dev org).

---

### Step 2 - Create the OIDC application

1. In the left sidebar, go to **Applications** → **Applications**.
2. Click **Create App Integration**.
3. On the dialog:
   - **Sign-in method:** select **OIDC - OpenID Connect**
   - **Application type:** select **Web Application**
4. Click **Next**.

#### General settings (first wizard screen)

| Field | Value |
|-------|-------|
| **App integration name** | `Observal - <Customer Name>` (e.g. `Observal - Acme Corp`) |
| **Grant type** | Leave **Authorization Code** checked. Do **not** enable Client Credentials for Agentic Resource Registry login. |
| **Sign-in redirect URIs** | `https://<OBSERVAL_DOMAIN>/api/v1/auth/oauth/callback` |
| **Sign-out redirect URIs** | `https://<OBSERVAL_DOMAIN>/login` |
| **Controlled access** | Choose based on your process (see note below) |

Replace `<OBSERVAL_DOMAIN>` with the customer’s Agentic Resource Registry hostname only (no path), e.g. `acme.observal.io`.

**Controlled access note:**

- **Allow everyone in your organization to access** - fastest for pilots; any Okta user can attempt login (assignment still recommended).
- **Limit access to selected groups** - preferred for production; only assigned groups can use the app.

5. Click **Save**.

#### Copy credentials immediately

On the application **General** tab, record:

| Okta label | Copy to (Agentic Resource Registry SSO setting) |
|------------|---------------------------|
| **Client ID** | `oauth.client_id` |
| **Client secret** (click eye icon) | `oauth.client_secret` |
| **Okta domain** (from browser URL, e.g. `acme.okta.com`) | Used in metadata URL below |

**Metadata URL** (use the **default** custom authorization server):

```
https://<OKTA_DOMAIN>/oauth2/default/.well-known/openid-configuration
```

Example: `https://acme.okta.com/oauth2/default/.well-known/openid-configuration` → this becomes `oauth.server_metadata_url`.

6. Open that metadata URL in a browser; confirm it returns JSON with `authorization_endpoint` and `token_endpoint`. If it fails, stop and fix the domain before continuing.

---

### Step 3 - Configure application sign-on settings (optional but recommended)

1. Stay on the Agentic Resource Registry application.
2. Open the **Sign On** tab.
3. Under **OpenID Connect ID Token**, confirm:
   - **Issuer** shows the default authorization server (`/oauth2/default`)
   - **Audience** includes your Client ID (default for OIDC apps)
4. If your org uses **App sign-on policies** (Okta Identity Engine), attach the policy you want for this app (password only vs MFA). See **Step 8** for global authentication policies.

You do not need to change SAML settings; Agentic Resource Registry uses OIDC for this flow.

---

### Step 4 - Create groups (teams / departments)

Groups control both **who can access Agentic Resource Registry** (when assigned to the app) and **department data in the exec dashboard**.

1. Go to **Directory** → **Groups**.
2. Click **Add Group**.
3. For each team, create a group:

| Field | Example |
|-------|---------|
| **Name** | `engineering` |
| **Description** | `Acme engineering team` |

Repeat for every department you need (`product`, `design`, `data-science`, `platform`, etc.).

4. Click **Save** after each group.

**Tip:** Use short, lowercase names without spaces; they are stored as-is in Agentic Resource Registry’s `user_groups` table.

---

### Step 5 - Create people (users)

1. Go to **Directory** → **People**.
2. Click **Add Person**.
3. Fill in:

| Field | Guidance |
|-------|----------|
| **First name** / **Last name** | As provided by the customer |
| **Username** | Use work email, e.g. `alice@acme.com` |
| **Primary email** | Same as username |
| **Password** | **Set by admin** (send securely) or **Set by user** (invitation email) |

4. Click **Save**.
5. Repeat for every user who needs Agentic Resource Registry access.

**Activate users:** If the person is **Staged**, open their profile → **More Actions** → **Activate** before they can sign in.

---

### Step 6 - Add people to groups

For **each** group created in Step 4:

1. Go to **Directory** → **Groups**.
2. Click the group name (e.g. `engineering`).
3. Open the **People** tab (or **Assign people**, depending on UI version).
4. Click **Assign people**.
5. Search for each user who belongs to that team.
6. Click **+** (or checkbox) next to each user.
7. Click **Save** / **Done**.

Verify: each user appears under the correct group(s). Users can belong to multiple groups.

---

### Step 7 - Assign groups to the Agentic Resource Registry application

This step controls **who is allowed to sign in** to Agentic Resource Registry via SSO.

1. Go to **Applications** → **Applications**.
2. Click your **Agentic Resource Registry - &lt;Customer&gt;** application.
3. Open the **Assignments** tab.
4. Click **Assign** → **Assign to Groups**.
5. Select every group that should have Agentic Resource Registry access (e.g. `engineering`, `product`, `platform`).
6. Click **Assign**, then **Done** for each group.

**Optional - assign individual users:** Use **Assign** → **Assign to People** only for one-off access (e.g. executives not in a team group). Prefer group assignment for managed customers.

**Verify:** Under **Assignments**, you should see all intended groups (and any individual users) listed.

---

### Step 8 - Authentication policies (MFA and sign-on rules)

Agentic Resource Registry does not configure MFA itself; Okta enforces it here.

#### Option A - Application-specific rule (recommended)

1. Go to **Security** → **Authentication Policies** (or **Security** → **Global Session Policy** on Classic).
2. Open the policy that applies to your workforce (or create **Add a policy**).
3. Click **Add rule** (or edit an existing rule).
4. Set:

| Setting | Production | Dev / pilot |
|---------|------------|-------------|
| **If user is** | Assigned app / Any app | Same |
| **And user is** | Assigned to `Observal - <Customer>` | Same |
| **Then authenticate with** | Password + another factor (MFA) | Password only (if acceptable) |

5. **Save** the rule and ensure the policy is **Active**.

#### Option B - Sign-on policy on the app (Identity Engine)

1. Open the Agentic Resource Registry application → **Sign On** tab.
2. Under **Sign On Policy**, edit or add a rule for MFA requirements.

**Test implication:** If MFA is required, test users must complete MFA during the first SSO test (Step 14).

---

### Step 9 - Authorization server: `groups` scope and claim

Agentic Resource Registry requests scope `groups` and reads `userinfo.groups` from the token. Without this, login works but **departments stay empty**.

#### 9a - Add the `groups` scope (if missing)

1. Go to **Security** → **API**.
2. Click the **default** authorization server (issuer path `/oauth2/default`).
3. Open the **Scopes** tab.
4. If `groups` is **not** listed:
   - Click **Add Scope**
   - **Name:** `groups`
   - **Description:** `Access user group memberships`
   - Check **Include in public metadata**
   - Click **Create**

#### 9b - Add the `groups` claim to the ID token

1. Still on the **default** authorization server, open the **Claims** tab.
2. Click **Add Claim**.
3. Set:

| Field | Value |
|-------|-------|
| **Name** | `groups` |
| **Include in token type** | **ID Token** → **Always** |
| **Value type** | **Groups** |
| **Filter** | Matches regex: `.*` (all groups) - or restrict to your Agentic Resource Registry group names |
| **Include in** | **Any scope** |

4. Click **Create**.

#### 9c - Allow the client to use `groups` (access policy)

1. On the same **default** authorization server, open the **Access Policies** tab.
2. Open the active policy (often named **Default Policy**).
3. Open the policy rule (or **Add rule**).
4. Ensure:
   - **Grant type** includes **Authorization Code**
   - **Scopes** include at least: `openid`, `profile`, `email`, `groups`
   - The rule applies to **All clients** or explicitly includes your Agentic Resource Registry Client ID
5. **Save** the rule.

---

### Step 10 - Trust and client authentication (verify defaults)

1. On the Agentic Resource Registry application, open the **General** tab.
2. Scroll to **Client Credentials** / **Client authentication**:
   - **Client authentication:** **Client secret** (default for web app)
   - Secret should match what you copied to `oauth.client_secret`
3. Under **Login**, confirm:
   - **Login initiated by:** **App Only** (or equivalent) - users start from Agentic Resource Registry, not Okta dashboard tile only
   - **Application visibility:** your choice (hide from Okta dashboard if you only want Agentic Resource Registry-initiated login)

No change needed unless your security team requires PKCE-only public clients (Agentic Resource Registry server uses confidential client + secret).

---

## Part B - Agentic Resource Registry server configuration

Complete Okta Part A before saving Agentic Resource Registry SSO settings, so redirect URIs and secrets are ready.

### Step 11 - Set OAuth in the SSO tab

Configure OIDC in **Admin → SSO → SSO settings**.

| Setting | Value |
| --- | --- |
| `oauth.client_id` | `<client id>` |
| `oauth.client_secret` | `<client secret>` |
| `oauth.server_metadata_url` | `https://<OKTA_DOMAIN>/oauth2/default/.well-known/openid-configuration` |

Restart the stack after saving OIDC settings:

```bash
make down && make up
```

(or your equivalent: `docker compose down && docker compose up -d`)

---

### Step 12 - Set Frontend URL in Settings (must match Okta)

The API builds the OAuth redirect from **`deployment.frontend_url`**, which you set in the web UI, not in `.env`.

1. Sign in to Agentic Resource Registry as an admin (bootstrap or existing admin account).
2. Open **Settings** (admin area).
3. Under **Deployment**, set **Frontend URL** to exactly:
   ```
   https://<OBSERVAL_DOMAIN>
   ```
   - Must match the host used in Okta sign-in redirect URIs (Step 2)
   - No trailing slash
4. Click **Save**.

If this value is wrong, you will see `redirect_uri mismatch` or `mismatching_state` errors.

---

### Step 13 - Confirm SSO is enabled on the server

```bash
curl -s https://<OBSERVAL_DOMAIN>/api/v1/config/public | jq '.sso_enabled'
```

Expected: `true`.

If `false`:

- Verify all three `OAUTH_*` variables are set
- Restart `observal-api`
- Check API logs for metadata fetch errors

---

## Part C - Verification (follow in order)

### Step 14 - Test SSO login (first user)

1. Open a **private/incognito** browser window.
2. Go to `https://<OBSERVAL_DOMAIN>/login`.
3. Click **Sign in with SSO** (or **Sign in via browser**).
4. You should be redirected to Okta (`/oauth2/default/v1/authorize` in the URL).
5. Sign in as a user who is:
   - Active in Okta
   - Assigned to the Agentic Resource Registry app (via group or direct assignment)
6. Complete MFA if your policy requires it.
7. You should land back on Agentic Resource Registry (`/login?code=...` briefly, then the app home).

**Expected failures if misconfigured:**

| Symptom | Likely cause |
|---------|----------------|
| Okta error `redirect_uri` mismatch | Redirect URI in app ≠ `https://<domain>/api/v1/auth/oauth/callback` or Frontend URL wrong |
| Okta “You are not allowed to access this app” | User/group not assigned in Step 7 |
| Agentic Resource Registry “OAuth authorization failed” | Wrong client secret or metadata URL |
| Login works, no departments | Missing `groups` claim / scope (Step 9) |

---

### Step 15 - Verify groups synced

1. Log in as a user who belongs to Okta groups (e.g. `engineering`).
2. In Agentic Resource Registry, open the **exec dashboard** or check admin user tooling.
3. Confirm department/group filters reflect Okta group names.

Groups refresh on **each SSO login**. If you change group membership in Okta, have the user log out and sign in again.

---

### Step 16 - Promote the customer admin (required)

**SSO users are created with role `user` by default.** They are not admins automatically.

1. As an existing Agentic Resource Registry admin, go to **Admin** → **Users**.
2. Find the customer’s admin user (the email they used for SSO).
3. Change role to **admin** or **super_admin** as appropriate.

Do this for at least one person per customer instance before enabling SSO-only mode.

---

### Step 17 - Optional: lock to SSO only

Only after Steps 14–16 succeed:

1. **Settings** → **Deployment** → enable **SSO Only Mode**.
2. Confirm password login is disabled for normal users.
3. Document CLI access: users run `observal auth login` with device flow (see `docs/self-hosting/cli-sso.md`).

---

## Part D - Optional follow-ups

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `redirect_uri mismatch` | Okta URI ≠ `deployment.frontend_url` + `/api/v1/auth/oauth/callback` | Align Okta app URIs and Admin **Frontend URL** |
| `mismatching_state` / CSRF | Frontend URL changed mid-login, or API restarted during flow | Set Frontend URL, retry in fresh incognito window |
| Okta “access denied” for app | User not assigned | Step 7 - assign group or person |
| `OAuth authorization failed` | Bad secret, wrong metadata URL, or network block to Okta | Re-copy secret; verify metadata JSON in browser from API host |
| `Email claim is missing` | ID token missing email | Ensure `email` scope allowed; user has primary email in Okta |
| `Resource not found: AuthenticatorEnrollment` | Broken MFA enrollment | Okta: **Directory** → user → **More Actions** → **Reset Multifactor** |
| User logs in but no admin pages | Default SSO role is `user` | Step 16 - promote role in Agentic Resource Registry |
| Groups empty in dashboard | No `groups` in ID token | Repeat Step 9; user must log in again |
| `sso_enabled` is false | OIDC settings missing or API not restarted | Step 11, set all three settings and restart API |

---

## Related documentation

- [Authentication and SSO](/self-hosting/authentication) - JWT, RBAC, SSO behavior
- [Configuration](/self-hosting/configuration) - `.env` reference
- `docs/self-hosting/oidc-setup.md` - OIDC reference (multi-IdP)
- `docs/self-hosting/cli-sso.md` - CLI login when SSO-only is enabled
