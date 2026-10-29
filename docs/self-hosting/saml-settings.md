---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "SAML 2.0 SSO Configuration"
---

Configure SAML-based Single Sign-On to allow users to authenticate via your organization's identity provider (Okta, Azure AD, OneLogin, Google Workspace, etc.).

> For a step-by-step Okta setup guide, see the [Okta Setup Guide](/self-hosting/okta-setup).

## Prerequisites

- An active Agentic Resource Registry license with the `saml` feature enabled
- Admin access to your identity provider
- The Agentic Resource Registry instance must be reachable over HTTPS (SAML assertions are signed and validated)

## Quick Setup

1. In your IdP, create a new SAML 2.0 application
2. Set the ACS URL to: `https://YOUR_DOMAIN/api/v1/sso/saml/acs`
3. Set the Entity ID to: `https://YOUR_DOMAIN/api/v1/sso/saml/metadata`
4. Copy the IdP Entity ID, SSO URL, and X.509 certificate from your IdP
5. Paste them into the Agentic Resource Registry Settings > SAML section
6. Test with a non-admin user before enabling SSO-only mode

## Field Reference

### IdP Entity ID

The unique identifier of your identity provider, found in the IdP's SAML metadata.

| Value | Example |
|-------|---------|
| Okta | `http://www.okta.com/exk1234567890` |
| Azure AD | `https://sts.windows.net/{tenant-id}/` |
| Google | `https://accounts.google.com/o/saml2?idpid=C01234567` |

### IdP SSO URL

The URL where Agentic Resource Registry redirects users to authenticate. This is the SingleSignOnService Location with HTTP-Redirect binding from your IdP's metadata.

**Affects:** The redirect target when a user clicks "Sign in with SSO". If incorrect, users see a 404 or error page at the IdP.

### IdP SLO URL

The URL for SAML Single Logout (optional).

**Affects:** When set, logging out of Agentic Resource Registry also triggers a logout at the IdP, ending all SSO sessions. When blank, only the Agentic Resource Registry session is terminated.

**When to set:** Deployments that require centralized session termination, for example when an employee is offboarded and all sessions must end immediately.

### IdP Certificate

The IdP's public X.509 certificate in PEM format, used to verify SAML assertion signatures.

**Affects:** Security of the SAML flow. Without a valid certificate, Agentic Resource Registry cannot verify that assertions actually came from your IdP. Invalid or expired certificates cause all SSO logins to fail.

**Format:** PEM-encoded, including the `-----BEGIN CERTIFICATE-----` and `-----END CERTIFICATE-----` markers.

**Certificate rotation:** When your IdP rotates certificates, update this field immediately. Some IdPs publish both old and new certificates during a transition period. Agentic Resource Registry validates against the configured certificate only.

### IdP Metadata URL

URL to your IdP's SAML metadata XML document (optional).

**Affects:** When provided, Agentic Resource Registry can auto-populate the Entity ID, SSO URL, and certificate from the metadata. Useful for IdPs that rotate certificates automatically.

**When to set:** If your IdP publishes metadata at a stable URL and you want automatic certificate rotation handling.

### SP Entity ID

The entity ID that identifies Agentic Resource Registry to your identity provider.

**Default:** `https://YOUR_DOMAIN/api/v1/sso/saml/metadata`

**Affects:** Must exactly match what you configured in your IdP's SAML application settings. A mismatch causes "Audience validation failed" errors.

### SP ACS URL

The Assertion Consumer Service endpoint where your IdP sends SAML responses after authentication.

**Default:** `https://YOUR_DOMAIN/api/v1/sso/saml/acs`

**Affects:** Must exactly match the ACS URL registered in your IdP. A mismatch causes authentication to silently fail (the IdP sends the response to the wrong URL).

### JIT Provisioning

Automatically create Agentic Resource Registry user accounts when someone authenticates via SAML for the first time.

| Value | Effect |
|-------|--------|
| `true` (default) | New users are created on first SSO login with the configured default role |
| `false` | Users must be pre-created manually before they can log in via SSO |

**When to disable:** Deployments that want explicit control over who can access Agentic Resource Registry and prefer manual user creation.

### Default Role

The role assigned to users created via JIT provisioning.

| Value | Effect |
|-------|--------|
| `user` (default) | Standard access: can view registry, install agents, view own traces |
| `reviewer` | Can additionally approve/reject component submissions |
| `admin` | Full admin access (use with caution for JIT) |

**Best practice:** Use `user` and promote individuals manually. Assigning `admin` via JIT means anyone with an IdP account gets admin access.

### SP Key Password

Password used to encrypt the auto-generated SP private key at rest in the database.

**Affects:** Security of the SAML SP private key. The key is used to sign SAML requests and decrypt encrypted assertions.

**When to set:** Always set in production. If blank, the private key is stored unencrypted in the database.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Audience validation failed" | SP Entity ID mismatch | Ensure SP Entity ID matches what's in your IdP |
| "Signature validation failed" | Wrong or expired IdP certificate | Update the certificate from your IdP |
| Users can't log in after IdP cert rotation | Old certificate still configured | Replace with the new certificate |
| SSO works but users get "Unauthorized" | JIT provisioning disabled and user doesn't exist | Enable JIT or pre-create the user |
| Redirect loops after login | Frontend URL misconfigured | Ensure Frontend URL matches your actual domain |
