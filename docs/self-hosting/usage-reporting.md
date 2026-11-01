---
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Usage reporting"
---

Agentic Resource Registry can send aggregate usage reports to the Agentic Resource Registry-operated collector at `https://usage.observal.io/api/v1/usage-pings`. Reporting is enabled by default with a six-hour cadence and starts on new installations without any setup. Until a super administrator sets a company name, reports identify the installation as `unknown-` followed by a six-digit number derived from its installation ID. Once a name is set, the next report carries it under the same installation ID. The collector and reporting dashboard are maintained separately in [Agentic Resource Registry Usage](https://github.com/Observal/observal_usage).

## Configure reporting

A super administrator can open **Admin > Settings > Usage Reporting**, which appears near the top of the settings page, to enter the company name and save the reporting configuration. Setting **Deployment Public URL** adds the deployment hostname to reports; without it the hostname is reported as `unknown`. The available frequencies are every six hours, daily, and weekly. Every six hours is the default. The same panel can disable reporting, change the frequency, preview the exact payload, and send a test report.

Only a super administrator can change the company identity, frequency, or consent setting.

The first upgrade to 1.15.1 or later resets usage reporting to its defaults (on, every six hours) on every existing installation, including installations where it was previously switched off. A super administrator can switch it off again after the upgrade.

Disabling the switch stops scheduled delivery immediately. It does not remove reports already received by Agentic Resource Registry. Contact the Agentic Resource Registry maintainers with the installation ID shown in the panel to request deletion.

## Data included

- Stable, randomly generated installation ID
- Company name and deployment hostname supplied by the administrator, or the `unknown-` placeholder and `unknown` hostname when unset
- Agentic Resource Registry version and deployment type
- Aggregate counts for users, teams, registry components, agent installations, and sessions
- Aggregate session totals by harness
- Distinct active-user and active-agent counts for 7- and 30-day windows
- Aggregate event, prompt, tool-call, tool-result, token, cache-token, and credit totals for 7- and 30-day windows
- Thirty-day session averages for duration, prompts, and tool calls
- Thirty-day counts for sessions using tools or tokens, registered versus unregistered agents, top-level versus sub-agent sessions, and distinct agent/model versions
- Aggregate parser-error and truncated-event counts for ingestion health
- Boolean adoption signals for selected server features
- Report schema version and timestamp

## Data never included

Usage reports do not include names, email addresses, user IDs, agent IDs, prompts, responses, tool arguments, tool results, trace events, source code, file paths, repository names, model identifiers, IP addresses, authentication tokens, API keys, credentials, or arbitrary configuration values. Token and credit fields contain numeric totals only. Distinct model and agent-version fields contain counts, never their names or identifiers. Only the selected feature flags listed above are included.

## Delivery behavior

The selected schedule uses fixed UTC boundaries:

- Every six hours: 00:30, 06:30, 12:30, and 18:30 UTC
- Daily: 06:30 UTC each day
- Weekly: Monday at 06:30 UTC

The worker evaluates due reports every six hours. If a scheduled report was missed or failed, a later worker pass retries it until the current schedule window has a successful delivery. Each pass makes up to three total delivery attempts with short backoff. A failure never blocks normal Agentic Resource Registry operation. The last successful send, latest error, selected frequency, and next delivery time are visible in the Usage Reporting settings panel.

The collector URL is fixed in production releases. `USAGE_PING_URL` exists only to direct development and isolated test deployments to a local collector.
