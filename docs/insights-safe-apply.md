<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Safe deterministic Insights Apply

## Problems

The old Apply flow could commit a parent that failed required deterministic checks, silently omit selected components, hide dependency reviews, and replace the useful version description with a generated changelog. Its hook parser mapped `before tool use` to `Stop`; its script wrapper could produce a successful placeholder rather than executable logic. The secret detector matched `sk-answers-not-investigations` inside a harmless `ask-...` slug. Both Apply routes also excluded ordinary agent creators at the admin-only boundary (OBS-1542).

## Contract and flow

1. Nothing is selected by default. Select changes, then request `/api/v1/insights/reports/{id}/preview` (or the agent-scoped equivalent) with explicit `config_indices`, `feature_indices`, and `pattern_indices` arrays. Omitted arrays are empty; null/coerced indices and invalid/duplicate indices are not accepted.
2. Preview uses the same staged builder as submission. It shows the generated files, the parent's before-files, deterministic check results, existing links/removals/superseded proposals and component-before-parent publication order. Credential values are redacted for display, but raw version/structured lock/submission metadata is checked before acceptance.
3. Apply requires the preview's digest with the same selection. The digest covers the source suggestions, base approved version and effective artifact files/effects; generated UUIDs are normalized to logical references only for fingerprinting. Report/review/agent locks serialize repeated submissions and use review-before-agent ordering for supersession. If inputs changed, Apply rejects the stale preview and rolls back.
4. Required deterministic failures roll back the proposal, reviews, inbox notifications and applied timestamp. A valid preview commits **nothing**. New prompt additions require `requires_feature_indices`, including [] for independent additions; a missing required selection is an error. Legacy suggestions without this contract need a newly generated report, not mutation of old records.
5. Success commits pending artifacts and review references once. New components must publish before the parent can publish. Owners/edit-authorized users and admins can prepare/submit; readers cannot. Generated reviews carry durable provenance that prevents automatic publication even if a zero-approval auto-publish policy is configured. Normal human publication gates remain in force.
6. `applied_items.reviews` records review references and their statuses **at submission**, not a live gate cache. Open the review for its current status. Public installation requires publication; existing owner-only draft-install policy exceptions are unchanged. Apply never installs profiles or approves an artifact automatically.

### Why preview stages review rows

Direct skill locks require a valid revision-bound skill folder. The existing review renderer and lock path therefore run in a rollback-only savepoint during preview, rather than a second approximate builder. Inbox delivery is already transactional, with no inline external event-bus notification. Nothing staged is visible to the worker or survives rollback. **PostgreSQL review-number sequences are not transactional**, so previews/failures can leave harmless numbering gaps. This trade-off is explicit; preview IDs are not advertised as live review links. Successful submission creates the actual references.

## Mapping changes

- New feature creation requires a supported explicit action type; ambiguous/unsupported selections and failed materialization are errors, not silent successful skips. Existing components resolve by authorized ID and actual version/type. New MCP generation remains prohibited. Reuse requires an explicit action and authorized component ID; create-new actions are never silently replaced by a name match.
- Prompt destinations and content are validated; unsupported structured `agent_config` changes are not converted into prompt text. Descriptions are preserved from the approved base; changes remain visible in the actual prompt diff. Removals must refer to a component in that base.
- Hooks require one exact canonical event or documented alias. `before tool use`/`PreToolUse` means pre-tool sync; `after tool use`/`PostToolUse` means post-tool async; `session end`/`Stop` means stop blocking; `before prompt`, `session start`, `Notification`, and `SubagentStop` have explicit mappings. Vague `before commit/push` never silently becomes `Stop`.
- Complete Bash source and syntax validation are required. Bash `-n` does not execute the script. No pseudocode-to-successful-placeholder transformation exists. Known `jq` use must declare that requirement; Bash itself is recorded as a requirement. Requirements declaration is **not proof** of availability on a client machine, and syntax validity is **not proof** a guardrail enforces its promise.
- Harness claims derive from registry metadata and adapter-owned delivery checks. Both newly generated and reused/carried hooks are checked; default-scope installation plans must actually emit the requested command/script bytes. Pi uses its existing supported matrix rather than a generic hooks capability label. Pi's script directory is absent, so generated script hooks are rejected for Pi instead of being falsely linked as enforceable. [Existing Pi user-scope command hooks](integrations/pi.md#registry-hooks) remain supported; broader Pi script delivery is deferred.
- Supersession only targets pending versions linked by actual prior Apply records or durable generated-review events, not a descriptive text prefix. Their review/inbox state is updated in the same transaction. Closed/published reviews and ordinary hand-authored proposals are not guessed to be generated.

## Credential detection

The generic `sk-` pattern now needs a token boundary; scoped provider prefixes remain strong matches anywhere. Structured credential fields/maps use stricter generic matching, including camel-case names. Slug/qualified-name values are not blanket-whitelisted. The same detector governs display redaction, and paths/metadata diagnostics never contain detected token values. Real provider-token fixtures remain blocked, while the exact harmless `ask-answers-not-investigations` lock name passes unchanged.

No finite shape detector can perfectly recognize every password or disambiguate every concatenated generic key from prose. Ambiguous values in credential contexts remain fail-closed; this does not disable the required gate, rewrite names to appease it, or certify unknown executable behavior.

## Verification boundary

SQLite regression tests cover rollback, digest stability/staleness, raw-secret rejection/redaction, dependency selection, event aliases, syntax-only checks, route role/edit permissions and human-only publication after checks clear. Disposable OrbStack verification additionally exercises real PostgreSQL preview/submission, API permissions, review dependency publication and strict installation of the reviewed version. Browser tests check default selection, exact preview, confirmation and review links at desktop/mobile sizes. Hosted reports, production settings, historical telemetry and personal harness configuration are outside scope.

AI assistance: Pi under human direction. Human review of complete diffs and screenshots is required before PR publication.

## Executed verification (2026-11-06)

- Full Python suite: **11,510 passed, 71 skipped, 3 failures**. All three failures reproduced on unchanged `main` earlier in the suite (dead-worker cleanup and two macOS Bash setup tests). Subsequent current-source targeted selection: **258 passed, 1 skipped**. The final rollback regression selection: **40 passed**.
- Live PostgreSQL/authenticated API verification passed for ordinary-owner access, reader denial, preview rollback, stale-digest rollback, no duplicate submission, component-before-parent human publication, strict installation of the reviewed version and redacted raw-secret rejection. The exact `ask-answers-not-investigations` component-name shape passed without renaming or disabling the gate.
- Live testing caught an additional issue absent from mocks: outer `db.rollback()` expired the authenticated ORM user and made audit middleware turn a handled stale-preview error into HTTP 500. Submission now rolls back only its proposal savepoint, preserving the caller's authentication transaction; an ORM-expiration regression and the repeated real HTTP flow passed after rebuilding.
- Production API/web Dockerfiles both built. `make rebuild` was run, but an OrbStack/Compose running-container recreation failure required stopping/restarting only this disposable project's API/worker. The final running stack and browser use the resulting source-built images, not an old UI or a patched bind mount. API checksums matched the tested source.
- The local host has 8 GB RAM. Slow npm retries and memory pressure caused prolonged builds and a temporary test-PostgreSQL recovery/startup failure. Only the disposable stack/builds were paused; unrelated containers/apps and hosted settings/data were not changed. The successful web build completed with the disposable backend stopped during compilation; its TypeScript stage and Vite bundle passed.
- Final opt-in Playwright run, retries disabled: **1 passed**, checking no default selections, exact preview, enabled confirmation only after validation, desktop/mobile overflow and submission review links. Screenshots were preserved in the private `insights-evidence/apply/` folder. Temporary UI test credentials were removed; network traces were disabled.
- Frontend typecheck passed; lint: **0 errors, 94 existing warnings**; scoped design detector: no findings. No paid model or actual harness guardrail execution was performed. This is proof of API/preflight/publication/install behavior, not a certification of generated executable logic on every harness version.
- AI assistance: **Pi 1.5.0**, under human direction. Human diff/screenshot review and explicit authorization are still required before PR publication.

## Generated hook and dependency boundaries

Hook suggestions must declare executable requirements in their model schema and format rules. Syntax-only Bash validation runs in a worker thread, never on the API event loop. Required-feature checks use the feature loop's case normalization, so skipped/removed actions cannot justify dependent instructions. The disposable runtime verifier receives its password from the caller environment and never writes credential files.
