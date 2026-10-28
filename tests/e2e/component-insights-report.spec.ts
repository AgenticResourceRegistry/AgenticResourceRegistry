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
        const exclusions = page.getByText("Excluded because the hook could not run:", { exact: false });
        await expect(exclusions).toContainText("this hook's placement cannot run");
        await expect(exclusions).not.toContainText("agent hooks do not run");
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`${kind}-${kind === "skill" ? "mobile" : "desktop"}.png`) });
      await page.getByRole("region", { name: "Present-session distribution" }).scrollIntoViewIfNeeded();
      await expect(page.getByText("1.0.0", { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath(`${kind}-details.png`) });
    });
  }

  test("Codex skill selection is not presented as a confirmed load", async ({ page }) => {
    const codex = {
      ...base,
      component_type: "skill",
      metrics: {
        present_sessions: 5, present_users: 1, available_sessions: 5, loaded_sessions: null,
        confirmed_loads: null, load_attempts: null, invoked_sessions: 1, invocations: 1,
        harness_distribution: { codex: 5 }, version_distribution: { "0.1.0": 5 },
      },
      coverage: {
        ...coverage,
        presence: { present_sessions: 5, present_users: 1, eligible_sessions: 5 },
        projection: { projection_complete_sessions: 5, projection_pending_sessions: 0, projection_failed_sessions: 0, unsupported_present_sessions: 0 },
        evidence: { candidate_facts: 6, attributed_facts: 6, collision_facts: 0, unmatched_facts: 0, unknown_load_results: 0 },
        usage_rate_denominator_sessions: 5,
        reasons: [],
        limitations: ["loads_not_recorded: Codex does not record attributable skill loads"],
      },
      narrative: { summary: "A native Codex skill selection was recorded, not a separately confirmed skill load." },
    };
    // Keep credentials on loopback, including when a disposable Vite server is used.
    await page.route("**/*", (route) => ["localhost", "127.0.0.1"].includes(new URL(route.request().url()).hostname) ? route.fallback() : route.abort());
    if (process.env.E2E_API_ORIGIN) {
      const api = new URL(process.env.E2E_API_ORIGIN);
      if (!["localhost", "127.0.0.1"].includes(api.hostname)) throw new Error("E2E API must be loopback");
      await page.route("**/api/**", async (route) => {
        const target = new URL(route.request().url());
        target.host = api.host;
        const response = await route.fetch({ url: target.href });
        await route.fulfill({ response });
      });
    }
    await loginToWebUI(page);
    await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(codex),
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/insights/report-fixture");
    const evidence = page.getByRole("region", { name: "Evidence" });
    await expect(evidence.getByText("Codex selections")).toBeVisible();
    await expect(evidence.getByText("Not recorded").first()).toBeVisible();
    await expect(evidence).toContainText("Native Codex skill selection; not a separately confirmed load");
    await page.getByText("Detailed skill evidence").click();
    const details = page.locator("details").filter({ hasText: "Detailed skill evidence" });
    await expect(details.getByText("Native Codex selections")).toBeVisible();
    await expect(details.locator("dt").filter({ hasText: "Unknown load results" }).locator("..").getByText("Not recorded")).toBeVisible();
    await expect(page.getByRole("region", { name: "Attribution coverage" })).toContainText("5 of 5 verified-present sessions processed.");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test("a component report leads with the reading, what to look at and the session comparison", async ({ page }, testInfo) => {
    const side = { sessions: 40, sampled: 30, analyzed: 20, achieved: 6, not_achieved: 14, unclear: 0, satisfied: 5, dissatisfied: 9, with_friction: 12, blocking_friction: 3, tool_failed: 8, friction_types: [["tool_failed", 8]] };
    const narrative = {
      ...reports.hook.narrative,
      attention: [
        { id: "hook_failing", severity: "attention", title: "Most recorded runs failed", detail: "9 of 10 recorded runs failed.", evidence: { failures: 9 }, suggestion: "Check that its command exits 0 when nothing is wrong." },
        { id: "hook_version_unverified", severity: "info", title: "Some sessions are excluded for an unverified harness version", detail: "4 sessions were recorded by a harness version whose hook records are not verified.", evidence: { unverified_sessions: 4 }, suggestion: null },
      ],
      outcomes: { version: 1, state: "assessed", with: side, without: { ...side, sessions: 90, analyzed: 30, achieved: 20, not_achieved: 10, friction_types: [["tool_failed", 2]] }, gap: { state: "lower" }, comparison: "Sessions in this project in the same period." },
      analysis: {
        version: 1, state: "assessed",
        at_a_glance: { health: "needs_attention", summary: "Sessions with this hook achieved their goal less often, and 9 of 10 recorded runs failed." },
        what_works: [{ title: "Runs are recorded", detail: "Each run leaves a record the owner can read." }],
        friction: [{ title: "Failing runs", detail: "Most recorded runs exit with an error." }],
        suggestions: [{ action_type: "edit_component", title: "Make the command exit cleanly", detail: "Return exit code 0 when no problem is found, and print the reason when it fails.", evidence: ["hook_failing", "friction"], confidence: "medium" }],
      },
    };
    await loginToWebUI(page);
    await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ ...base, component_type: "hook", ...reports.hook, narrative }),
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/insights/report-fixture");
    await expect(page.getByRole("heading", { name: "team/review Insights", level: 2 })).toBeVisible();
    const sections = await page.locator("main section").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
    const order = ["At a glance", "What to look at", "Sessions with and without it", "What the sessions show", "Suggested changes", "Evidence", "Attribution coverage"];
    expect(order.map((name) => sections.indexOf(name))).toEqual(order.map((_, index) => index));
    await expect(page.getByText("Needs attention", { exact: true })).toBeVisible();
    const attention = page.getByRole("region", { name: "What to look at" });
    await expect(attention.getByText("Most recorded runs failed")).toBeVisible();
    await expect(attention.getByText("Look at this")).toHaveCount(1);
    const comparison = page.getByRole("region", { name: "Sessions with and without it" });
    await expect(comparison.getByRole("row", { name: /With it 6 of 20 \(30%\)/ })).toBeVisible();
    await expect(comparison.getByRole("row", { name: /Without it 20 of 30 \(67%\)/ })).toBeVisible();
    await expect(comparison.getByText("Sessions with it achieved their goal less often.")).toBeVisible();
    await expect(comparison.getByText("an association, not a cause", { exact: false })).toBeVisible();
    await expect(page.getByRole("region", { name: "Suggested changes" })).toContainText("Edit the component · medium confidence · based on Most recorded runs failed; friction in sessions with it");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("hook-interpretation-mobile.png"), fullPage: true });
  });

  test("a report without a reading says why instead of leaving a gap", async ({ page }) => {
    await loginToWebUI(page);
    const unavailable = { ...reports.skill.narrative, attention: [], outcomes: { version: 1, state: "unavailable" }, analysis: { version: 1, state: "unavailable" } };
    await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ ...base, component_type: "skill", ...reports.skill, narrative: unavailable }),
    }));
    await page.goto("/insights/report-fixture");
    await expect(page.getByText("The reading could not run because the model failed. Generate the report again.")).toBeVisible();
    await expect(page.getByText("Outcomes could not be read because a session read or the model failed. Generate the report again.")).toBeVisible();
    await expect(page.getByText("Nothing in the counts crossed a threshold. That is not proof nothing needs work.")).toBeVisible();
  });

  test("MCP coverage explains historical gaps without attributing them to the listing", async ({ page }, testInfo) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/insights/reports/report-fixture", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({
        ...base, component_type: "mcp", ...reports.mcp,
        coverage: { ...reports.mcp.coverage, presence: { ...coverage.presence, legacy_hash_sessions: 60, snapshot_missing_sessions: 1 } },
      }),
    }));
    await page.goto("/insights/report-fixture");
    const note = page.getByRole("note");
    await expect(note).toContainText("Some sessions lack installation evidence");
    await expect(note).toContainText("60 project sessions have legacy layer hashes.");
    await expect(note).toContainText("1 project session has no installation snapshot.");
    await expect(note).toContainText("not proof that this component was installed or used");
    await expect(note).toContainText("A missing v2 snapshot may arrive late");
    await expect(page.getByText("1 of 2 verified-present sessions processed.")).toBeVisible();
    await note.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mcp-historical-coverage.png") });
  });

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
