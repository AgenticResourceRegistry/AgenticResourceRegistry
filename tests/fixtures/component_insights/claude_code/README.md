# Claude Code Phase 0 structural fixture

Live-derived **sanitized** subset of a single consented Claude Code 2.1.283 development session. Original transcript is not committed. Zero-based source indices retained for provenance: `21–24`, `33–36`, `38–39`; the JSONL fixture is compacted to ten lines, so test batch offsets must be supplied separately. Indices `21–23` are three separate assistant source records (thinking, text, ToolSearch) with one message ID. Indices `33–34` are two separate parallel MCP calls with another message ID; `35–36` are their linked results. `38–39` are an MCP call and its explicitly errored result. There is **no observed multi-tool single-line record** or missing-result call in this subset.

All message, source and tool IDs, timestamps, thinking/text, tool inputs, and result contents were replaced; root metadata, model, usage, cwd, signatures, request fields and unrelated records were discarded. Only tool-name shape and safe alias names, block types and ordering, result links, and the explicit `is_error` flag remain from the session. The fixture is **derived**, not a verbatim transcript. Separately constructed parser edge cases must be labelled as such in tests.

`lockfile.json` is a minimal projection of the one genuine bundled-agent pull. Registry UUIDs, server URL, and directory are replacements. The two original component UUIDs were checked against the read-only registry `show` responses for `super/phase0-probe` and `component-insights-phase0/phase0-probe` before substitution. `registry_components.json` retains those observed namespace/slug identities and expected per-component installed aliases, paired to the replacement UUIDs. `effective_mcp_config.json` retains the two alias keys observed in the project-specific Claude Code config **after manual registration in the target directory**; the original command/arguments are replaced with inert placeholders. Pull initially ran MCP setup in its caller's cwd, so this file does **not** prove the pull configured the target. `pinned_snapshot.json` is a minimal projection of the actual snapshot's relevant agent pins: it intentionally shows that nested bundled IDs/aliases were dropped by Python's current extractor. Its manifest paths, hashes, layer hash and lockfile hash are constructed placeholders, not the full uploaded snapshot or a claim about the live session hash. No standalone lockfile entry is asserted.

## Verified parser and identity boundaries

Canonical ingest produces one rendered source `session_events` row per fixture line; `tool_name` / `tool_id` summarize a call and are **null for result rows**. The original sanitized `raw_line` is retained, including the `tool_result.tool_use_id` link and `is_error: true` on the failed result. The real parallel MCP calls occupy different source records with one assistant message ID. The preceding text shares the `ToolSearch` message ID, not the parallel MCP message ID. Tests use explicitly constructed (not captured) two-call and text-before-tool *single-record* inputs to prove the first-tool-only summary and the preserved full content array.

Current MCP call names follow `mcp__<installed-alias>__<tool>`. Do not identify a listing by a bare alias prefix: `mcp__team-a-probe-2__ping` also begins with `mcp__team-a-probe`. `_local_registry_names` disambiguates duplicate slugs and a dot/hyphen namespace collision (`team.a` vs `team-a`), but a space/hyphen namespace pair (`team a` vs `team-a`) remains distinct until `_build_mcp_context` sanitizes both to the **same** `team-a-probe` key. That is a current collision to fix before relying on aliases for identity. Identical alias strings can also occur in project and user harness scopes; the alias alone does not identify a registry listing. `config_generator._build_mcp_context` uses `sanitize_name(local_name or slug)` for a standalone-style call in code; this Phase 0 recording predates standalone tracking.

## Phase 1 isolated standalone capture

`standalone_install.json` is a **sanitized identity projection** from a later genuine tracked standalone MCP `--apply` installation into an isolated project. The installed registry item actually lived in a separately isolated PostgreSQL database and was served by the current-source Observal registry resolution/show/install routes (with test-only authentication override), not the older sample server. The real Claude Code 2.1.283 executable wrote a project `.mcp.json` entry; the CLI verified that effective alias, persisted a scoped standalone lockfile entry with the selected version and integrity fingerprint, and built a v2 pinned snapshot. A preliminary attempt in a *different* temporary project registered a Claude entry but failed before tracking when the output pipe closed; it is not the fixture. No existing sample migration, production install, or real user credentials were involved.

The stored fixture replaces the original registry UUID and namespace with constructed values, omits server URL, original layer/file hashes, absolute paths, raw command/arguments, settings content, and the original integrity fingerprint. `v2_hash_built` reports the observed result, **not** an assertion that a hash computed from the sanitized fixture equals the original upload hash. The first-harness integration test creates a synthetic scoped snapshot and registry row from these sanitized pins to test the actual extractor and `presence_cohort`; it does not represent a historical session upload.

## Skill fixtures

Sanitized copies of six headless Claude Code 2.1.286 sessions, recorded on 2026-10-01 with Opus 4.6 on Amazon Bedrock. The recording ran in an isolated `HOME` and `CLAUDE_CONFIG_DIR` with `--allowedTools Skill Read`. Only the authentication variables were passed through. It used two synthetic skills: user-scope `observal-probe`, which replies `PROBE-7F3A`, and project-scope `project-probe`, which replies `PROJECT-9C1D`.

- `skill_session_model_read.jsonl`: "Use the observal-probe skill." The model called `Skill`.
- `skill_session_slash_command.jsonl`: `/observal-probe`.
- `skill_session_slash_args.jsonl`: `/observal-probe with extra words`.
- `skill_session_literal_text.jsonl`: the user typed the `<command-name>`/`<command-message>` tags as literal text. No skill ran.
- `skill_session_project_skill.jsonl`: "Use the project-probe skill." (project scope).
- `skill_session_unknown_skill.jsonl`: the model was asked to call `Skill` with `missing-probe`, which failed.

Every line is kept in order. Session, entry, prompt, request, message and tool-use IDs were replaced with fixture values. The isolated config directory was mapped to `/home/fixture/.claude` and the project to `/home/fixture/project`. The system prompt, agent listing, thinking text and signatures were replaced with placeholders. The skill listing was reduced to the two probe skills; the recording also listed Claude Code's bundled skills. Record types, `isMeta`, `promptId`, `sourceToolUseID`, `toolUseResult`, `is_error`, tool names and inputs, the `Base directory for this skill:` expansion text, timestamps, model and usage come from the recording.

### Observed skill evidence

- **Offered:** the `skill_listing` attachment lists `names` and descriptions, but no locations. It cannot be tied to the verified file, so it is not counted.
- **Loaded by the model:** an assistant `tool_use` named `Skill` with `input.skill`, then its `tool_result` (with `is_error: true` on failure, or `toolUseResult.success: true`), then an `isMeta: true` user record with `sourceToolUseID` set to the call id, whose text starts `Base directory for this skill: <dir>`. An unknown skill gets an error result and no expansion.
- **Invoked by the user:** `/name` is a user record whose content is exactly `<command-message>name</command-message>\n<command-name>/name</command-name>`, optionally followed by `\n<command-args>…</command-args>`. It is followed by an `isMeta: true` expansion with the same `promptId` and no `sourceToolUseID`. Typing the same tags produces an ordinary user record with no expansion.

None of these shows that a skill achieved anything.

## Hook fixtures

Sanitized copies of four Claude Code 2.1.286 sessions, recorded on 2026-10-01 with Opus 4.6 on Amazon Bedrock. The recording used the same isolation as the skill fixtures, with `--allowedTools Read` (plus `Glob` where noted). Probe hooks in the project's `.claude/hooks/` appended a line to a marker file each time they ran, so whether a hook actually ran is known independently of the transcript:

- `probe-prompt.sh` (`UserPromptSubmit`) prints one line to stdout and exits 0.
- `probe-pre.sh` (`PreToolUse`) prints nothing and exits 0.
- `probe-fail.sh` (`PostToolUse`) writes to stderr and exits 1.
- `probe-block.sh` (`PreToolUse`, matcher `Glob`) writes to stderr and exits 2.

| Fixture | Mode and install path | Ran (marker file) | Recorded in the transcript |
| --- | --- | --- | --- |
| `hook_session_settings_outcomes.jsonl` | headless `-p`, project `settings.json` (the standalone `hook install` shape) | prompt, block, fail | `hook_success` (prompt), a blocked `tool_result`, `hook_non_blocking_error` (fail) |
| `hook_session_silent_success.jsonl` | headless `-p`, project `settings.json` | pre | nothing |
| `hook_session_agent_interactive.jsonl` | interactive `--agent probe-agent`, hooks in the agent's frontmatter exactly as Observal writes them (no `matcher`) | prompt, pre, fail | `hook_success` (prompt), `hook_non_blocking_error` (fail); nothing for the silent pre |
| `hook_session_agent_headless.jsonl` | headless `-p --agent probe-agent`, same frontmatter | none | nothing |

The interactive session was driven through a pseudo-terminal, with onboarding marked complete and the project marked trusted in the isolated config. IDs, paths, the system prompt and thinking were replaced as for the skill fixtures. Hook record fields, `toolDenialKind`, tool results, timestamps and usage come from the recording.

### Observed hook evidence

- **Success with output:** `attachment.type: "hook_success"`, with `hookEvent`, `hookName`, `command` (exactly as configured), `exitCode: 0`, `stdout`, `durationMs` and `toolUseID`.
- **Silent success:** no record at all. A hook that ran and printed nothing cannot be distinguished from a hook that did not run.
- **Non-blocking failure:** `attachment.type: "hook_non_blocking_error"`, with the same fields and the non-zero `exitCode`.
- **Blocking failure (exit 2):** no hook attachment. The blocked tool's `tool_result` has `is_error: true` and the content `"<Event>:<Tool> hook error: [<command>]: <stderr>"`, and the record has `toolDenialKind: "permission-rule"`.
- **Agent frontmatter hooks** ran only in the interactive session. They did not run under headless `-p`, either with `--agent` or when the agent ran as a subagent; that subagent recording is not kept as a fixture. A matcher of `"*"` made no difference. Settings hooks ran in both modes, including inside a headless subagent.

None of these shows what a hook achieved.

## Agent hook gate fixtures

These were recorded on 2026-10-01 with Claude Code 2.1.286 and Opus 4.6 on Bedrock, using the same isolation as the fixtures above. They inform the opt-in move of agent hooks from frontmatter into a gated `settings.json` (`observal_cli/hook_gate.py`), which `agent pull --hooks=settings` writes (`observal_cli/agent_hooks.py`). The gated sessions were recorded before that command existed.

`hook_inputs/` holds the raw hook input JSON that Claude Code passed to a recorder hook, for every event in six modes: headless and interactive `--agent probe-agent`, headless and interactive plain sessions, and headless and interactive plain sessions that ran `probe-agent` as a subagent. File names are `<mode>-<owner>--<event>[-<tool>].json`. The owner is `probe-agent` when the event belongs to that agent, and `main` for the main thread of a subagent session. Session, prompt and tool-use IDs, paths, prompts, tool inputs and responses are replaced. Keys, `hook_event_name`, `agent_type`, the presence of `agent_id`, `tool_name`, `permission_mode` and `source` come from the recording. Observed:

- With `--agent`, every event, from `SessionStart` to `Stop`, carries `agent_type` set to the agent and no `agent_id`, headless and interactive.
- A subagent's own events (`PreToolUse`, `PostToolUse`, `SubagentStop`) carry `agent_type` and `agent_id`. The main thread's events in the same session carry neither.
- Plain sessions carry no `agent_type` on any event. A present but malformed `agent_type` was never seen.
- Claude Code runs a hook command as `/bin/sh -c <command>` (bash 3.2 in POSIX mode on macOS) in the project directory.

`gate_session_*.jsonl` are sanitized transcripts of sessions in which the probe hooks were configured in `settings.json`, each wrapped by the gate for `probe-agent`: prompt context, a silent success, a failure, an exit-2 block on `Glob`, and a shell-heavy `Stop`/`SubagentStop` command. The marker file showed:

| Fixture | Mode | Gated hooks that ran |
| --- | --- | --- |
| `gate_session_headless_agent.jsonl` | headless `--agent probe-agent` | all five (the headless gap is closed) |
| `gate_session_headless_other_agent.jsonl` | headless `--agent other-agent` | none |
| `gate_session_headless_plain.jsonl` | headless plain | none |
| `gate_session_headless_subagent_main.jsonl` / `gate_session_headless_subagent.jsonl` | headless plain session, and its `probe-agent` subagent transcript | the subagent's pre, fail and `SubagentStop`; not the main thread's prompt hook |

Interactive `--agent probe-agent` ran all five, and interactive plain ran none. Those transcripts are not kept. The shell-heavy command wrote `A B|it's` and received the exact stdin. Records name the full gated command. The interpreter and source paths in those commands were replaced with `/home/fixture/.local/share/observal/bin/python3` and `/home/fixture/observal-src`; the recording used the source-checkout `PYTHONPATH` fallback.

The subagent pair shows how the two transcripts link. Every record of the subagent's own transcript has `isSidechain: true`, the same `agentId`, and `sessionId` set to the parent session's id, but nothing names the agent. The parent's main thread records the spawn twice: an assistant `tool_use` named `Agent` whose `input.subagent_type` is `probe-agent`, and the `user` record holding its single `tool_result` (same `tool_use_id`), whose top-level `toolUseResult` has `agentId` (the subagent's) and `agentType: probe-agent`. The `tool_result` text also mentions the `agentId`, but that text is model-facing output and is never read. The `agentId` and the session id agree across the two files.
