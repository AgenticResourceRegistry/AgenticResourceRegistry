<!-- SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Usage Ping Implementation Plan

## Goal

Add privacy-conscious, administrator-scheduled usage reporting so Agentic Resource Registry can identify active company installations and understand product adoption without collecting user content, source code, prompts, traces, credentials, or other secrets.

## Plan

1. **Define the payload**
   - Include a stable installation ID, configured company name, instance hostname, Agentic Resource Registry version, deployment type, timestamp, and aggregate counts for users, agents, components, sessions, activity, token usage, and major feature adoption.
   - Version the payload so fields can evolve safely.

2. **Add administrator controls**
   - Add usage-ping settings for enable/disable, company identity, and a super-admin-selected frequency.
   - Show administrators the exact payload preview, last-send status, and next scheduled send.
   - Document every collected field and how to opt out.

3. **Send pings reliably**
   - Add a background job that sends the payload on the super administrator's selected schedule over HTTPS to the Agentic Resource Registry-operated central collector at `https://usage.observal.io/api/v1/usage-pings`.
   - Keep the collector destination fixed by the release configuration rather than administrator-editable; pings must never be sent to another customer instance.
   - Use short timeouts, bounded retries, and isolated failures so collection never affects normal product operation.

4. **Receive and store pings centrally**
   - Add a validated, rate-limited ingestion endpoint to the Agentic Resource Registry-managed telemetry service.
   - Upsert installations by installation ID and retain timestamped aggregate snapshots for trends.
   - Reject oversized, malformed, or unsupported payloads.

5. **Build the company usage report**
   - Add a super-admin report listing companies, active installations, last seen time, deployed version, usage totals, and adoption trends.
   - Support filtering and CSV export for product planning.

6. **Verify and document**
   - Test payload privacy, aggregation accuracy, opt-out behavior, scheduling, retries, ingestion, deduplication, authorization, and reporting.
   - Add operator documentation covering purpose, fields, schedule, controls, retention, and deletion.
