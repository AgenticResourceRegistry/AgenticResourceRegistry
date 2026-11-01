---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "SCIM 2.0 Provisioning Setup Guide"
---

This guide covers configuring SCIM 2.0 user provisioning between your identity provider (IdP) and Agentic Resource Registry. SCIM automates user creation, updates, and deprovisioning so your Agentic Resource Registry user directory stays synchronized with your IdP.

---

## Prerequisites

Before configuring SCIM, verify the following:

1. **SAML or OIDC SSO is configured.** Users provisioned via SCIM will authenticate through your IdP. Ensure SSO login works before enabling SCIM.
2. **Admin database access.** You will need `psql` access to the Agentic Resource Registry PostgreSQL database to insert a SCIM bearer token.

---

## Generating a SCIM Bearer Token

Agentic Resource Registry authenticates SCIM requests using a bearer token. The raw token is sent
by your IdP in the `Authorization` header; only the SHA-256 hash is stored in
the database.

### Option A: Admin API (recommended)

Use the admin API to generate and manage tokens without direct database access:

```bash
# Create a new SCIM token
curl -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "Primary SCIM token for Okta"}' \
  https://your-observal-instance.example.com/api/v1/admin/scim-tokens
```

The response includes the plaintext token. **Save it immediately -- it will not
be shown again.**

```json
{
  "id": "a1b2c3d4-...",
  "token": "oXk9Qm7...",
  "description": "Primary SCIM token for Okta",
  "message": "Save this token now. It will not be shown again."
}
```

To list active tokens (metadata only, no plaintext):

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://your-observal-instance.example.com/api/v1/admin/scim-tokens
```

To revoke a token:

```bash
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://your-observal-instance.example.com/api/v1/admin/scim-tokens/{token-id}
```

### Option B: Manual Database Insert

For environments without admin API access, you can insert tokens directly:

```bash
export SCIM_TOKEN=$(openssl rand -hex 32)
echo "Save this token securely: $SCIM_TOKEN"
export SCIM_TOKEN_HASH=$(echo -n "$SCIM_TOKEN" | sha256sum | awk '{print $1}')
```

```sql
INSERT INTO scim_tokens (id, token_hash, description, active, created_at)
VALUES (
  gen_random_uuid(),
  '<SCIM_TOKEN_HASH>',
  'Primary SCIM token for Okta',
  true,
  now()
);
```

### Verify the Token

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  https://your-observal-instance.example.com/api/v1/scim/Users
```

A `200` response confirms the token is valid. A `401` response indicates the
token hash does not match any active record.

> **Important:** Store the raw token value securely. It cannot be recovered from
> the database, which only stores the hash.

---

## Identity Provider Configuration

### Okta

1. In the Okta admin console, go to **Applications > Applications** and select your Agentic Resource Registry SAML application.
2. Navigate to the **Provisioning** tab and click **Configure API Integration**.
3. Check **Enable API Integration**.
4. Set the following values:
   - **SCIM connector base URL:** `https://your-observal-instance.example.com/api/v1/scim`
   - **Unique identifier field for users:** `userName`
   - **Authentication Mode:** HTTP Header
   - **Authorization:** paste the raw bearer token from Step 1 above
5. Click **Test API Credentials** to verify connectivity.
6. Under **Provisioning > To App**, enable:
   - Create Users
   - Update User Attributes
   - Deactivate Users
7. Go to the **Assignments** tab and assign users or groups. Okta will begin provisioning automatically.

### Azure AD (Microsoft Entra ID)

1. In the Azure portal, go to **Microsoft Entra ID > Enterprise applications** and select your Agentic Resource Registry application.
2. Navigate to **Provisioning** and set **Provisioning Mode** to **Automatic**.
3. Under **Admin Credentials**, enter:
   - **SCIM URL:** `https://your-observal-instance.example.com/api/v1/scim`
   - **Secret Token:** paste the raw bearer token
4. Click **Test Connection** to validate.
5. Under **Mappings**, configure attribute mappings. The required attributes are:
   - `userName` mapped to the user's email address
   - `name.givenName` mapped to the first name
   - `name.familyName` mapped to the last name
   - `active` mapped to the account enabled status
6. Set the **Scope** to **Sync only assigned users and groups** (recommended).
7. Set **Provisioning Status** to **On** and save.

Azure AD runs provisioning cycles approximately every 40 minutes. You can trigger an on-demand cycle from the **Provisioning** page.

### Google Workspace

Google Workspace does not natively support outbound SCIM provisioning. To synchronize Google Workspace users with Agentic Resource Registry, you have two options:

**Option A: Use a third-party bridge.**
Services such as [SSPM tools](https://workspace.google.com/marketplace) or custom middleware can read the Google Directory API and push changes to Agentic Resource Registry's SCIM endpoints. Configure the bridge with the SCIM base URL and bearer token as described above.

**Option B: Script the Google Directory API.**
Write a scheduled script (cron job, Cloud Function, or Cloud Scheduler) that:

1. Lists users from the Google Admin SDK Directory API.
2. Compares against the current Agentic Resource Registry user list via `GET /api/v1/scim/Users`.
3. Calls `POST /api/v1/scim/Users` for new users and `PUT /api/v1/scim/Users/{id}` with `"active": false` for removed users.

Example skeleton using `curl`:

```bash
# Fetch all current SCIM users
curl -H "Authorization: Bearer $SCIM_TOKEN" \
  https://your-observal-instance.example.com/api/v1/scim/Users

# Create a new user
curl -X POST \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  -H "Content-Type: application/scim+json" \
  -d '{"schemas":["urn:ietf:params:scim:schemas:core:2.0:User"],"userName":"alice@example.com","name":{"givenName":"Alice","familyName":"Smith"},"active":true}' \
  https://your-observal-instance.example.com/api/v1/scim/Users
```

---

## SCIM API Reference

All SCIM endpoints require a valid bearer token in the `Authorization` header:

```
Authorization: Bearer <your-scim-token>
```

Responses use the `application/scim+json` content type and follow [RFC 7644](https://datatracker.ietf.org/doc/html/rfc7644).

### List Users

```
GET /api/v1/scim/Users
```

| Parameter    | Type   | Default | Description                                                        |
|--------------|--------|---------|--------------------------------------------------------------------|
| `startIndex` | int    | 1       | 1-based index for pagination (minimum 1)                           |
| `count`      | int    | 100     | Maximum number of users to return (maximum 500)                    |
| `filter`     | string | (none)  | SCIM filter expression (see Filter Support below)                  |

**Filter Support:**

The following filter operators are supported on the `userName` attribute:

| Expression | Description |
|---|---|
| `userName eq "alice@example.com"` | Exact match |
| `userName ne "admin@example.com"` | Not equal |
| `userName sw "alice"` | Starts with |
| `userName co "example"` | Contains |

**Example: list all users**

```bash
curl -H "Authorization: Bearer $SCIM_TOKEN" \
  "https://your-observal-instance.example.com/api/v1/scim/Users?startIndex=1&count=50"
```

**Example: filter by email**

```bash
curl -H "Authorization: Bearer $SCIM_TOKEN" \
  'https://your-observal-instance.example.com/api/v1/scim/Users?filter=userName%20eq%20%22alice@example.com%22'
```

**Response (200):**

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
  "totalResults": 1,
  "itemsPerPage": 1,
  "startIndex": 1,
  "Resources": [
    {
      "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "userName": "alice@example.com",
      "name": {
        "givenName": "Alice",
        "familyName": "Smith",
        "formatted": "Alice Smith"
      },
      "displayName": "Alice Smith",
      "emails": [{"value": "alice@example.com", "primary": true, "type": "work"}],
      "active": true,
      "meta": {
        "resourceType": "User",
        "created": "2026-04-20T14:30:00+00:00",
        "location": "https://your-observal-instance.example.com/api/v1/scim/Users/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
      }
    }
  ]
}
```

### Create User

```
POST /api/v1/scim/Users
```

**Request body:**

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "userName": "bob@example.com",
  "name": {
    "givenName": "Bob",
    "familyName": "Jones"
  },
  "emails": [
    {"value": "bob@example.com", "primary": true, "type": "work"}
  ],
  "active": true
}
```

**Example:**

```bash
curl -X POST \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "bob@example.com",
    "name": {"givenName": "Bob", "familyName": "Jones"},
    "emails": [{"value": "bob@example.com", "primary": true, "type": "work"}],
    "active": true
  }' \
  https://your-observal-instance.example.com/api/v1/scim/Users
```

**Response (201):** Returns the created user resource with a server-assigned `id`.

**Response (409):** Returned if a user with the same email already exists.

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
  "status": "409",
  "detail": "User with email bob@example.com already exists"
}
```

### Get User

```
GET /api/v1/scim/Users/{id}
```

| Parameter | Type | Description                   |
|-----------|------|-------------------------------|
| `id`      | UUID | The Agentic Resource Registry user ID (UUID)   |

**Example:**

```bash
curl -H "Authorization: Bearer $SCIM_TOKEN" \
  https://your-observal-instance.example.com/api/v1/scim/Users/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response (200):** Returns the full SCIM user resource.

**Response (404):** Returned if the user ID does not exist.

### Update User

```
PUT /api/v1/scim/Users/{id}
```

This performs a full replacement of the user resource. To deactivate a user, set `"active": false` in the request body. Deactivation clears the user's password hash and sets their auth provider to `deactivated`, preventing all login.

**Example: deactivate a user**

```bash
curl -X PUT \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "bob@example.com",
    "name": {"givenName": "Bob", "familyName": "Jones"},
    "active": false
  }' \
  https://your-observal-instance.example.com/api/v1/scim/Users/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response (200):** Returns the updated user resource. The `active` field will reflect the new value.

**Response (404):** Returned if the user ID does not exist.

### Delete User

```
DELETE /api/v1/scim/Users/{id}
```

Permanently removes the user record from the database. This is irreversible. If your IdP supports it, prefer deactivation (`PUT` with `"active": false`) over deletion.

**Example:**

```bash
curl -X DELETE \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  https://your-observal-instance.example.com/api/v1/scim/Users/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response (204):** No content. The user has been deleted.

**Response (404):** Returned if the user ID does not exist.

### Partial Update (PATCH)

```
PATCH /api/v1/scim/Users/{id}
```

Performs a partial update using SCIM PatchOp format (RFC 7644 Section 3.5.2).
This is the preferred update method for Okta and Azure AD.

**Supported operations:**

| Operation | Paths | Description |
|---|---|---|
| `replace` | `displayName`, `name.givenName`, `name.familyName`, `userName`, `emails`, `active` | Update a field |
| `add` | Same as replace | Treated as replace for single-valued attributes |
| `remove` | (none) | Returns 400 (required fields cannot be removed) |

**Example: update display name**

```bash
curl -X PATCH \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
    "Operations": [
      {"op": "replace", "path": "displayName", "value": "Jane Doe"}
    ]
  }' \
  https://your-observal-instance.example.com/api/v1/scim/Users/{id}
```

**Example: deactivate a user via PATCH**

```bash
curl -X PATCH \
  -H "Authorization: Bearer $SCIM_TOKEN" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
    "Operations": [
      {"op": "replace", "path": "active", "value": false}
    ]
  }' \
  https://your-observal-instance.example.com/api/v1/scim/Users/{id}
```

### Discovery Endpoints

These endpoints do not require authentication and are used by IdPs during SCIM
configuration to discover server capabilities.

**Service Provider Configuration:**

```
GET /api/v1/scim/ServiceProviderConfig
```

Returns supported SCIM features (patch, filter, bulk, authentication schemes).

**Schemas:**

```
GET /api/v1/scim/Schemas
```

Returns the User schema definition with all supported attributes.

**Resource Types:**

```
GET /api/v1/scim/ResourceTypes
```

Returns the list of supported resource types (currently only `User`).

---

## How SCIM Works with SAML

SCIM and SAML serve complementary purposes. SAML handles authentication (who the user is), while SCIM handles provisioning (managing user lifecycle).

| Aspect              | SAML 2.0                              | SCIM 2.0                                    |
|---------------------|---------------------------------------|----------------------------------------------|
| **Purpose**         | Single sign-on authentication         | User lifecycle provisioning                  |
| **Direction**       | User-initiated (browser redirect)     | IdP-initiated (server-to-server API calls)   |
| **Protocol**        | XML-based assertions                  | REST/JSON API                                |
| **When it runs**    | At login time                         | On schedule or on user changes in the IdP    |
| **Creates users**   | Only on first login (JIT provisioning)| Proactively, before the user ever logs in    |
| **Deactivates users**| No (cannot revoke access)            | Yes, via `active: false` or DELETE           |
| **Updates profiles**| Limited (only attributes in assertion)| Full attribute sync on each provisioning cycle|
| **Requires**        | Browser, SP metadata, IdP metadata    | Bearer token, HTTPS base URL                 |

### Recommended setup

For the most robust user management, enable both SAML and SCIM:

1. **SAML** authenticates users at login. Configure it first, since users provisioned via SCIM will need SAML to sign in.
2. **SCIM** pre-provisions user accounts so they exist in Agentic Resource Registry before the user's first login. When a user is removed from the IdP, SCIM deactivates or deletes their Agentic Resource Registry account.

Without SCIM, user accounts are only created via SAML JIT (just-in-time) provisioning on first login, and deprovisioned users retain access until an admin manually removes them.

---

## Troubleshooting

### 401 Unauthorized

**Symptom:** All SCIM requests return `401` with `"Missing or invalid SCIM bearer token"`.

**Possible causes:**

- **Token not sent.** Verify your IdP is including the `Authorization: Bearer <token>` header. Use your IdP's test or log feature to inspect the outgoing request.
- **Token mismatch.** The hash of the token your IdP sends does not match any `token_hash` in the `scim_tokens` table. Regenerate the token and re-enter it in your IdP.
- **Token deactivated.** Check that the token record has `active = true`:
  ```sql
  SELECT id, active, created_at FROM scim_tokens;
  ```
- **Wrong endpoint URL.** Ensure the IdP is pointing to `/api/v1/scim` (not `/scim` or `/api/scim`).
- **TLS / proxy issues.** If Agentic Resource Registry is behind a reverse proxy, verify the `Authorization` header is being forwarded and not stripped.

### 409 Conflict

**Symptom:** `POST /api/v1/scim/Users` returns `409` with `"User with email ... already exists"`.

**Possible causes:**

- **Duplicate provisioning.** The user was already created by a previous SCIM push, by SAML JIT provisioning, or manually. Use the filter endpoint to look up the existing user:
  ```bash
  curl -H "Authorization: Bearer $SCIM_TOKEN" \
    'https://your-observal-instance.example.com/api/v1/scim/Users?filter=userName%20eq%20%22alice@example.com%22'
  ```
- **IdP retry.** Some IdPs retry failed requests. If the initial `POST` succeeded but the IdP did not receive the `201` response (due to a timeout), it may retry and receive a `409`. This is safe to ignore. Configure your IdP to treat `409` as a non-fatal error if possible.
- **Case sensitivity.** Agentic Resource Registry normalizes email addresses to lowercase. Ensure your IdP is not sending mixed-case emails that map to the same lowercase value.

### 404 Not Found

**Symptom:** `GET`, `PUT`, or `DELETE` on `/api/v1/scim/Users/{id}` returns `404`.

**Possible causes:**

- **Invalid UUID format.** The `{id}` path parameter must be a valid UUID. Verify the value your IdP is using matches the `id` returned by the create or list endpoints.
- **User was deleted.** If the user was previously removed (via SCIM DELETE or manual database deletion), the ID is no longer valid.

### General Debugging Tips

- **Check audit logs.** All SCIM operations are recorded in the audit log. Query them at `GET /api/v1/admin/audit-log?action=scim.user.create` (or `scim.user.update`, `scim.user.delete`).
- **Check application logs.** SCIM operations are logged under the `observal.ee.scim` logger. Set `LOG_LEVEL=DEBUG` for verbose output.

### Revoking a SCIM Token

**Using the admin API (recommended):**

```bash
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://your-observal-instance.example.com/api/v1/admin/scim-tokens/{token-id}
```

**Using direct database access:**

```sql
UPDATE scim_tokens SET active = false WHERE id = 'TOKEN_UUID';
```

All subsequent requests using the revoked token will receive `401`.
