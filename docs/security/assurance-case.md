---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Security assurance case"
---

## Claim

Observal protects registry control, authentication credentials, agent configuration, and telemetry against unauthorized disclosure or modification when operators follow the documented deployment requirements. This case records the threats, boundaries, controls, evidence, and residual risks supporting that claim.

## Assets

The primary assets are:

* User passwords, access tokens, refresh tokens, OAuth secrets, Git tokens, and signing keys
* Registry agents and their MCP, skill, hook, prompt, and sandbox configurations
* Session transcripts, traces, insights, audit events, and user identity data
* Release artifacts, release tags, and the software supply chain
* Administrative control over users, teams, policy, retention, and SSO

## Threat actors and assumptions

Relevant actors include unauthenticated internet clients, malicious authenticated users, compromised user tokens, hostile registry submissions, compromised dependencies, and network attackers between clients and a remote deployment. Administrators and host operators are trusted to protect the host, secret files, backups, and TLS termination. PostgreSQL, ClickHouse, Redis, the API, and workers are trusted only inside the deployment network.

A host-level compromise, malicious administrator, compromised identity provider, or compromised GitHub release workflow is outside the controls of the application alone. Recovery procedures and external infrastructure controls must address those events.

## Trust boundaries

1. **Client to frontend:** Untrusted browsers, CLIs, harness hooks, and integrations cross the HTTPS boundary into nginx and the API. Packaged HTTP listeners bind to loopback by default. Remote access requires an operator-selected TLS proxy or an explicit non-loopback bind.
2. **API authentication and authorization:** Requests cross from unauthenticated parsing into JWT, password, SSO, role, team, and ownership checks.
3. **Registry execution inputs:** URLs, archives, MCP metadata, prompts, and harness configuration cross from users or external repositories into scanners and config generation.
4. **Application to data services:** The API and workers cross the container network into PostgreSQL, ClickHouse, and Redis. Database ports bind to loopback in the server package by default.
5. **Secret storage:** Operator-controlled secret files and JWT or SAML key files cross into application memory. File-backed dynamic secrets are not copied into PostgreSQL or Redis.
6. **External services:** OAuth providers, source repositories, webhooks, and model providers cross outbound HTTPS and SSRF policy boundaries.
7. **Release supply chain:** Source and GitHub Actions identities cross into signed tags, built artifacts, provenance attestations, and public downloads.

## Security requirements and arguments

### Identity and least privilege

Passwords use scrypt with a per-user salt. Tokens use configurable ES256 or RS256 asymmetric signing, short-lived access tokens, refresh-token revocation, and published JWKS. Role, team, visibility, ownership, and admin dependencies enforce authorization at API boundaries. Redis-backed revocation fails closed.

Evidence: [`models/user.py`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/observal-server/models/user.py), [`services/crypto.py`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/observal-server/services/crypto.py), and [Authentication and SSO](/self-hosting/authentication).

### Credential and key protection

Operator credentials support bounded `NAME_FILE` inputs, direct and file values cannot be configured together, and server-package secrets are operator-owned and readable only by the configured container service group. JWT keys are persisted separately with owner-only permissions. SAML SP keys and certificates can be mounted as dedicated files. File-backed admin values are reported as externally managed and cannot be overwritten or revoked through the admin API.

End-user password hashes and issued-token metadata remain in their purpose-built database records. They are not deployer secrets and do not belong in shared host secret files.

Evidence: [`observal_shared/secrets.py`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/packages/observal-shared/observal_shared/secrets.py), [Configuration](/self-hosting/configuration), and [Environment variables](/reference/environment-variables).

### Network confidentiality

Remote deployments are expected to terminate TLS 1.2 or TLS 1.3 using Caddy, nginx, an enterprise load balancer, or a cloud ingress. Standard HTTPS clients retain certificate and hostname verification. The server package exposes HTTP and data-service ports only on loopback by default. A non-loopback plaintext listener requires an explicit bind setting.

Evidence: [Single-node deployment](/self-hosting/single-node-deploy), [`docker/nginx.production.conf`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docker/nginx.production.conf), and [Requirements](/self-hosting/requirements).

### Untrusted input containment

Pydantic schemas, enums, length bounds, allowlists, namespace validation, and database constraints reject malformed input. Outbound URL paths use SSRF guards. Redirects, archive paths, XML, SVG, host headers, proxy headers, and request sizes receive dedicated validation. Containers add non-root execution, read-only filesystems, temporary filesystems, and `no-new-privileges` where supported.

Evidence: [Trusted proxies and network security](/self-hosting/trusted-proxies), [`services/ssrf_guard.py`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/observal-server/services/ssrf_guard.py), and [`docker/docker-compose.yml`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docker/docker-compose.yml).

### Cryptographic and supply-chain integrity

JWT signing can switch between ES256 and RS256 without recompilation. Algorithm changes retain old public verification keys for the token transition window and reject header or key-type confusion. Release artifacts carry GitHub keyless Sigstore provenance, and release tags are signed and verified with the release workflow's OIDC identity before publication.

Evidence: [`services/crypto.py`](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/observal-server/services/crypto.py), [Release verification](/security/release-verification), and [the release workflow](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/.github/workflows/release.yml).

### Detection and recovery

Security-sensitive administration emits audit and security events. Health checks, diagnostics, backup guidance, key backup guidance, retention controls, and vulnerability reporting support detection and recovery. Private vulnerability reports have documented acknowledgement and remediation targets.

Evidence: [Security policy](https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/SECURITY.md), [Backup and restore](/self-hosting/backup-and-restore), and [Authentication and SSO](/self-hosting/authentication).

## Common implementation weakness mitigations

| Weakness | Mitigation |
| --- | --- |
| Injection and malformed data | Typed schemas, allowlists, parameterized SQLAlchemy queries, and output encoding |
| Broken access control | Central role dependencies plus ownership, team, and visibility checks |
| SSRF | Scheme and address validation before outbound requests |
| Path traversal and unsafe archives | Resolved-path containment, archive member limits, and type checks |
| Credential disclosure | Secret redaction, encrypted sensitive settings, restricted secret files, and support-bundle filtering |
| Token forgery or algorithm confusion | Asymmetric signatures, key-derived allowlists, required key IDs, expiry, and revocation |
| XML and browser attacks | SAML validation, XML protections, SVG sanitization, CSP, frame denial, and content-type controls |
| Resource exhaustion | Request, line, archive, query, timeout, connection-pool, and rate limits |
| Supply-chain substitution | Locked dependencies, dependency scanning, signed release tags, checksums, and artifact provenance |

## Residual risks

* Explicit non-loopback plaintext mode exposes credentials and telemetry to network interception. It is intended only for trusted private networks or an upstream TLS terminator.
* Loopback HTTP relies on the host boundary. Other privileged processes on a compromised host can observe local traffic and files.
* File-backed secrets are only as secure as host permissions, mounts, backups, and the operator's rotation process.
* A compromised administrator, host, identity provider, or canonical GitHub Actions workflow can exercise its trusted authority.
* Third-party MCP servers, hooks, skills, models, and harnesses may process data outside Observal's boundary. Operators must review and isolate them appropriately.
* Historical releases and tags created before signed-tag enforcement may not have equivalent evidence.

## Maintenance

Review this assurance case when authentication, authorization, cryptography, deployment exposure, secret storage, release signing, or a trust boundary changes. Security reports may invalidate an argument and must be reflected here after remediation.
