// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { loginToWebUI } from "./helpers";

const base = {
  id: "report-fixture",
  subject_type: "component",
  component_id: "11111111-1111-4111-8111-111111111111",
  component_name: "team/review",
  period_start: "2026-10-01T00:00:00Z",
  period_end: "2026-10-15T00:00:00Z",
  component_version: null,
  status: "completed",
};

const coverage = {
  presence: { present_sessions: 2, present_users: 1, eligible_sessions: 3 },
  projection: { projection_complete_sessions: 1, projection_pending_sessions: 1, projection_failed_sessions: 0, unsupported_present_sessions: 0 },
  attribution_state: "observed",
  usage_rate_denominator_sessions: 1,
  observed_sessions: 1,
  usage_rate: 1,
  reasons: ["projection_pending"],
  limitations: ["entered_context_not_helped"],
};

const reports = {
  mcp: {
    metrics: { present_sessions: 2, observed_sessions: 1, observed_calls: 3, result_states: { error: 0 }, version_distribution: { "1.0.0": 2 }, harness_distribution: { pi: 2 }, cohort_collision_calls: 0, cohort_unmatched_calls: 0 },
    coverage: { ...coverage, calls: { collision_calls: 0, unmatched_calls: 0 }, attribution_state: "observed" },
    narrative: { summary: "Three published calls in one verified session.", component_analysis: { state: "assessed", findings: [{ kind: "workflow", insight: "Sampled calls suggest a workflow.", confidence: "low", evidence_refs: ["ref-1"] }], evidence: { "ref-1": "read_issue · unknown result" }, sampled_sessions: 1, truncated: false } },
  },
  skill: {
    metrics: { present_sessions: 2, loaded_sessions: 1, available_sessions: null, invoked_sessions: null, load_attempts: 0, version_distribution: { "1.0.0": 2 }, harness_distribution: { pi: 2 } },
    coverage: { ...coverage, evidence: { collision_facts: 0, unmatched_facts: 0 }, reasons: ["projection_pending"] },
    narrative: { summary: "One confirmed load. Outcome is not measured." },
  },
  hook: {
    metrics: { present_sessions: 2, eligible_sessions: 1, sessions_with_recorded_run: 1, runs_with_output: 1, silent_runs: null, failures: 0, blocks: 0, version_distribution: { "1.0.0": 2 }, harness_distribution: { "claude-code": 2 } },
    coverage: { ...coverage, evidence: { collision_runs: 0, unmatched_runs: 0 }, eligibility: { eligible_sessions: 1, headless_sessions: 1, agent_inactive_sessions: 0, mode_unknown_sessions: 0 }, limitations: ["silent_success_unrecorded"] },
    narrative: { summary: "One recorded run; silent successes may not be recorded." },
  },
};

test.describe("Component Insights report", () => {
  for (const kind of ["mcp", "skill", "hook"] as const) {
    test(`${kind} shows facts before coverage and only applicable evidence`, async ({ page }, testInfo) => {
      await loginToWebUI(page);
      await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify({ ...base, component_type: kind, ...reports[kind] }),
      }));
      if (kind === "skill") await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/insights/report-fixture");
      await expect(page.getByRole("heading", { name: "team/review Insights", level: 2 })).toBeVisible();
      await expect(page.getByRole("region", { name: "Evidence" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Attribution coverage" })).toBeVisible();
      await expect(page.getByText("1 of 2 verified-present sessions processed.")).toBeVisible();
      const sections = await page.locator("main section").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
      expect(sections.indexOf("Evidence")).toBeLessThan(sections.indexOf("Attribution coverage"));
      if (kind === "mcp") {
        expect(sections.indexOf("Attribution coverage")).toBeLessThan(sections.indexOf("Interpretive insights"));
        await expect(page.getByText("Sampled calls suggest a workflow.")).toBeVisible();
      } else if (kind === "skill") {
        await expect(page.getByText("Not recorded").first()).toBeVisible();
        await expect(page.getByText("Only harnesses that record it")).toHaveCount(0);
      } else {
        await expect(page.getByText("At least; silent runs may be unrecorded")).toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`${kind}-${kind === "skill" ? "mobile" : "desktop"}.png`) });
      await page.getByRole("region", { name: "Present-session distribution" }).scrollIntoViewIfNeeded();
      await expect(page.getByText("1.0.0", { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath(`${kind}-details.png`) });
    });
  }

  for (const status of ["pending", "failed"] as const) {
    test(`${status} report keeps the report shell and explains its status`, async ({ page }) => {
      await loginToWebUI(page);
      await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify({ ...base, component_type: "skill", status, progress_percent: 40, error_message: status === "failed" ? "Projection failed" : null }),
      }));
      await page.goto("/insights/report-fixture");
      await expect(page.getByRole("heading", { name: "team/review Insights", level: 2 })).toBeVisible();
      if (status === "pending") {
        await expect(page.getByRole("progressbar", { name: "Report progress" })).toHaveAttribute("aria-valuenow", "40");
      } else {
        await expect(page.getByRole("alert")).toContainText("Projection failed");
      }
      await expect(page.getByRole("region", { name: "Evidence" })).toHaveCount(0);
    });
  }
});
