<!-- SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Integration guides and validation-blocker investigation

This batch extends PR #2045 without changing application, adapter, installer, or test implementation. It does not authorize publication or certify every native harness runtime.

## Documentation changes

- Add Codex's project-scope custom-subagent workflow. Explicitly distinguish custom role invocation from `codex --profile` and from a normal parent session.
- Add Copilot CLI's project-scope profile selection workflow. Distinguish the terminal `copilot-cli` adapter from VS Code `copilot`.
- Add Cursor's explicit native subagent invocation steps and remove the implication that a profile automatically replaces the parent session.
- Add an Antigravity compatibility checkpoint rather than fabricate a verified activation path. The adapter emits JSON and an older global path; current vendor docs describe Markdown and a different global path. Legacy JSON acceptance is unverified without the native CLI.
- Surface the reproduced macOS packaged-server limitation and remove the self-hosting overview's unconditional zero-egress claim.

## Source and tool checks

Registry source: `0a60587c` (implementation unchanged from the pre-docs baseline `a64d4380`).

| Tool | Evidence obtained | Not established |
| --- | --- | --- |
| Codex CLI 0.163.0 | Local help/config inspection, generated TOML fields compared with current vendor custom-agent schema; isolated `debug prompt-input` completed | Successful authenticated child task or native role discovery. The agent name in the input probe was supplied in the user prompt, so it is not independent discovery evidence; the role description did not appear. |
| Copilot CLI 1.0.107 | Temporary npm execution of `--version`/`--help` confirmed `--agent`/`--prompt`; generated profile format compared with vendor instructions | Authenticated custom-agent task, entitlement, telemetry, or installed MCP execution |
| Cursor CLI 2026.10.22-27dcf5f | Local help has no main-session `--agent` flag; current vendor documentation describes explicit `/NAME` custom-subagent invocation | Authenticated child task or runtime selection proof |
| Antigravity | Current vendor CLI reference and Agent Manager documentation compared with generated JSON/global paths | Native binary execution, legacy JSON support, or a compatible current agent load |

Codex model login was unavailable, no Cursor API key was available to the isolated checks, and no `agy` binary was installed. Missing credentials or tools were not bypassed. No model task was run in this batch.

## Test failure attribution

### Controlled macOS suite diagnostic

A run with `--maxfail=1`, four workers, and a per-test timeout produced a completed diagnostic summary: **1 failed, 6,193 passed, 17 skipped**, plus two passing subtests. It stopped at:

`tests/test_delegation.py::test_a_dead_workers_child_and_worktree_are_cleaned_up`

This is not a completed full-suite pass.

### Independent pre-docs baseline

A detached temporary worktree at `a64d4380` reproduced the same failure. The implementation's `_ran_in` requires `/proc` to prove that an orphaned process still belongs to the recorded workspace. On macOS it deliberately returns false and leaves the process alone. The test permits macOS but expects the child to be killed within ten seconds. This is a test/platform-contract mismatch present before the documentation changes, not a reason to weaken process-identity safeguards.

Two packaged-server tests also failed on both baseline and docs branch:

- `test_server_package_headless_setup_uses_safe_defaults`
- `test_server_package_replacement_preserves_existing_credentials`

Both failed at `profile_args[@]: unbound variable` under the host's Bash 3.2.57. The packaged setup's empty-array handling needs a separate implementation correction or documented compatible-shell requirement.

The cached concurrency-upgrade and leaderboard-gating failures were rechecked individually and **passed on both baseline and docs branch**. A stale pytest cache is not sufficient evidence that they remain failing.

### Linux comparison

All three failing cases above **passed** in a temporary Linux container using the cached API image, GNU Bash, Python 3.14, and a read-only mount of the baseline. This supports the platform diagnosis but does not certify every platform or package workflow.

A broader Linux run with extra test dependencies and a read-only source mount exceeded its execution window around 93% after logging additional failures. It produced no completed summary. Its environment also differs from standard CI, so those additional findings are **inconclusive** until reproduced individually. No claim is made that the full Linux suite passed or that every remaining failure has been attributed.

### Focused integration regression tests

**195 tests passed** across agent config generation, install harness validation, Copilot integration, and the Antigravity adapter suites. These are mocked/fixture tests of the registry implementation, not proof of native model execution.

## Compatibility hold requiring follow-up

Antigravity's current documented project profile is `.agents/agents/NAME/agent.md`; its documented global profile is `~/.gemini/config/agents/NAME/agent.md`. This registry revision emits `.agents/agents/NAME/agent.json` or `~/.gemini/antigravity-cli/agents/NAME/agent.json`.

Determine whether the intended native CLI still accepts the older JSON format and locations. If not, update the adapter, scanner, generated MCP/settings paths, and tests together in a separately reviewed implementation change. Renaming the extension or copying only the prompt would not preserve all configuration or registry attribution.

## Publication status

The PR remains draft. Required follow-up includes authenticated native tasks for these tools, Antigravity compatibility resolution, separate fixes for the demonstrated platform issues, remaining full-suite failure attribution, Windows/manual accessibility/visual review, and stable-release/Mintlify publication selection.

## Sources

- [OpenAI custom subagents](https://developers.openai.com/codex/agent-configuration/subagents)
- [GitHub Copilot CLI custom agents](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)
- [Cursor custom subagents](https://cursor.com/docs/subagents)
- [Antigravity Agent Manager](https://antigravity.google/docs/cli/commands/agents.md)
- [Antigravity CLI reference](https://antigravity.google/docs/cli/reference/)

## Documentation checks

- Full Mintlify strict build: zero errors and warnings.
- Full-site internal links and anchors: passed.
- Seven affected routes at desktop and mobile widths (14 cases): HTTP 200, expected heading, no document-level horizontal overflow.
- Eight integration preview screenshots captured with SPDX sidecars. Image inspection is disabled, so these are review artifacts, not a human visual sign-off.
- Scoped deterministic design scan: no findings; not rendered accessibility certification.

No new native activation claim was inferred from a configuration file, a guide link, CLI help, or a name appearing in the user prompt.

## Four-issue editorial follow-up

The subsequent audit identified four remaining documentation defects; all four were corrected without implementation or test changes:

- Antigravity's compatibility hold is now in the matrix row itself, with registered-only capability values, and in the opening qualification.
- The self-hosting inventory is explicitly the source Compose stack: all eight core service names and image/build fields match `docker/docker-compose.yml`. Init and the load balancer are included. Prometheus and Grafana are listed separately with overlay image values, and packaged deployments are distinguished. The original architecture heading/anchor was preserved.
- Literal escaped paragraph breaks were removed from the A2A guide.
- Cursor's direct entry path now links exact-release inspection, uses an explicit registry URL with login setup skipped, and previews/pulls the same explicit version and scope in strict mode. Login and pull side effects are explained before installation.

Checks performed for this follow-up:

- Targeted content assertions compared all eight service/image or build rows and both optional images against source YAML, checked the inline hold, checked Cursor's command sequence, and checked the escaped-paragraph defect. Passed.
- Full Mintlify strict build: zero errors/warnings.
- Full-site link and anchor check: passed.
- Browser checks on four routes at desktop/mobile widths (eight cases): HTTP 200, expected headings, no document-level horizontal overflow. Assertions also checked the rendered matrix hold, eight core rows, normal paragraph text, and Cursor's setup safeguards.
- Updated Cursor captures and new matrix, source-stack, and delegation-guide captures were recorded. They are browser evidence, not a human visual sign-off; image inspection remains disabled.
- Scoped deterministic scan and whitespace check: no findings.

No authenticated native tasks or new full-suite run were performed in this editorial follow-up. Earlier platform evidence remains unchanged; the Linux comparison changed Python/dependency environment as well as OS and is not a controlled platform-only experiment. Native execution, Antigravity compatibility, publication target, and human/manual review remain open.
