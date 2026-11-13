<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recovery workflows

## Contents

- Reconcile missed sessions
- Inspect local Kiro completion (read-only)
- Kiro engine changes and hook placement
- Manage CLI versions
- Explicit local fallback

## Reconcile missed sessions

Use when telemetry status or the user identifies sessions missed by automatic delivery.
Kiro V3/IDE finalization is a separate, deliberate reconciliation step: its hook owner
terminates detached children at shutdown. After the chat exits, finish recorded Stop
intents from an independent terminal:

```bash
observal reconcile --harness kiro --since 24 --output json
```

If Kiro was launched with a relocated runtime root, the independent terminal must use
**the same `KIRO_HOME` value** (and the same Observal authentication home). For example:

```bash
KIRO_HOME=/absolute/path/used/by/kiro observal reconcile --harness kiro --since 24 --output json
```

Both examples use `--since 24` to select transcripts modified within the last 24 hours.
The window filters transcript modification time, including sessions with pending Stop
intent: a recent intent does not make an old transcript discoverable. Choose a window
wide enough for the oldest missed transcript, for example `--since 168` for a week.
The supported range is 1–8,760 hours. Already queued outbox batches can still drain
outside that discovery window; zero discovered sessions does not prove all Stop intents
were finalized.

The bare command does not inherit environment variables from another terminal and may
otherwise discover the wrong runtime root. This is explicit reconciliation,
not automatic background finalization.

After upgrading the CLI, run `observal doctor patch --harness kiro --output json`
and verify an enabled `SessionEnd` command exists in the active hook scope before
checking automatic credits. Older prompt/Stop-only files need this refresh.

V3 uses the matching native companion's `agentMode` for automatic agent attribution.
A `SessionEnd` hook captures late records and credits at CLI teardown; it never creates
Stop authority or claims verified completion. Manual reconciliation remains a separate
integrity-finalization step, not a prerequisite for attribution or credit capture.

Kiro hooks capture records and persist Stop intent, but do not claim automatic V3 final
completion. Reconcile sends late records and credits and finalizes only the stopped source
group. Quiet/permission-paused sessions without a matching Stop remain non-final. A resumed
prompt cancels old authority, including queued final semantics and waiting V2 workers.
Queued records and credits remain deliverable without finalizing the resumed session.
Verify the local finalized cursor and emptied intent/outbox; a scheduled child or
successful capture is not finalization evidence. In reconcile JSON, `pushed` means
records were delivered; that status can also accompany a final-metadata send, so it
alone does not verify local finalization. Inspect the local facts below.

## Inspect local Kiro completion (read-only)

Use the same authentication home and `KIRO_HOME` as the chat. There is no dedicated
CLI command exposing cursor, Stop intent, and session-specific outbox state together.
For a Python-based CLI installation, use its Python interpreter (or the repository's
development environment), the project's `read_cursor_status` helper, and a read-only
SQLite connection. Replace `SESSION_ID` with the parent session ID from reconcile JSON:

```bash
PYTHONDONTWRITEBYTECODE=1 python - 'SESSION_ID' <<'PY'
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from observal_cli import telemetry_buffer
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.sessions.base import load_config, read_cursor_status
from observal_cli.sessions.delivery_guard import _session_directory

cfg = load_config()
if not cfg or not cfg.get("user_id"):
    raise SystemExit("Use the CLI's authenticated home; local state is unavailable.")
ensure_loaded()
adapter = get_adapter("kiro")
source = adapter.resolve_session_source({"session_id": sys.argv[1]})
if source is None:
    raise SystemExit("Transcript not found in this runtime root; completion is unverified.")
sources = [source, *adapter.related_session_sources(source)]
home = Path.home()
cursors = []
for item in sources:
    offset, lines, finalized, valid = read_cursor_status(item.checkpoint_key)
    cursors.append({"session_id": item.session_id, "valid": valid,
                    "offset": offset, "line_count": lines, "finalized": finalized,
                    "source_bytes": item.path.stat().st_size})
intent = _session_directory(source, cfg, home) / "finalization.pending"
db = Path(telemetry_buffer.DB_PATH)
queue = []
if db.is_file():
    wal = Path(str(db) + "-wal")
    def signature():
        return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                for p in (db, wal)]
    before = signature()
    with tempfile.TemporaryDirectory(prefix="observal-readonly-") as directory:
        copied = Path(directory) / db.name
        shutil.copyfile(db, copied)
        if wal.exists():
            shutil.copyfile(wal, Path(str(copied) + "-wal"))
        if signature() != before:
            raise SystemExit("Outbox changed during snapshot; retry after workers settle.")
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as conn:
            for item in sources:
                counts = conn.execute(
                    "SELECT count(*), coalesce(sum(final), 0) FROM session_outbox "
                    "WHERE destination = ? AND user_id = ? AND harness = ? AND session_id = ?",
                    (cfg["server_url"].rstrip("/"), cfg["user_id"], "kiro", item.session_id),
                ).fetchone()
                queue.append({"session_id": item.session_id, "pending_batches": counts[0],
                              "pending_final_batches": counts[1]})
        if signature() != before:
            raise SystemExit("Outbox changed during inspection; retry after workers settle.")
print(json.dumps({"cursors": cursors, "stop_intent_present": intent.is_file(),
                  "matching_stop_intent": adapter.session_finalization_pending(source, cfg),
                  "outbox_exists": db.is_file(), "outbox": queue}, indent=2))
PY
```

Run after the chat and delivery workers settle. The recipe reads local state only:
it does not contact ingestion, drain the outbox, migrate the original SQLite database,
acquire a delivery guard, or modify cursors/intents. SQLite can create WAL sidecars even
on a read-only connection, so the query uses an owner-only temporary DB/WAL copy,
deleted automatically, and rejects a snapshot whose source size/mtime changes. The
copy contains sensitive queued data; do not share it. The output contains no credentials,
prompt contents, or queued payloads. Do not substitute `telemetry_buffer.pending()` or
`stats()` for the read-only query; those accessors can create/migrate the database.
A standalone CLI binary is not a Python environment: use a matching Python installation
for this recipe rather than assuming the system interpreter can import its modules.

For each collected parent/child source, verify a valid finalized cursor covering the
source bytes, no remaining Stop intent, and no pending batches. Missing/invalid state,
a missing transcript, or a deliberately uncollected child is not a successful finalization
claim. These are local completion indicators, not an independent remote-manifest audit.
A support bundle or aggregate telemetry counts alone does not expose all these facts.

Preview first when scope is uncertain:

```bash
observal reconcile --harness claude-code --since 24 --dry-run --output json
```

Then run the smallest required scope:

```bash
observal reconcile --harness claude-code --since 24 --output json
observal reconcile --output json
```

Dry run does not drain the outbox, contact ingestion, or change cursors. Report discovered, queued, skipped, failed, and cursor state. Reconciliation does not install hooks.

## Kiro engine changes and hook placement

Configure the native global engine before pulling or patching. `chat.agentEngine`
is global-only; workspace `cli.json` entries do not override it. V3 uses valid
standalone coverage without duplicate inline telemetry; V2 needs inline hooks.
After switching:

```bash
kiro-cli settings chat.agentEngine v3  # or v2
observal doctor patch --harness kiro --output json
```

A later `--v2`/`--v3` launch override is unknown at profile-write time. Patch with
`OBSERVAL_KIRO_ENGINE=v2` (or `v3`) for that override, then re-patch for the saved
engine afterward. Missing or partial V3 standalone coverage keeps uncovered inline
fallback events. Single-invocation coverage requires placement matching the engine.

**Override blast radius:** `doctor patch --harness kiro` rewrites every locked Kiro
profile in the current registry, not just the agent for that launch. A temporary `v3`
override can remove inline hooks needed by other chats using the global V2 default.
Do not apply the override while affected chats are running, or start affected chats
until normal placements are restored. After the override session, unset
`OBSERVAL_KIRO_ENGINE`, run `observal doctor patch --harness kiro --output json` again
with the same runtime/authentication roots, and verify the saved V2/V3 placements
before launching other agents.

## Manage CLI versions

Read current state first:

```bash
observal self status --output json
```

Upgrade, downgrade, and rollback are separate operations:

```bash
observal self upgrade --force --output json
observal self upgrade --version 2.6.0 --force --output json
observal self downgrade --list --output json
observal self downgrade --version 2.5.0 --force --output json
observal self rollback --force --output json
```

### Automatic sync with the server

The server accepts only a CLI on its own exact version. The CLI keeps itself there without being asked:

- **When:** at `observal auth login` (before credentials), and on the first request of any later command that finds a mismatch, for example after the server was redeployed. `observal self ...` and `observal server ...` commands are exempt, so a manual version change or a local server upgrade is never fought.
- **What:** it installs the server's version over the running installation with the tool that owns it (uv, pipx, pip, or the standalone binary), then re-runs the command, unless the command already sent a request to the server or read its piped input: then it fails with `version_mismatch` after syncing, and you check what the command already changed before running it again. An editable or source install is replaced like any other. There is no prompt and no setting to turn it off.
- **Other copies:** every other `observal` on PATH is brought to the same version with its own installer. A copy no installer owns (a system package, an unknown binary) is named on stderr; remove it or update it with whatever installed it.
- **Development servers:** `X.Y.Z-dev.N` is not on PyPI. The CLI builds the commit the server reports as `build_commit` in `GET /api/v1/config/version`. For a server that reports none, it finds the newest commit on `main` that still declares that version, because `main` itself may already be on the next version. Only commits on `main` or a `release/*` branch are ever built, never an open pull request or a fork; if none declares the version, nothing is installed and the error says so.
- **Downgrades:** dependencies are capped at the versions the CLI already runs with, because older releases can break on newer dependency releases. After any install the CLI loads a command from the new installation and reports the sync as failed if it cannot start.
- **Windows with uv:** the running `observal.exe` cannot be replaced, so the package is updated inside uv's tool environment and the launcher is kept. `observal --version` and `uv tool list` show the new version.
- **Once per command:** if the CLI still does not match after one sync, the command fails with `version_mismatch` (exit code 10) instead of looping.
- **Record:** `~/.observal/last_sync.json` holds the version that was replaced, the commit the server reported (`server_commit`), and what was actually installed (`installed_from`). They differ when the reported commit was unpublished and another published commit was built.

When a `version_mismatch` error does appear, its message says why the sync did not complete. Typical causes and fixes:

| Cause in the error | Fix |
| --- | --- |
| Managed by apt, dnf or brew | Update with that package manager, or install with `uv tool install observal-cli==<server version>` and remove the packaged copy |
| Development build has no standalone binary | Install `<build_commit>` only if it is on `main` or a `release/*` branch and declares the server's version; otherwise ask the server's administrator. Then `uv tool install --force "git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@<build_commit>"` |
| Needs git (a development build is built from source) | Install git with the command the error names (`winget install --id Git.Git -e`, `xcode-select --install`, or the distribution's package manager), open a new terminal, and rerun the original command; the CLI syncs itself. Do not run `uv tool install git+...` instead: it needs git too |
| The installer failed (network, permissions) | Fix the cause, then `observal self upgrade --version <server version> --force --output json` when the server is newer than the CLI, or `observal self downgrade --version <server version> --force --output json` when it is older |
| Installed but does not start | A dependency is newer than that CLI version supports. Reinstall with the dependency capped, for example `uv tool install --force "<same source>" --with "typer<0.29"`, then retry |
| No published commit declares the version | The server runs a build that was never merged, or `git` or github.com is unavailable. Do not install a pull-request or fork commit to match it; ask the server's administrator |
| Still reports the old version after installing | Another copy is first on PATH: `which -a observal` (`where observal` on Windows), remove the stale one |
| Synced, but the command was not re-run because it had already read its standard input or sent a request | Check what the command already did (a request may have reached the server), then run it again with the same input, for example the same `observal api ... < body.json` |
| The install is still running in the background | Wait for that process to finish (the error names its PID), then run the command again. Do not start another install meanwhile |
| The sync says the version predates automatic sync | Nothing now. After that server's next upgrade, run `observal self upgrade --version <server version> --force` once (`self downgrade` if the server moved to an older version) |
| The sync was skipped in a hook, MCP server or background worker (a failed delegated task, an observal-agents tool error) | Run any `observal` command, such as `observal auth whoami`, to sync the CLI, then retry; restart the harness so its MCP servers start on the new version |
| Ctrl+C during a sync says it is finishing the install | Let it finish. Stopping an install part-way would leave no working CLI |
| Another version change did not finish in time | Another `observal` process is installing a version. Wait for it, then run the command again. The lock is released when that process exits, even if it crashes, so there is no lock file to delete |

`self upgrade` and `self downgrade` accept `-dev` versions and locate the right commit the same way. Without `--version`, a CLI logged in to a server that runs a `-dev` build targets that build instead of the latest release. If the server cannot be reached, upgrade falls back to GitHub releases and says so. As a last resort install from source: `uv tool install --force git+https://github.com/AgenticResourceRegistry/AgenticResourceRegistry@<build_commit>` for a development build, only if that commit is on `main` or a `release/*` branch (otherwise ask the server's administrator; `@main` only if the server reports no commit and `main` is still on that version), or `@v<version>` (release tag), then confirm `observal --version` equals the server version.

Standalone binary changes require a published checksum because JSON mode does not accept unsigned downloads interactively. Verify installed version after mutation.

## Explicit local fallback

Use only after JSON returns either `error.category: unavailable` with exit code `9`, or `error.category: authentication` with operation `Load authenticated CLI configuration` and exit code `3`. The user confirms they still want a local-only Agent file before anything is written.

| Harness | User scope | Project scope |
| --- | --- | --- |
| Claude Code | `~/.claude/agents/<name>.md` | `.claude/agents/<name>.md` |
| Kiro | `~/.kiro/agents/<name>.json` | `.kiro/agents/<name>.json` |
| Cursor | `~/.cursor/rules/<name>.mdc` | `.cursor/rules/<name>.mdc` |
| VS Code | `~/.config/Code/User/agents/<name>.md` | `.vscode/agents/<name>.md` |
| Codex CLI | `~/.codex/agents/<name>.md` | `.codex/agents/<name>.md` |
| Copilot CLI | `~/.config/github-copilot/agents/<name>.md` | `.github/copilot/agents/<name>.md` |
| OpenCode | `~/.opencode/agents/<name>.md` | `.opencode/agents/<name>.md` |

Kiro JSON shape:

```json
{"name":"<name>","description":"<description>","prompt":"<prompt>","model":"<model>","mcpServers":{},"tools":["read"],"resources":["skill://~/.kiro/skills/*/SKILL.md"]}
```

Start with the minimum explicit Kiro tool allowlist. Add exact built-in or MCP tool names only when the user requests them. Set `tools` to `["*"]` only after separate confirmation that unrestricted built-in and MCP tool access is intended.

Markdown harness shape:

```markdown
---
name: <name>
description: <description>
---
<prompt>
```

Cursor uses the same frontmatter in an `.mdc` file. Preserve existing files unless the user explicitly authorizes replacement. Report the exact path and state that no Registry publication occurred. When service returns, use the normal `observal agent create` or authoring workflow.
