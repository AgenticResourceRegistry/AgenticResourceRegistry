<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Self-hosting and administration documentation batch

## Scope

Refine the existing operator journey rather than add more documentation pages. Existing URLs and the original operator navigation entries remain available. The sidebar now separates deployment, configuration/access, and operations/recovery.

Rewritten core pages: configuration, authentication, source Compose, upgrades, backup/restore, ports/volumes, and database responsibilities. The overview, software requirements, OIDC introduction, retention guidance, and immediate environment reference were aligned with those mechanisms. No application, adapter, installer, migration, or test implementation was changed.

## Main corrections

- Replace obsolete API-key authentication with the implemented JWT/device/credential flows. Distinguish CLI `/auth/init` from the separate local `/auth/bootstrap` route and document that first initialization must be protected before opening access.
- Distinguish boot/process environment, runtime settings, externally managed files, and deployment-artifact templates. Document actual restart/recreation and verification requirements rather than equating a settings save with applied behavior.
- Keep secret values out of command arguments/screenshots and distinguish encryption configuration from JWT signing material. Normal signing-key rotation is not immediate revocation.
- Remove unconditional compatibility and “zero downtime” claims. Preserve release-specific writer draining and complete-folder rollout gates; distinguish package staging, image cutover, image-only rollback, and data recovery.
- Replace deletion-based restore examples with an isolated recovery destination. Resolve actual volume mounts rather than guess project prefixes. Preserve encryption configuration and full application data with relational and evidence backups.
- Correct source/package host bindings and PostgreSQL 18 mount layout. Redis is not merely a disposable job cache, and a ClickHouse schema export is not an evidence backup.
- Remove copied cost/time/capacity guarantees from the deployment chooser. Self-hosting is not a zero-egress promise.
- Reduce duplicated provider details in the authentication overview, retaining its existing provider/help anchors and linking the focused OIDC/SAML guides.

## Live configuration findings

### Demo seeding

Source Compose supplies demo credentials directly in the API service environment. Removing `.env` lines is insufficient because the service interpolation provides fallback values. The inspected seeding path also does not consult `SEED_DEMO_ACCOUNTS`.

An explicit eight-field empty API environment override was applied **only to the isolated rehearsal**. With fresh owned volumes, health reported a healthy but uninitialized server, then CLI initialization created one real rehearsal administrator with role `admin`. This is not automatic super-admin provisioning or acceptance of every production identity setup.

### URLs and CORS

Runtime `deployment.frontend_url` was saved and read through the ordinary admin mechanism. A sensitive synthetic provider setting's API response was redacted.

The current CORS builder reads process `CORS_ALLOWED_ORIGINS`, not the similarly named `deployment.cors_origins` database setting. In the lab, saving the database value did not permit the test origin; recreating the API with that process environment did permit its preflight. Both tests used a synthetic origin and the local loopback API, not a production browser/IdP exchange.

### Configured retention is not effective TTL

The default policy is `data.retention_days=90`, but source Compose sets `SKIP_DDL_ON_STARTUP=true`, bypassing the runtime ClickHouse initializer that applies the policy. The fresh rehearsal's `session_events` table had **no TTL clause**. The runtime skip flag was independently inspected, and the source guard/application path was read.

This was recorded as a limitation, not papered over with a manual table change. The data-retention guide now distinguishes requested policy from actual storage behavior. Other migration-defined table TTLs and scheduled application retention are separate. A reviewed implementation/policy-application change and privacy/storage verification are required before promising default expiration for that artifact.

## Rehearsal environment

- Source archive: `755dc014`; runtime implementation files unchanged from the previously built `af631690` image source.
- Cached test images: API/web `1.17.0-dev.0`, PostgreSQL 18, ClickHouse 26.6, Redis 8.
- Separate `arr-docs-operator` Compose project with fresh owned volumes; loopback-only test web/LB ports 18381/18380 and no database/Redis/ClickHouse host bindings.
- Usage collector redirected to an unreachable local development endpoint; no production integrations or real provider credentials were configured.
- Temporary CLI HOME, synthetic account/resource/setting data only. The contributor's normal registry login/configuration was not replaced.
- Restore destination database/evidence containers and app volume were separate, newly created resources. Database/evidence targets had no host ports and `--network none`.

## Backup and restoration checks

All source API and worker writers were stopped before backup. ClickHouse was stopped before its cold archive. Actual Docker mount names were resolved programmatically.

| Check | Result |
| --- | --- |
| Healthy initial dependencies/API | Passed |
| Fresh no-demo startup, CLI administrator creation, and identity | Passed; role `admin` |
| Registration disabled by default | Refused with HTTP 403; no test account created |
| Custom-format PostgreSQL dump | Created; 201,919 bytes |
| Read-only application-volume archive | Created; signing material present |
| Cold ClickHouse volume archive | Created |
| PostgreSQL restore into a separate fresh target | `pg_restore --exit-on-error` passed; users, agents, reviews, and settings counts matched |
| Restored application keys | Loaded and verified the prior access token's signature |
| Restored encrypted synthetic setting | Readable with matching evaluation encryption configuration |
| Restored ClickHouse probe | Two records and expected aggregate preserved |

The PostgreSQL comparison covered one synthetic administrator, one synthetic agent, one review, and four settings. The ClickHouse probe was synthetic. These checks are **not** a representative historical migration rehearsal, full restored-API login, Redis/revocation continuity, production encrypted-file rotation, or provider-authentication test. The restored ClickHouse destination used isolated default local authentication, so credential/policy parity was not claimed.

No production restore, release upgrade, downgrade, or rollback was executed. The documentation preserves those acceptance requirements rather than inferring them from a fresh same-version backup.

## Documentation and focused tests

- **122 focused tests passed**: crypto/key initialization, dynamic settings, SSO settings/guards, server backup helpers, and JWT behavior. Warnings were retained; this is not a new full-suite pass.
- Full Mintlify strict build passed with zero errors/warnings.
- Full-site internal links/anchors passed after the rewrites; provider/help anchors remain available.
- Browser checks covered 12 affected routes at desktop/mobile widths (24 cases), with expected headings, HTTP 200, and no document-level horizontal overflow.
- Content assertions rejected deletion/reset commands in the backup/source operations examples and checked the actual restore/initialization/storage guardrails.
- A synthetic administrator's real SSO settings view rendered. Credential values were absent from page text; the capture illustrates layout, not successful SSO login.
- Documentation captures were refreshed, and the configuration/identity screenshot has a meaningful caption/alt text. Image inspection is disabled; human visual/editorial and manual accessibility review remain required.

## Still open

- Native model verification and Antigravity compatibility holds from prior batches.
- Stable-release target/Mintlify publication selection, full-suite failure attribution, Windows and human/manual review.
- Historical migration/cutover/rollback rehearsals and full multi-store restored application verification.
- Real OIDC/Google/GitHub/SAML exchanges and deployed TLS/proxy policy.
- A separate implementation resolution for the source TTL-application gap; no retention guarantee can be based only on its configured value.
- Previously reproduced Bash/platform defects remain separate from this docs PR.

The temporary rehearsal and restore resources are removed after validation. Raw databases, token files, private keys, and backup archives are not committed or included in PR screenshots.
