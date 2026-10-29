---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "E2E Test Checklist"
---

## Test Accounts

| # | Account | Role | Purpose |
|---|---------|------|---------|
| 1 | Super Admin | `super_admin` | Creates all users |
| 2 | Admin | `admin` | Multi-user traces, archive/delete agents |
| 3 | Reviewer A | `reviewer` | Reviews components + agents via CLI |
| 4 | Reviewer B | `reviewer` | Reviews components + agents via UI |
| 5 | User A | `user` | Submits components, creates agents |
| 6 | User B | `user` | Pulls agents, tests in harnesses, leaves ratings |
| 7 | User C | `user` | Also pulls agents (verifies download count increments), verifies registry visibility |

---

## Prerequisites

> [!NOTE]
> You need Docker Engine ≥ 24.0 with Compose v2 (`docker compose`, not `docker-compose`). Homebrew's Docker formula is outdated. Install [Docker Desktop](https://docs.docker.com/get-docker/) or use your distro's upstream packages. Verify with `docker version` and `docker compose version`.

## 1. Environment Setup
- [ ] `make rebuild-clean`
- [ ] Install the CLI from source:
  ```bash
  git clone https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
  cd Observal
  uv tool install --editable .
  ```
- [ ] Verify: `observal --version`

## 2. Super Admin - User Management
- [ ] Log in as super admin
- [ ] Create Admin account
- [ ] Create Reviewer A account
- [ ] Create Reviewer B account
- [ ] Create User A account
- [ ] Create User B account
- [ ] Create User C account

## 3. User A - Add Components (Drafts → Submit)

### Via UI
- [ ] Log in as User A via UI
- [ ] Create MCP as draft, verify it appears in drafts
- [ ] Create Skill as draft, verify it appears in drafts
- [ ] Submit MCP draft for review
- [ ] Submit Skill draft for review

### Via CLI
- [ ] Log in as User A via CLI (`observal auth login`)
- [ ] Create Prompt as draft via CLI, verify it appears in drafts
- [ ] Create Sandbox as draft via CLI, verify it appears in drafts
- [ ] Create Hook as draft via CLI, verify it appears in drafts
- [ ] Submit Prompt draft for review via CLI
- [ ] Submit Sandbox draft for review via CLI
- [ ] Submit Hook draft for review via CLI

## 4. Reviewer A - Review Components via CLI
- [ ] Log in as Reviewer A via CLI (`observal auth login`)
- [ ] List pending submissions (`observal review list`)
- [ ] Approve some components via CLI (`observal review approve` then `observal review publish`)
- [ ] Reject some components via CLI with reasons (`observal review request-changes`)

## 5. Reviewer B - Review Components via UI
- [ ] Log in as Reviewer B via UI
- [ ] View pending submissions in review queue
- [ ] Click a pending component → click "Review" button on detail page
- [ ] Approve some components via UI
- [ ] Reject some components via UI with reasons

## 6. User A - Check Component Review Status
- [ ] Log back in as User A
- [ ] Verify accepted components show as accepted
- [ ] Verify rejected components show as rejected
- [ ] Verify draft components still show as drafts

## 7. User A - Create Agents with Components (Drafts → Submit)

### Via UI
- [ ] Create 3 agents via UI using approved components
- [ ] Save at least 1 agent as draft first, verify it appears in drafts
- [ ] Submit agents for review

### Via CLI
- [ ] Create 3 agents via CLI (`observal agent create` / `observal agent init` + `agent add` + `agent build` + `agent publish`)
- [ ] Save at least 1 agent as draft first, verify it appears in drafts
- [ ] Submit agents for review

## 8. Reviewer A - Review Agents via CLI
- [ ] Log in as Reviewer A via CLI
- [ ] List pending agent submissions (`observal review list`)
- [ ] Approve some agents via CLI (`observal review approve` then `observal review publish`)
- [ ] Reject some agents via CLI with reasons (`observal review request-changes`)

## 9. Reviewer B - Review Agents via UI
- [ ] Log in as Reviewer B via UI
- [ ] View pending agent submissions in review queue
- [ ] Click a pending agent → click "Review" button on detail page
- [ ] Approve some agents via UI
- [ ] Reject some agents via UI with reasons

## 10. User A - Check Agent Review Status
- [ ] Log back in as User A
- [ ] Verify approved agents show as approved
- [ ] Verify rejected agents show as rejected

## 11. User B - Agent Pull & Downloads
- [ ] Log in as User B via CLI (`observal auth login`)
- [ ] Pull/install an agent via CLI (`observal agent pull <agent> --harness <harness>`)
- [ ] Verify download count increases (0 → 1)

## 12. User C - Agent Pull & Downloads
- [ ] Log in as User C via CLI (`observal auth login`)
- [ ] Pull/install the same agent via CLI (`observal agent pull <agent> --harness <harness>`)
- [ ] Verify download count increases (1 → 2)

## 13. User B - Multi-harness Long Prompt Test
- [ ] Test a long prompt involving multiple steps and tool calls in:
  - [ ] Cursor
  - [ ] Kiro
  - [ ] Claude Code
  - [ ] Codex
  - [ ] Copilot
  - [ ] Open Code

## 14. User B - Self Traces
- [ ] Check that User B can see their own traces
- [ ] Verify traces appear for each harness tested

## 15. Admin - Multi-User Traces
- [ ] Log in as Admin
- [ ] Verify admin can see traces from User B
- [ ] Verify admin can see traces from User C
- [ ] Verify admin can see traces from multiple users simultaneously

## 16. Feedback & Ratings
- [ ] As User B, leave star ratings (1-5) on agents
- [ ] As User B, leave star ratings (1-5) on components
- [ ] As User B, add comments with ratings
- [ ] As User C, leave ratings on the same agents/components
- [ ] Verify aggregate rating summary displays correctly

## 17. CLI -- Scan, Doctor & Patch
- [ ] Run `observal scan` to discover harness configs (read-only)
- [ ] Run `observal doctor patch --all-harnesses --dry-run` to preview instrumentation
- [ ] Run `observal doctor patch --all-harnesses` to instrument harnesses
- [ ] Run `observal doctor --output json` to check harness compatibility

## 18. Admin - Agent Registry Management
- [ ] Log in as Admin
- [ ] Go to agent registry
- [ ] Archive some agents
- [ ] Delete some agents

## 19. User C - Verify Registry Visibility
- [ ] Log in as User C
- [ ] Verify archived agents are not visible in the registry
- [ ] Verify deleted agents are not visible in the registry

---

## Test Matrix

The sections below must be run **twice**, once per configuration:

| Case | Global Tracing | Registered-Agents-Only | What it validates |
|------|---------------|------------------------|-------------------|
| **A** | ✅ On (default) | ❌ Off | All agent sessions are eligible for attribution regardless of registry status |
| **B** | ❌ Off | ✅ On | Only registered agents are attributed; unregistered session activity may remain unattributed |

> **How to switch:** Log in as Admin → Settings → toggle "Registered Agents Only". The toggle affects new session ingestion.

Run sections 20–30 once with Case A, then reset and repeat with Case B.

---

## 20. Admin - Trace Privacy Toggle

- [ ] Log in as Admin
- [ ] Go to Settings → Trace Privacy
- [ ] Enable trace privacy (users see only their own traces)
- [ ] Switch to User B → verify User B sees only their own traces
- [ ] Switch to User C → verify User C sees only their own traces
- [ ] Switch to Admin → verify Admin can still see all users' traces
- [ ] Switch to Admin → disable trace privacy
- [ ] Switch to User B → verify User B can now see other users' traces again

---

## 21. User A - Edit Components & Publish New Versions

### Via UI
- [ ] Log in as User A
- [ ] Navigate to an approved Hook → click Edit tab
- [ ] Change a field (e.g. hook script content) → click "Publish New Version"
- [ ] In the version bump dialog, select version bump type (patch/minor/major)
- [ ] Confirm → verify new version appears as "pending review"
- [ ] Navigate to an approved Skill → click Edit tab
- [ ] Change a field → publish new version → verify pending status
- [ ] Navigate to an approved Prompt → click Edit tab
- [ ] Change a field → publish new version → verify pending status

### Via CLI
- [ ] Log in as User A via CLI (`observal auth login`)
- [ ] Run `observal registry version list <component-type> <listing>` → verify current versions shown
- [ ] Run `observal registry version publish <component-type> <listing>` → follow interactive prompts
- [ ] Verify CLI confirms version submitted for review

---

## 22. Reviewer B - Review Component Versions via UI (with Diff)

- [ ] Log in as Reviewer B
- [ ] Go to review queue → see pending component versions from Section 21
- [ ] Click a pending version → verify diff dialog shows GitHub-style split view (old vs new)
- [ ] Approve at least 2 component versions
- [ ] Reject at least 1 component version with a reason

---

## 23. User A - Edit Agent & Release New Version

### Via UI
- [ ] Log in as User A
- [ ] Navigate to an approved agent → click Edit tab
- [ ] Modify agent config (e.g. change model, add/remove a component)
- [ ] Click "Release New Version" → select version bump in dialog
- [ ] Confirm → verify new agent version appears as "pending review"

### Via CLI
- [ ] Log in as User A via CLI
- [ ] Run `observal agent versions <agent-slug>` → verify current versions shown
- [ ] Run `observal agent release <agent-slug>` → follow prompts to publish new version
- [ ] Verify CLI confirms version submitted for review

---

## 24. Reviewer A - Review Agent Versions via CLI

- [ ] Log in as Reviewer A via CLI
- [ ] Run `observal review list` → see pending agent version from Section 23
- [ ] Run `observal review approve <number>` → approve the new agent version

---

## 25. User B - Pull Updated Agent & Verify New Version

- [ ] Log in as User B via CLI (`observal auth login`)
- [ ] Run `observal agent pull <agent-slug> --harness <harness>` (note: command moved from `observal pull`)
- [ ] Verify the pulled config reflects the NEW version's changes (updated model, components, etc.)
- [ ] Verify per-agent hooks are installed correctly for the chosen harness
- [ ] Verify download count increments
- [ ] Use the pulled agent in an harness → generate a trace
- [ ] Verify the trace shows the correct agent version (agent name should reflect the new version)
- [ ] Switch to Admin → verify the trace's agent_version matches what was pulled

---

## 26. User A - Edit & Resubmit Rejected Items

- [ ] Log in as User A
- [ ] Navigate to the rejected component version from Section 22
- [ ] Click Edit / Resubmit → modify the rejected fields
- [ ] Submit for review again → verify status changes to "pending review"
- [ ] Navigate to a previously rejected agent (if any)
- [ ] Edit and resubmit → verify status changes to "pending review"

---

## 27. Registered-Agents-Only Enforcement (Case B only)

> These steps only apply when running under **Case B** (registered-agents-only enabled).

- [ ] Log in as Admin → Settings → enable "Registered Agents Only"
- [ ] Switch to User B
- [ ] Use an harness with a **registered** agent → generate a trace
- [ ] Verify the trace appears in User B's trace list
- [ ] Use an harness with an **unregistered** agent (any agent not in the registry) → generate activity
- [ ] Verify **no trace** is ingested for the unregistered agent
- [ ] Run `observal scan` → verify it warns/skips unregistered agents
- [ ] Run `observal doctor patch --all-harnesses` → verify it only instruments registered agents
- [ ] Switch to Admin → verify traces only exist for registered agent activity

---

## 28. User A - Edit Pending Submissions (Edit Lock)

> PR #712 / Issue #663: Owners can edit/delete pending items. Editing acquires a 30-min lock that hides the item from the review queue.

### Edit a pending component while in review queue (UI)
- [ ] Log in as User A
- [ ] Submit a new component (or use one already pending from earlier sections)
- [ ] Switch to Reviewer B → verify the pending item appears in review queue
- [ ] Switch back to User A → open the pending component → click Edit
- [ ] Verify edit lock is acquired (item shows "editing" state)
- [ ] Switch to Reviewer B → verify the item has **disappeared** from the review queue
- [ ] Switch back to User A → save edits
- [ ] Switch to Reviewer B → verify the item **reappears** in the review queue

### Edit a pending agent while in review queue (UI)
- [ ] Log in as User A
- [ ] Navigate to a pending agent → open agent builder / edit page
- [ ] Verify edit lock is acquired on mount
- [ ] Switch to Reviewer A → run `observal review list` → verify agent is NOT listed
- [ ] Switch back to User A → save edits
- [ ] Switch to Reviewer A → run `observal review list` → verify agent reappears

### Edit pending items via CLI
- [ ] Log in as User A via CLI
- [ ] Run edit command for a pending component (e.g. `observal registry skill edit <skill-id>`)
- [ ] Verify start-edit lock is acquired (check review queue from another session)
- [ ] Complete the edit → verify cancel-edit releases the lock

### Edge cases
- [ ] As Reviewer B, open a pending component and begin reviewing it → switch to User A → try to edit that same component → verify User A is blocked (banner shown: "item is being reviewed")
- [ ] As Reviewer A, open a pending agent in CLI review → switch to User A → try to edit that agent → verify User A is blocked
- [ ] Reverse: User A starts editing a pending component (lock acquired) → switch to Reviewer B → verify item is gone from review queue (cannot review what's being edited)
- [ ] As Reviewer B, try to approve/reject a component that User A is currently editing → verify 409 error
- [ ] Close browser tab while editing a pending item → verify lock releases (item reappears in queue after page close or 30-min TTL expiry)
- [ ] Delete a pending component from My Submissions → verify it disappears from review queue permanently

---

## 29. Auth - Token Revocation Stops Traces

- [ ] Log in as User B via CLI (`observal auth login`)
- [ ] Use an harness with an agent → verify traces are being captured
- [ ] Run `observal auth logout`
- [ ] Use the same harness with the same agent → generate activity
- [ ] Verify **no new traces** appear (token revoked server-side, telemetry rejected)

---

## 30. CLI Compatibility Checks

- [ ] With an outdated CLI version, run any command → verify warning about CLI version being older than server requires
- [ ] Verify `observal agent pull` works (not the old `observal pull` which no longer exists)
