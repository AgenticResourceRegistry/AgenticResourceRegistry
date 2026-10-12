// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI } from "./helpers";

const suffix = Date.now().toString(36);
let token: string;
let sourceAgent: string;
let forkAgent: string;
let sourceSkill: string;
let forkSkill: string;

async function api(path: string, method = "GET", body?: unknown) {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

test.describe("Registry fork workflow", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async () => {
    token = await getAccessToken();
    const agent = await api("/agents", "POST", {
      name: `e2e-fork-source-${suffix}`, version: "1.0.0", description: "A source for fork UI testing",
      owner: "admin", model_name: "claude-sonnet-4-20250514", prompt: "You are a helpful test agent.", components: [],
    });
    sourceAgent = agent.id;
    await api(`/review/agents/${sourceAgent}/approve`, "POST", { notes: "E2E source approval" });
    const skill = await api("/skills/submit", "POST", {
      name: `e2e-fork-skill-${suffix}`, version: "1.0.0", description: "A skill for fork UI testing",
      owner: "admin", task_type: "code-review", skill_path: "/",
    });
    sourceSkill = skill.id;
    await api(`/review/${sourceSkill}/approve`, "POST");
  });

  test.afterAll(async () => {
    // Clean up only rows created by this spec; skills support archiving, not DELETE.
    // If a test fails before approval, keep its draft for diagnosis rather than hiding the test failure.
    for (const id of [forkSkill, sourceSkill]) {
      if (id) {
        const item = await api(`/skills/${id}`);
        if (item.status === "approved") await api(`/skills/${id}/archive`, "PATCH");
      }
    }
    for (const id of [forkAgent, sourceAgent]) {
      if (id) await api(`/agents/${id}`, "DELETE");
    }
  });

  test("agent: draft, review, public forks and private count", async ({ page }, testInfo) => {
    await loginToWebUI(page);
    await page.goto(`/agents/${sourceAgent}`);
    await expect(page.getByRole("button", { name: "Fork", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: /Forks/ }).click();
    await expect(page.getByText("No public forks yet")).toBeVisible();
    await page.getByRole("button", { name: "Fork", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Fork agent" });
    await expect(dialog.getByLabel("Base version")).toHaveValue("1.0.0");
    await dialog.getByLabel("Name").fill(`e2e-fork-agent-${suffix}`);
    await expect(dialog.getByText(new RegExp(`e2e-fork-agent-${suffix}`))).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("agent-fork-dialog-desktop.png"), fullPage: true });
    await dialog.getByRole("button", { name: "Create draft fork" }).click();
    await expect(page).toHaveURL(/\/agents\/builder\?draft=/);
    forkAgent = new URL(page.url()).searchParams.get("draft")!;
    await expect(page.getByRole("button", { name: "Save draft" }).first()).toBeVisible();
    await page.getByRole("button", { name: "Save draft" }).first().click();
    await expect(page.getByText("Draft updated")).toBeVisible();
    // A draft is never exposed in the public count or list.
    expect((await api(`/agents/${sourceAgent}/forks`)).total).toBe(0);
    await page.getByRole("button", { name: "Continue to behavior" }).click();
    await page.getByRole("button", { name: "Continue to components" }).click();
    await page.getByRole("button", { name: "Continue to review" }).click();
    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(page).toHaveURL(new RegExp(`/agents/${forkAgent}`));
    await page.getByRole("button", { name: "Diff vs upstream" }).click();
    await expect(page.getByText("Unchanged from upstream")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("agent-fork-detail-diff-desktop.png"), fullPage: true });
    expect((await api(`/agents/${sourceAgent}/forks`)).total).toBe(0);
    await page.goto("/review?tab=agents");
    await page.locator(`[data-review-item="${forkAgent}"]`).click();
    await expect(page.getByText(/Forked from/).first()).toBeVisible();
    await page.getByRole("button", { name: "Diff vs upstream" }).click();
    await expect(page.getByText("Unchanged from upstream")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("agent-fork-review-desktop.png"), fullPage: true });
    await api(`/review/agents/${forkAgent}/approve`, "POST", { notes: "E2E fork approval" });
    await page.goto(`/agents/${sourceAgent}`);
    await expect(page.getByRole("tab", { name: "Forks (1)" })).toBeVisible();
    await page.getByRole("tab", { name: "Forks (1)" }).click();
    await expect(page.getByText(`e2e-fork-agent-${suffix}`).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("agent-public-forks-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath("agent-public-forks-mobile.png"), fullPage: true });
    await api("/teams/claim-personal", "POST");
    await api(`/registry/agent/${forkAgent}/visibility`, "PATCH", { visibility: "team" });
    await page.reload();
    await page.getByRole("tab", { name: "Forks", exact: true }).click();
    await expect(page.getByText("No public forks yet")).toBeVisible();
    expect((await api(`/agents/${sourceAgent}/forks`)).total).toBe(0);
  });

  test("fork conflict stays in the dialog with an actionable error", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto(`/agents/${sourceAgent}`);
    await page.getByRole("button", { name: "Fork", exact: true }).click();
    await page.route(`**/api/v1/agents/${sourceAgent}/fork`, (route) => route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "That reference already exists. Choose another name." }),
    }));
    const dialog = page.getByRole("dialog", { name: "Fork agent" });
    await dialog.getByRole("button", { name: "Create draft fork" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Choose another name");
    await expect(dialog).toBeVisible();
  });

  test("inaccessible provenance reveals no saved source identity", async ({ page }) => {
    await loginToWebUI(page);
    await page.route(`**/api/v1/agents/${forkAgent}`, async (route) => {
      const response = await route.fetch();
      const detail = await response.json();
      await route.fulfill({
        response,
        json: { ...detail, forked_from: { available: false, qualified_name: "secret-team/private-source", version: "9.9.9" } },
      });
    });
    await page.goto(`/agents/${forkAgent}`);
    await expect(page.getByText("Source unavailable")).toBeVisible();
    await expect(page.getByText("secret-team/private-source")).toHaveCount(0);
    await expect(page.getByText("9.9.9")).toHaveCount(0);
    await expect(page.getByText("Source unavailable").locator("xpath=ancestor::a")).toHaveCount(0);
  });

  test("skill: fork opens edit and review shows source", async ({ page }, testInfo) => {
    await loginToWebUI(page);
    await page.goto(`/components/${sourceSkill}?type=skills`);
    await page.getByRole("button", { name: "Fork", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Fork skill" });
    await dialog.getByLabel("Name").fill(`e2e-forked-skill-${suffix}`);
    await page.screenshot({ path: testInfo.outputPath("skill-fork-dialog-desktop.png"), fullPage: true });
    await dialog.getByRole("button", { name: "Create draft fork" }).click();
    await expect(page).toHaveURL(/\/components\/[^/]+\?type=skills&tab=edit/);
    forkSkill = new URL(page.url()).pathname.split("/").pop()!;
    await expect(page.getByRole("tab", { name: "Edit" })).toHaveAttribute("data-state", "active");
    const draftEditor = page.getByRole("dialog");
    await expect(draftEditor.getByLabel("Name *")).toHaveValue(`e2e-forked-skill-${suffix}`);
    await page.screenshot({ path: testInfo.outputPath("skill-fork-edit-desktop.png"), fullPage: true });
    expect((await api(`/skills/${sourceSkill}/forks`)).total).toBe(0);
    await draftEditor.getByRole("button", { name: "Save & Resubmit" }).click();
    await expect(draftEditor).toBeHidden();
    const submitted = await api(`/skills/${forkSkill}`);
    expect(submitted.status).toBe("pending");
    await page.goto(`/components/${forkSkill}?type=skills`);
    await page.getByRole("button", { name: "Diff vs upstream" }).click();
    await expect(page.getByText("Unchanged from upstream")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("skill-fork-detail-diff-desktop.png"), fullPage: true });
    const review = await api(`/review/${forkSkill}`);
    expect(review.forked_from?.qualified_name).toBeTruthy();
    await page.goto("/review?tab=components");
    // Skill review rows are keyed by exact version, not listing.
    await page.locator(`[data-review-item="skill:${review.version_id}"]`).click();
    await expect(page.getByText(/Forked from/).first()).toBeVisible();
    await page.getByRole("button", { name: "Diff vs upstream" }).click();
    await expect(page.getByText("Unchanged from upstream")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("skill-fork-review-desktop.png"), fullPage: true });
    await api(`/review/${forkSkill}/approve`, "POST");
    await page.goto(`/components/${sourceSkill}?type=skills`);
    await expect(page.getByRole("tab", { name: "Forks (1)" })).toBeVisible();
    await page.getByRole("tab", { name: "Forks (1)" }).click();
    await expect(page.getByText(`e2e-forked-skill-${suffix}`).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("skill-public-forks-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath("skill-public-forks-mobile.png"), fullPage: true });
    await api("/teams/claim-personal", "POST");
    await api(`/registry/skill/${forkSkill}/visibility`, "PATCH", { visibility: "team" });
    await page.reload();
    await page.getByRole("tab", { name: "Forks", exact: true }).click();
    await expect(page.getByText("No public forks yet")).toBeVisible();
    expect((await api(`/skills/${sourceSkill}/forks`)).total).toBe(0);
  });
});
