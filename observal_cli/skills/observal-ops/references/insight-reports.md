<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Insight reports

## Contents

- Find an existing report
- Generate only when needed
- Choose a section
- Analyze evidence
- Reuse Registry components safely
- Explain missing reuse suggestions

## Find an existing report

Always list completed reports first:

```bash
observal ops insights list NAMESPACE/AGENT_SLUG --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --output json
```

Use the full report for broad questions. Cite report period, session count, Agent version, and comparison baseline when present.

For an owned MCP, skill or hook, list and inspect component reports separately (use `--component skill` or `--component hook`):

```bash
observal ops insights list --component mcp NAMESPACE/SLUG --output json
observal ops insights list --component mcp NAMESPACE/SLUG --all --output json  # all pages
observal ops insights show --component mcp NAMESPACE/SLUG --output json
observal ops insights show latest --component mcp NAMESPACE/SLUG
```

Before generating, inspect the owned component's current evidence and gaps (read-only; owner, co-author or admin access required):

```bash
observal ops insights activity --component mcp NAMESPACE/SLUG --period 14 --output json
observal ops insights sessions --component mcp NAMESPACE/SLUG --limit 50 --output json
observal ops insights sessions --component mcp NAMESPACE/SLUG --cursor NEXT_CURSOR --output json
```

Use `--component skill` or `--component hook` in the same way. The `next_cursor` is opaque and is bound to caller, component, period and version. `--version 1.2.3` on activity, sessions or generate selects the exact component release (unavailable versions error out). Examine the full JSON `coverage.presence`, `coverage.projection`, reasons and limitations before treating an absence of observed use as meaningful. A legacy Agent pin, Bash call, unverified install or missing snapshot is not verified presence. Source/projection gaps are unknown, never zero use.

Component reports contain verified presence, published observed calls and attribution coverage. New reports may also contain `narrative.component_analysis`: bounded interpretations citing published call names and result states, never user prompts or tool arguments. Older prompt-backed analysis is removed. Installation or activation alone never proves use. An `unknown` analysis means insufficient or invalid evidence, not no use. For reports beyond the recent list, use `--all` and open a report by its full ID.

Every component report (MCP, skill and hook) also carries three sections that say how sessions went and what to change. `narrative.outcomes` compares sampled sessions with the component against sampled sessions in the same project, period and harnesses whose layer snapshot lacks it (goal achieved, satisfaction, friction, from session facets; each side shows its cohort size, sample and how many have outcome data). `narrative.attention` is a rule-based list of things to look at, each with the numbers behind it and, where there is a known remedy, one suggestion. `narrative.analysis` is a model-written reading: `at_a_glance` (health and a summary), `what_works`, `friction` and `suggestions` (each with an `action_type`, a confidence and the evidence ids it rests on). Outcome figures are associations: the two groups differ in more than the component, so never say the component caused or improved anything. `analysis.state` is `assessed`, `insufficient` (too few sessions with outcomes and nothing flagged), `unknown` (not requested by a user, or its answers failed the checks) or `unavailable` (the model call failed; regenerate); `outcomes.state` is `assessed`, `insufficient` or `unavailable`. A report generated without a requester reads cached outcomes only and spends no model calls.

## Generate only when needed

Generate when no completed report covers the requested period or version:

```bash
observal ops insights generate NAMESPACE/AGENT_SLUG --period 14 --wait --output json
observal ops insights generate NAMESPACE/AGENT_SLUG --version 1.2.0 --compare 1.1.0 --period 30 --wait --output json
```

Generate a component report (1 to 90 days; the deterministic block works without a model, but interpretations require a configured model):

```bash
observal ops insights generate --component mcp NAMESPACE/SLUG --period 14 --wait --output json
observal ops insights generate --component mcp NAMESPACE/SLUG --version 1.2.3 --period 14 --output json
```

Skill reports use the same commands with `--component skill NAMESPACE/SLUG`. Their counts are deterministic and they report present sessions, sessions with a confirmed load (a successful read or `Skill` call that put the file's text into context), sessions with an invocation (an explicit `/name`, only where the harness records its origin), and sessions where the skill was offered. A `null` count means no harness in the cohort records that kind, which is unknown, not zero; `*_not_recorded_on_some_harnesses` in `coverage.reasons` means the count covers only the harnesses that record it. Load attempts that failed are not loads. Skill evidence counts only for a verified install at the exact file location the session named. Never say a skill helped, was followed, or improved an outcome: the evidence shows only that its instructions entered context. Hook reports (`--component hook`, Claude Code and Pi) are deterministic. They report present sessions, `eligible_sessions` (sessions where the hook could run), `sessions_with_recorded_run`, and runs that printed output, ran silently, failed or blocked. Pi records silent runs; Claude Code does not, so there `silent_runs` is null, recorded runs are a lower bound, and "no recorded runs" never means the hook did not run. On Claude Code, hooks installed by `agent pull` run only while their agent is active and never in headless `claude -p` sessions; on Pi, a verified hook can run in every session, headless included; `coverage.eligibility` counts those sessions separately, outside the denominator. Never say a hook helped or changed an outcome.

Generation can take longer than normal CLI calls. Verify final status before reading the report. If the component presence cohort is empty, generation fails with validation exit code 7 rather than producing a zero-use report. In JSON mode read `error.result.coverage` on stderr for the complete server-provided coverage; the human error summarizes the main gaps and suggests checking activity. A permission or connection error is not an empty cohort.

## Choose a section

Use one section for a narrow question:

```bash
observal ops insights show NAMESPACE/AGENT_SLUG latest --section at_a_glance --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --section friction_analysis --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --section suggestions --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --section usage_cost_analysis --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --section version_comparison --output json
observal ops insights show NAMESPACE/AGENT_SLUG latest --section regression_detection --output json
```

Other sections include `what_they_work_on`, `interaction_style`, `usage_patterns`, `what_works`, `on_the_horizon`, and `fun_ending`. For components, `--section summary`, `--section metrics` and `--section coverage` work across MCP, skill and hook reports. For MCPs, `--section component_analysis --output json` exposes sampled interpretations and cited call names, without user prompts. `--section outcomes`, `--section attention` and `--section analysis` work for every component type.

## Analyze evidence

For a broad answer, report:

1. Health and sample size.
2. Strongest evidence of what works.
3. Highest-impact friction with frequency or severity.
4. Cost or model-efficiency signal when available.
5. Version improvements or regressions.
6. Two or three concrete next actions.

Say when a section is absent, evidence is thin, or the report predates a feature. Do not infer certainty from narrative prose alone. For component reports, read `coverage`: pending, stale, failed, unsupported or missing-source sessions are unknown, not unused. `observed_calls` counts only attributed calls from published generations; activation actions never count as use, and are a best-effort count from delivered sessions, not a complete install history. Never claim per-tool cost or a causal effect from these counts. Interpretive findings are from a small, possibly truncated sample: cite their evidence refs, distinguish inference from observed calls, and do not generalize them to all users or sessions.

## Reuse Registry components safely

A suggestion is an installable Registry match only when it contains a validated `component_ref` object. Use these fields verbatim:

- `component_ref.type`
- `component_ref.id`
- `component_ref.qualified_name`
- `component_ref.latest_version`

Lead with reuse suggestions before create-new suggestions. Inspect before acting:

```bash
observal registry skill show NAMESPACE/SLUG --output json
observal registry mcp show NAMESPACE/SLUG --output json
```

If the Agent is being authored locally, add by component UUID:

```bash
observal agent add skill COMPONENT_UUID --dir ./my-agent --output json
```

If `component_ref` is absent or null, do not claim the suggestion exists in the Registry and do not invent a matching identity. An `existing_component_id` without a validated `component_ref` is insufficient.

## Explain missing reuse suggestions

Read `narrative.registry_match` when present:

- `enabled: false`: reuse matching was disabled.
- `offered > 0` and `reused: 0`: matching ran but nothing fit.
- `registry_has_components: false`: the Registry had nothing eligible.
- Missing key: the report predates reuse metadata.

Explain the returned state directly. Do not convert absence into an error.
