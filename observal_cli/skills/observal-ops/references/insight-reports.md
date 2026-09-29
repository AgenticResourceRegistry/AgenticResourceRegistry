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

For an owned MCP, list and inspect component reports separately:

```bash
observal ops insights list --component mcp NAMESPACE/SLUG --output json
observal ops insights list --component mcp NAMESPACE/SLUG --all --output json  # all pages
observal ops insights show --component mcp NAMESPACE/SLUG --output json
observal ops insights show latest --component mcp NAMESPACE/SLUG
```

Component reports contain verified presence, published observed calls and attribution coverage. New reports may also contain `narrative.component_analysis`: bounded interpretations citing published call names and result states, never user prompts or tool arguments. Older prompt-backed analysis is removed. Installation or activation alone never proves use. An `unknown` analysis means insufficient or invalid evidence, not no use. For reports beyond the recent list, use `--all` and open a report by its full ID.

## Generate only when needed

Generate when no completed report covers the requested period or version:

```bash
observal ops insights generate NAMESPACE/AGENT_SLUG --period 14 --wait --output json
observal ops insights generate NAMESPACE/AGENT_SLUG --version 1.2.0 --compare 1.1.0 --period 30 --wait --output json
```

Generate a component report (1–90 days; the deterministic block works without a model, but interpretations require a configured model):

```bash
observal ops insights generate --component mcp NAMESPACE/SLUG --period 14 --wait --output json
```

Generation can take longer than normal CLI calls. Verify final status before reading the report. If the component presence cohort is empty, the server returns 422 with coverage rather than a zero-use report.

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

Other sections include `what_they_work_on`, `interaction_style`, `usage_patterns`, `what_works`, `on_the_horizon`, and `fun_ending`. For MCPs, `--section component_analysis --output json` exposes sampled interpretations and cited call names, without user prompts.

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
