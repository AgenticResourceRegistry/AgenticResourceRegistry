<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Deployment-destination consistency follow-up

The latest review identified unsafe or contradictory recipes at immediate destinations of the canonical operator guides. This is a consolidation repair, not a restart of the revamp or an implementation change.

## Corrections

1. Replaced the single-node password-editing recipe. It no longer generates new database passwords while leaving old application connection URLs or provides `sed` commands with secret values in argv. It directs the reader to coordinated credential/URL/native-store configuration and distinguishes fresh setup from existing-data rotation.
2. Replaced ineffective demo removal with the same explicit empty API environment inputs as the canonical configuration guide. Clearing `.env` lines alone is not presented as sufficient.
3. Removed duplicate single-node backup/cron scripts that preserved only PostgreSQL/keys, assumed a native ClickHouse backup disk and cron credentials, and advertised off-host delivery through a commented S3 command. The page now requires the complete recovery set and separately tested scheduling/off-host transfer.
4. Removed generic historical checkout/upgrade examples and unconditional zero-downtime or unaffected-database claims from single-node, production, AWS, and GCP guidance. Helm's adjacent update/rollback instructions also distinguish resource rollback from database recovery.
5. Cloud-specific ordering is explicit: AWS's enabled initializer runs/waits/checks exit status but does not stop old running tasks; GCP's init-job resource definition is not a completed execution. Operators must drain incompatible writers and verify initialization/traffic before cutover.
6. Added `SHOW CREATE TABLE observal.session_events;` as an authorized read-only retention checkpoint. Explain row versus column TTL and separately inspect scheduled policy and actual worker completion/failure/no-op evidence. A clause or successful scheduled job is not proof of deletion at an exact deadline.
7. Corrected the environment reference: package `API_HOST_PORT` publishes the load balancer, source `LB_HOST_PORT` is distinct, and the recommended CI example uses a mounted JWT access-token file instead of an API-key alias.

The single-node page also removes unsupported deployment-time/capacity promises and uses a loopback upstream on port 8080 so a host TLS proxy does not compete with the source load balancer for public port 80. Host commands and loopback meaning are explicit. TLS/public application exposure remains a separately reviewed step after protected initialization.

## Checks performed

- Rendered the exact single-node YAML example with Docker Compose 5.5.0 in a temporary source model. Load balancer had only `127.0.0.1:8080 → 80`, web had only `127.0.0.1:3000 → 3000`, and all eight API demo environment values were empty.
- `!override` is documented as requiring Compose 2.24.4 or newer. Docker's merge reference confirms ordinary port sequences can append/retain old bindings; removing the tag is not a safe compatibility workaround.
- Content assertions checked removal of the incomplete credential editor, duplicate backup script/cron assumptions, fixed historical image targets, positive zero-downtime claims, and preferred legacy-key CI example.
- The exact read-only `SHOW CREATE TABLE` syntax ran in a disposable, network-isolated ClickHouse 26.6 local process with a synthetic table containing separate column/table TTL expressions. The returned definition distinguished them. The synthetic CREATE statements are test setup, not public instructions to alter a deployment's retention.
- Full Mintlify strict build: zero errors/warnings.
- Full-site internal links and anchors: passed.
- Seven affected routes at desktop/mobile widths (14 cases): HTTP 200, expected headings, no document-level horizontal overflow. Browser assertions checked the rendered retention query and preferred token example.
- New destination/retention/reference screenshots are browser review artifacts with SPDX sidecars. Image inspection remains disabled; no human visual sign-off is asserted.
- Scoped deterministic scan and whitespace check: no findings.

## Limits and publication status

No production credential rotation, VM provisioning, real TLS/certificate setup, backup automation deployment, cloud apply, Helm upgrade, migration/cutover, or rollback was executed. The merge check validates a configuration model, not real network reachability or complete hardening. No new full-suite or authenticated native integration task was run in this consolidation batch.

The actual source-retention application gap remains a separate implementation/privacy-policy concern. It does not prevent publishing truthful documentation of the limitation, but it prevents claims of default expiration until effective behavior is resolved and verified.

The PR remains draft for the recorded native-execution, publication-target, suite-attribution, human/manual accessibility/visual, and deployment acceptance requirements. Existing canonical operator rehearsal evidence is not recast as proof these cloud or automation workflows executed.

## Source

[Docker Compose merge and replacement semantics](https://docs.docker.com/reference/compose-file/merge/)
