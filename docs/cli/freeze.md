<!-- SPDX-FileCopyrightText: 2026 Observal Contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# `observal freeze` and `observal unfreeze`

Control local consent for automatic **registry agent/component** updates. These commands never install or update an item themselves and do not affect `observal self` (CLI upgrades). Update checks and manual upgrades still work while frozen. Auto-updating defaults to **frozen** for everyone.

Startup is notice-only until you run `observal unfreeze`. After that, Pi, Claude Code, Kiro, Cursor and Codex sessions update only the installs Observal itself wrote and can verify, through the normal installers. See [What can update automatically](#what-can-update-automatically). `unfreeze` is the only user-level opt-in.

```bash
observal unfreeze                          # Opt in for eligible personal installs
observal freeze                            # Turn off automatic installs
observal unfreeze --project --dir ./repo    # Record project consent (no project installs update automatically yet)
observal freeze --project --dir ./repo      # Revoke just this project's grant
observal freeze --output json
```

These commands require a locally signed-in account and configured registry URL, but make no network request. Preferences live in `~/.observal/auto-update-policy.json`, separate from the machine's `lockfile.json` and the project's committed `observal.lock`. Consent is keyed by **both the configured registry and the locally authenticated account ID**. Signing out, switching accounts, or overriding the stored token with an environment token cannot inherit another account's grant. Environment-only credentials cannot opt in until a matching local login. Legacy registry-only consent is discarded on the first policy change; run `observal unfreeze` again to grant consent to the current account. Project consent is local to this account and machine and applies **only** to the resolved project root, not child projects or teammates' checkouts. Project auto-updates additionally require the global `unfreeze`. No project install is updated automatically yet, so a project grant currently changes nothing.

`--dir` requires `--project`; without `--dir`, `--project` uses the current directory. Repeating either command is safe. A malformed or unsupported policy disables automatic updates and is not overwritten silently (legacy unscoped v1 grants are dropped rather than migrated). If an install is in progress, `freeze` waits for the shared per-registry gate: an install may complete before it returns, but a successful `freeze` prevents any new automatic install afterward. If waiting times out, the command fails instead of claiming it froze updates.

JSON results contain `registry`, `scope` (`user` or `project`), `project` (resolved path or `null`), `auto_update` (grant in this scope), and `effective` (after global policy). No token is emitted. Exit codes follow the [CLI error contract](README.md#exit-codes): 3 for missing registry or local account, 6 for busy gate, 7 for malformed policy or invalid project, 4 for permission errors, and 9 for other filesystem failures.

See [`observal outdated`](outdated.md) to check versions manually.

## What can update automatically

Only **user-scope** installs, and only when Observal wrote them and can prove they are unchanged:

| Shape | Pi | Claude Code | Kiro | Cursor | Codex |
|---|---|---|---|---|---|
| Agent profile | yes | yes | yes | yes | yes |
| An agent's bundled registry-direct skills (at most one script each); skills a release adds or drops | yes | yes | yes | yes | yes |
| An agent's bundled hook scripts | n/a | yes | yes | yes | n/a |
| MCPs the agent keeps in its own files (add, drop, re-version; plain local servers only) | yes (`mcp.json`) | no | yes (in the profile) | no | no |
| Credentials you already saved in that file or in a managed MCP (carried forward unchanged) | yes | no | no | no | no |
| Standalone registry-direct skill (`SKILL.md` and at most one owned script) | yes | yes | n/a | n/a | yes |
| MCP installed with `registry mcp install --managed` | yes (credential-free, or with saved values) | yes (credential-free) | n/a | n/a | n/a |
| Observal's own settings hook groups | no (`observal doctor patch`) | yes, if unedited and none removed | no | no | no |

Each update needs an approved exact release, known unpinned intent (a plain pull or `--upgrade`, not `--version`), and unedited, unshared files that match the ownership record written when you installed. Anything else stays a notice, and the notice says why: an edited file, a name collision, an explicit pin, a new credential, a changed MCP set, an unrecorded or edited hook group, complete skill folders, or a shape not listed above. Notices never contain credential values.

**Shared configs.** Kiro, Cursor and Codex agent pulls also merge into configs every agent of that harness shares, and which the host edits itself (Codex records hook trust in `config.toml`): Codex `~/.codex/config.toml` and `hooks.json`, Cursor `~/.cursor/mcp.json` and `hooks.json`, Kiro `~/.kiro/hooks/observal.json`. No single agent owns them, so they are not part of its ownership record and your edits there never block an update. An automatic update runs only if the release would leave their content unchanged (compared after parsing, using the same merge as `agent pull`), and then does not rewrite them. A release that changes them, for example adding an MCP or a hook on Cursor or Codex, stays a manual `observal agent pull`.

**Agents with complete skill folders** (skills with extra files, which the server now delivers as reviewed folders) are upgraded only by a reviewed manual pull, on every harness.

**Not automatic yet:** project installs, Claude Code agent MCPs (Claude registers them per project in its shared `~/.claude.json`), credential-bearing Claude MCPs, managed MCP installs on Kiro, Cursor and Codex, pasted snippets, the other harnesses, and the Pi extension (`observal doctor patch`).

### How each harness starts updates and shows results

`observal doctor patch --harness <name>` installs the session hooks. They do nothing until you run `observal unfreeze`.

| Harness | Starts at | Results are shown |
|---|---|---|
| Pi | session start (extension) | in Pi |
| Claude Code | `SessionStart` hook | as a user message on your next prompt |
| Codex | `SessionStart` hook (`startup`/`resume`) | as a hook message at the start of the next interactive session. Codex asks you to trust new hooks in `/hooks` first. Headless `codex exec` runs updates but does not display the message. |
| Kiro | `SessionStart` in `~/.kiro/hooks/observal-updates.json` (Kiro IDE 1.0+, Kiro CLI with the V3 engine, `--agent-engine v3`) | on your next interactive `observal` command |
| Cursor | `sessionStart` hook | on your next interactive `observal` command |

Kiro and Cursor send hook output to the model, so registry text is never printed there. The CLI shows their results on stderr, only in an interactive terminal and never with `--output json`.

## Safety and recovery

- A cached notice never authorizes an install: the worker re-checks consent, pin, release, ownership and file state first, and the normal installer re-checks before writing.
- Recovery is conditional, not transactional. A stopped install restores original bytes and modes only if metadata is unchanged and every owned file still matches the old or planned content; created and deleted files are handled the same way. A Claude MCP update restores only its own entry, ownership record and lock row, and reports "restored" only after verifying all three.
- Otherwise a private backup (mode 0600, which can contain credentials you saved) and a pending notice are kept and further automatic installs are blocked until you inspect the files.
- A running session keeps what it loaded. A saved Pi profile needs `/agent` re-selection; Claude Code, Kiro, Cursor and Codex need a new session (and, where agents are selectable, re-selecting the agent).
