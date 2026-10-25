---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "SAML 2.0 Setup Guide"
---

This guide walks you through configuring SAML 2.0 Single Sign-On (SSO) for
Observal. It covers identity provider (IdP) configuration, service
provider (SP) metadata, and troubleshooting common issues.

---

## 1. Prerequisites

Before you begin, make sure the following requirements are met:

- **Admin access.** You need a super-admin account in Observal to configure SAML settings.
- **IdP admin access.** You need administrator privileges on your identity
  provider (Okta, Azure AD / Entra ID, Google Workspace, or another
  SAML 2.0-compliant IdP) to create and configure a SAML application.
- **HTTPS is required.** The Observal instance must be served over HTTPS.
  SAML assertions are security-sensitive, and IdPs will reject ACS URLs that
  use plain HTTP.

---

## 2. Quick Start: Environment Variables

Configure SAML in **Admin → SSO → SSO settings**. The table below lists every supported dynamic setting.

### IdP Settings (required)

| Setting | Description |
|---|---|
| `saml.idp_entity_id` | The entity ID of your identity provider. Sometimes called the "Issuer." |
| `saml.idp_sso_url` | The IdP's Single Sign-On URL where authentication requests are sent. |
| `saml.idp_x509_cert` | The PEM-encoded X.509 certificate used to verify IdP signatures. Paste the full certificate including the `BEGIN` and `END` lines. |

### IdP Settings (optional)

| Setting | Description |
|---|---|
| `saml.idp_slo_url` | The IdP's Single Logout URL. If set, Observal will send logout requests to the IdP when users sign out. |
| `saml.idp_metadata_url` | A URL pointing to the IdP's SAML metadata XML. When provided, Observal will periodically fetch metadata to keep certificates and endpoints up to date. |

### SP Settings

| Setting | Description | Default |
|---|---|---|
| `saml.sp_entity_id` | The entity ID that Observal uses to identify itself to the IdP. | Auto-derived from `FRONTEND_URL` (appends `api/v1/sso/saml/metadata`)|
| `saml.sp_acs_url` | The Assertion Consumer Service URL where the IdP posts SAML responses. | Auto-derived from `FRONTEND_URL` (appends `/api/v1/sso/saml/acs`) |
| `saml.sp_key_encryption_password` | Password protecting the SP's private key, used for encrypted assertions. Only required if your IdP sends encrypted SAML assertions. | (none) |

### Provisioning and Roles

| Setting | Description | Default |
|---|---|---|
| `saml.jit_provisioning` | Enable Just-In-Time user provisioning. When `true`, users who authenticate via SAML are automatically created in Observal on first login. | `true` |
| `saml.default_role` | The role assigned to JIT-provisioned users. | `user` |

### Minimal Example

In **Admin → SSO → SSO settings**, set:

| Setting | Value |
| --- | --- |
| `saml.idp_entity_id` | `https://idp.example.com/saml` |
| `saml.idp_sso_url` | `https://idp.example.com/saml/sso` |
| `saml.idp_x509_cert` | The PEM certificate from your IdP |

With these three settings saved, SAML SSO is ready.

---

## 3. Retrieving SP Metadata

Observal exposes its SP metadata as an XML document at:

```
GET /api/v1/sso/saml/metadata
```

Open this URL in your browser or fetch it with `curl`:

```bash
curl https://observal.example.com/api/v1/sso/saml/metadata
```

The response is a standard SAML 2.0 `EntityDescriptor` XML document containing:

- The SP entity ID
- The Assertion Consumer Service (ACS) URL
- The SP's public signing certificate (if configured)

Most IdPs allow you to import this metadata XML directly, which auto-fills the
SP configuration on the IdP side.

---

## 4. IdP-Specific Setup

### 4.1 Okta

1. In the Okta admin console, go to **Applications > Create App Integration**.
2. Select **SAML 2.0** and click **Next**.
3. Enter an app name (e.g., "Observal") and click **Next**.
4. Fill in the SAML settings:
   - **Single sign-on URL:** Your ACS URL, e.g.,
     `https://observal.example.com/api/v1/sso/saml/acs`
   - **Audience URI (SP Entity ID):** Your SP entity ID, e.g.,
     `https://observal.example.com`
   - **Name ID format:** `EmailAddress`
   - **Application username:** `Email`
5. Under **Attribute Statements**, map the following:
   - `email` to `user.email`
   - `firstName` to `user.firstName`
   - `lastName` to `user.lastName`
6. Click **Next**, then **Finish**.
7. On the application's **Sign On** tab, copy the following values into your
   Observal environment:
   - **Identity Provider Issuer** to `saml.idp_entity_id`
   - **Identity Provider Single Sign-On URL** to `saml.idp_sso_url`
   - **X.509 Certificate** to `saml.idp_x509_cert`
8. Assign users or groups to the Okta application.

### 4.2 Azure AD (Entra ID)

1. In the Azure portal, go to **Microsoft Entra ID > Enterprise applications**.
2. Click **New application > Create your own application**.
3. Name the application (e.g., "Observal"), select **Integrate any other
   application you don't find in the gallery**, and click **Create**.
4. Under **Single sign-on**, select **SAML**.
5. In **Basic SAML Configuration**, set:
   - **Identifier (Entity ID):** Your SP entity ID, e.g.,
     `https://observal.example.com`
   - **Reply URL (ACS URL):**
     `https://observal.example.com/api/v1/sso/saml/acs`
   - **Sign on URL:**
     `https://observal.example.com/api/v1/sso/saml/login`
6. Under **Attributes & Claims**, configure:
   - `email` mapped to `user.mail`
   - `firstName` mapped to `user.givenname`
   - `lastName` mapped to `user.surname`
7. In **SAML Certificates**, download the **Certificate (Base64)**.
8. Copy the following from **Set up Observal**:
   - **Microsoft Entra Identifier** to `saml.idp_entity_id`
   - **Login URL** to `saml.idp_sso_url`
   - **Logout URL** to `saml.idp_slo_url`
   - Paste the downloaded certificate contents into `saml.idp_x509_cert`
9. Assign users and groups to the enterprise application.

Alternatively, you can set `saml.idp_metadata_url` to the **App Federation
Metadata Url** from the SAML Certificates section. Observal will auto-configure
from metadata.

### 4.3 Google Workspace

1. In the Google Admin console, go to **Apps > Web and mobile apps**.
2. Click **Add app > Add custom SAML app**.
3. Name the application (e.g., "Observal") and click **Continue**.
4. On the **Google Identity Provider details** page, copy:
   - **SSO URL** to `saml.idp_sso_url`
   - **Entity ID** to `saml.idp_entity_id`
   - **Certificate** to `saml.idp_x509_cert`
5. Click **Continue**.
6. In **Service provider details**, set:
   - **ACS URL:**
     `https://observal.example.com/api/v1/sso/saml/acs`
   - **Entity ID:** Your SP entity ID, e.g.,
     `https://observal.example.com`
   - **Name ID format:** `EMAIL`
   - **Name ID:** `Basic Information > Primary email`
7. Click **Continue**.
8. Under **Attribute mapping**, add:
   - `email` mapped to `Basic Information > Primary email`
   - `firstName` mapped to `Basic Information > First name`
   - `lastName` mapped to `Basic Information > Last name`
9. Click **Finish**.
10. Turn the application **ON** for the relevant organizational units.

---

## 5. Admin API Configuration

As an alternative to the SSO tab, admins can manage SAML configuration through the REST API. This allows runtime changes without restarting the server.

### View Current Config

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://observal.example.com/api/v1/admin/saml-config
```

Returns the current SAML configuration with sensitive fields (private keys,
certificates) redacted. The `source` field indicates whether config comes from
`dynamic`, `database`, or `none`.

### Create or Update Config

```bash
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "idp_entity_id": "https://idp.example.com/saml",
    "idp_sso_url": "https://idp.example.com/saml/sso",
    "idp_slo_url": "https://idp.example.com/saml/slo",
    "idp_x509_cert": "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
    "jit_provisioning": true,
    "default_role": "user"
  }' \
  https://observal.example.com/api/v1/admin/saml-config
```

The SP key pair is auto-generated on first creation. To regenerate the SP key
pair (e.g., after a key compromise), include `"regenerate_sp_key": true` in the
request body.

### Delete Config

```bash
curl -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://observal.example.com/api/v1/admin/saml-config
```

Deleting the SAML config disables SAML SSO immediately. Users who were using
SAML will need to use password-based login until SAML is reconfigured.

---

## 6. SSO Flows

### SP-Initiated SSO

In SP-initiated SSO, the user starts at Observal and is redirected to the IdP
for authentication.

1. The user visits the Observal login page and clicks **Sign in with SAML SSO**.
2. Observal redirects the browser to the IdP's SSO URL with a SAML
   `AuthnRequest`:
   ```
   GET /api/v1/sso/saml/login
   ```
3. The IdP authenticates the user (prompting for credentials if no session
   exists).
4. The IdP posts a signed SAML response to Observal's ACS endpoint:
   ```
   POST /api/v1/sso/saml/acs
   ```
5. Observal validates the SAML assertion, checks for replay attacks, creates or
   updates the user session, and redirects to the application.

### IdP-Initiated SSO

In IdP-initiated SSO, the user starts at the IdP (e.g., clicks the Observal
tile in Okta or the Google Workspace app launcher).

1. The user clicks the Observal application in the IdP dashboard.
2. The IdP posts an unsolicited SAML response directly to Observal's ACS
   endpoint:
   ```
   POST /api/v1/sso/saml/acs
   ```
3. Observal validates the assertion, creates or updates the user session, and
   redirects to the application.

Both flows use the same ACS endpoint. The difference is that IdP-initiated
responses do not contain an `InResponseTo` attribute, since there was no
originating `AuthnRequest`.

### Single Logout (SLO)

When an IdP SLO URL is configured (via `saml.idp_slo_url` or the admin API),
Observal supports SP-initiated logout:

```
GET /api/v1/sso/saml/logout
```

This endpoint:
1. Generates a SAML LogoutRequest
2. Redirects the user to the IdP's SLO endpoint
3. The IdP terminates the session and redirects back to Observal's SLS callback:
   ```
   GET /api/v1/sso/saml/sls
   ```
4. Observal processes the LogoutResponse and redirects to the login page

If no SLO URL is configured, the logout endpoint redirects directly to the
login page (local session only).

### Assertion Replay Protection

Observal tracks SAML assertion IDs in Redis with a 5-minute TTL. If the same
assertion is submitted twice (e.g., by an attacker replaying a captured SAML
response), the second attempt is rejected with a 400 error and a security event
is logged.

---

## 7. Just-In-Time (JIT) Provisioning

When `saml.jit_provisioning` is set to `true` (the default), Observal
automatically creates user accounts on first SAML login. Here is how it works:

1. A user authenticates via their IdP for the first time.
2. The SAML assertion arrives at the ACS endpoint containing the user's email,
   first name, and last name.
3. Observal checks whether a user with that email already exists.
   - If the user exists, their session is created and they are logged in.
   - If the user does not exist, a new account is created with the role
     specified by `saml.default_role` (default: `user`), and they are logged in.

To disable JIT provisioning, set `saml.jit_provisioning=false`. With JIT
disabled, only pre-existing users can log in via SAML. Unrecognized users will
see a 403 Forbidden error.

---

## 8. Troubleshooting

### 404 Not Found on SAML endpoints

**Symptom:** Requests to `/api/v1/sso/saml/login`, `/api/v1/sso/saml/acs`, or
`/api/v1/sso/saml/metadata` return a 404.

**Cause:** The server is not running the expected release, or SAML routes failed to load.

**Fix:**
- Confirm the API is healthy and running the same version as the web UI.
- Restart the Observal API.
- Check server logs for SAML route startup errors.

### 400 Bad Request on ACS

**Symptom:** The IdP redirects back to Observal, but the ACS endpoint returns a
400 error.

**Common causes and fixes:**

- **Mismatched ACS URL.** The ACS URL configured in the IdP must exactly match
  the value Observal expects. Check `saml.sp_acs_url` or the auto-derived
  value at `/api/v1/sso/saml/metadata`.
- **Invalid or expired IdP certificate.** If the certificate in
  `saml.idp_x509_cert` does not match the one the IdP used to sign the
  assertion, validation will fail. Re-download the certificate from your IdP
  and update the environment variable.
- **Clock skew.** SAML assertions include timestamps with a validity window.
  If the server clock is more than a few minutes off from the IdP, assertions
  will be rejected. Ensure NTP is running on your server.
- **Malformed SAML response.** Check the server logs for the full error
  message, which will indicate whether the issue is with signature validation,
  decryption, or XML parsing.

### 403 Forbidden after successful IdP authentication

**Symptom:** The user authenticates at the IdP, the SAML response is valid, but
Observal returns a 403.

**Common causes and fixes:**

- **JIT provisioning is disabled.** If `saml.jit_provisioning=false` and the
  user does not have a pre-existing account in Observal, access is denied.
  Either create the user manually or enable JIT provisioning.
- **User is deactivated.** If the user's account exists but has been
  deactivated in Observal, SAML login will be rejected. Reactivate the user
  in the admin panel.
- **Missing email attribute.** The SAML assertion must include an email
  attribute. If the IdP is not sending one, Observal cannot identify the user.
  Verify your IdP attribute mappings include `email`.

### General Debugging Tips

- **Enable debug logging.** Set `LOG_LEVEL=debug` to see detailed SAML
  processing logs, including the raw assertion XML and validation steps.
- **Inspect the SAML response.** Use a browser extension such as "SAML-tracer"
  to capture the Base64-encoded SAML response. Decode it to inspect the
  assertion contents.
- **Validate metadata.** Fetch your SP metadata from
  `/api/v1/sso/saml/metadata` and compare it with what is configured in your
  IdP. Entity IDs and ACS URLs must match exactly.
- **Test with SP-initiated flow first.** SP-initiated SSO is easier to debug
  because you can see the full request/response cycle. Once SP-initiated SSO
  works, IdP-initiated SSO typically works as well.
