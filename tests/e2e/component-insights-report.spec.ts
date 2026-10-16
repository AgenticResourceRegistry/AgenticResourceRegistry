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

  test("UI demo skill keeps all published evidence stages distinct", async ({ page }, testInfo) => {
    // A sanitized, deterministic rendering fixture from the real Pi demo report; not a live delivery claim.
    const demo = {
      ...base,
      component_type: "skill",
      component_name: "super/component-insights-ui-demo",
      metrics: {
        present_sessions: 2, present_users: 1, available_sessions: 2, loaded_sessions: 1,
        confirmed_loads: 1, load_attempts: 0, invoked_sessions: null, invocations: null,
        version_distribution: { "1.0.0": 2 }, harness_distribution: { pi: 2 },
        activation_actions: { context_sessions: 0, next_session_sessions: 0, scope: "component" },
      },
      coverage: {
        ...coverage,
        presence: { present_sessions: 2, present_users: 1, eligible_sessions: 62, legacy_hash_sessions: 60, snapshot_missing_sessions: 0, verified_presence_sessions: 2, drifted_presence_sessions: 0 },
        projection: { projection_complete_sessions: 2, projection_pending_sessions: 0, projection_failed_sessions: 0, projection_stale_sessions: 0, unsupported_present_sessions: 0, source_missing_sessions: 0, source_unavailable_sessions: 0, source_incomplete_sessions: 0 },
        evidence: { candidate_facts: 5, attributed_facts: 3, collision_facts: 0, unmatched_facts: 2, unknown_load_results: 0 },
        usage_rate_denominator_sessions: 2,
        reasons: ["unattributed_skill_facts"],
        limitations: ["entered_context_not_helped: a load puts instructions into context; it does not prove effectiveness", "invocations_not_recorded: Pi cannot distinguish an explicit skill invocation"],
      },
      narrative: { summary: "The skill's instructions entered context. This does not show the skill was followed or helped." },
    };
    await loginToWebUI(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(demo),
    }));
    await page.goto("/insights/report-fixture");
    await expect(page.getByRole("region", { name: "Evidence" }).getByText("Not recorded")).toBeVisible();
    await expect(page.getByText("2 of 2 verified-present sessions processed.")).toBeVisible();
    await page.getByText("Detailed skill evidence").click();
    const details = page.locator("details").filter({ hasText: "Detailed skill evidence" });
    await expect(details.getByText("Legacy layer hashes")).toBeVisible();
    await expect(details.getByText("Cannot establish verified presence")).toBeVisible();
    await expect(details.getByText("60", { exact: true })).toBeVisible();
    await expect(details.getByText("Candidate records")).toBeVisible();
    await expect(details.getByText("Unmatched records")).toBeVisible();
    await expect(details.getByText("Activation is configuration activity, not observed skill use.", { exact: false })).toBeVisible();
    await details.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("demo-skill-detailed-desktop.png") });
  });

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
