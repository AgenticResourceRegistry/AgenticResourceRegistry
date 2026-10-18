// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI } from "./helpers";

const kinds = [
  ["Agent Files", "agents", "Agent File"],
  ["MCP Clients", "mcps", "MCP Client"],
  ["Skills", "skills", "Skill"],
  ["Hooks", "hooks", "Hook"],
  ["Prompts", "prompts", "Prompt"],
  ["Sandboxes", "sandboxes", "Sandbox"],
] as const;

test.describe("six-kind browser journeys", () => {
  for (const [label, kind, singular] of kinds) {
    test(`${singular}: discover, create, validate, save, resume, submit and open`, async ({ page }) => {
      await loginToWebUI(page);
      await page.goto("/discover");
      await page.getByRole("link", { name: "Browse Agentic Resources by kind instead" }).click();
      await page.getByRole("button", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/resources/${kind}`));
      await page.getByRole("link", { name: `Create ${singular}` }).click();

      if (kind === "agents") {
        await expect(page).toHaveURL(/\/agents\/builder/);
        const name = `pw-real-agent-${Date.now()}`;
        await page.getByRole("textbox", { name: "Agent File name" }).fill(name);
        await page.getByRole("textbox", { name: "Description" }).fill("Personal agent for browser review");
        await page.getByRole("button", { name: "Save draft" }).click();
        await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
        await page.getByRole("button", { name: "Continue to behavior" }).click();
        await expect(page.getByRole("heading", { name: "Define how the Agent File should behave" })).toBeFocused();
        await page.getByRole("textbox", { name: "Agent File prompt" }).fill("You help a developer review files safely.");
        await page.getByRole("button", { name: "Back", exact: true }).click();
        await page.getByRole("button", { name: "Save draft" }).click();
        await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
        await page.reload();
        await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue(name);
        await page.getByRole("button", { name: "Continue to behavior" }).click();
        await expect(page.getByRole("textbox", { name: "Agent File prompt" })).toHaveValue("You help a developer review files safely.");
        await page.getByRole("button", { name: "Continue to components" }).click();
        await expect(page.getByRole("heading", { name: "Choose building blocks" })).toBeFocused();
        await page.getByRole("button", { name: "Continue to review" }).click();
        await expect(page.getByRole("heading", { name: "Review the package" })).toBeFocused();
        await expect(page.getByRole("heading", { name: "Configuration summary" })).toBeVisible();
        await page.getByRole("button", { name: "Submit for review" }).click();
        await expect(page).toHaveURL(/\/agents\/[a-f0-9-]+/);
        await expect(page.getByText(name).first()).toBeVisible();
      } else {
        await expect(page).toHaveURL(new RegExp(`/build\\?kind=${kind}`));
        await page.getByRole("button", { name: "Submit for Review" }).click();
        await expect(page.getByRole("alert")).toContainText("Name is required");
        await expect(page.getByRole("textbox", { name: "Name *" })).toBeFocused();
        const name = `pw-real-${kind}-${Date.now()}`;
        await page.getByRole("textbox", { name: "Name *" }).fill(name);
        await page.getByRole("textbox", { name: "Description *" }).fill(`Practical ${kind} browser proof`);
        await page.getByRole("button", { name: "Save Draft" }).click();
        await expect(page).toHaveURL(new RegExp(`/build\\?kind=${kind}&draft=[a-f0-9-]+`));
        const saved = page.url();
        await page.reload();
        await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
        await page.getByRole("textbox", { name: "Description *" }).fill(`Revised ${kind} browser proof`);
        await page.getByRole("button", { name: "Save Changes" }).click();
        await expect(page.getByText("Draft updated")).toBeVisible();
        await page.goto(saved);
        await expect(page.getByRole("textbox", { name: "Description *" })).toHaveValue(`Revised ${kind} browser proof`);
        if (kind === "mcps") await page.getByRole("textbox", { name: /Git URL/ }).fill("https://github.com/example/mcp-server");
        if (kind === "skills") {
          await page.getByRole("tab", { name: "Paste" }).click();
          await page.getByRole("textbox", { name: "SKILL.md *" }).fill(`---\nname: ${name}\ndescription: Browser verified\n---\n\n## Instructions\n\nReview changes.`);
        }
        if (kind === "hooks") await page.getByRole("textbox", { name: "Handler Config (JSON)" }).fill('{"command":"echo verified"}');
        if (kind === "prompts") await page.getByRole("textbox", { name: "Template *" }).fill("Review {{task}} carefully.");
        if (kind === "sandboxes") await page.getByRole("textbox", { name: /Image \/ Artifact Ref/ }).fill("ubuntu:22.04");
        await page.getByRole("button", { name: "Save & Submit for Review" }).click();
        await expect(page).toHaveURL(/\/components\//);
        await expect(page.getByText(name).first()).toBeVisible();
      }
    });
  }

  test("guest and outsider cannot read or submit another owner's draft", async ({ page }) => {
    await loginToWebUI(page);
    const adminToken = await getAccessToken();
    const name = `pw-private-draft-${Date.now()}`;
    await page.goto("/build?kind=prompts");
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page).toHaveURL(/draft=[a-f0-9-]+/);
    const draftId = new URL(page.url()).searchParams.get("draft");
    expect(draftId).toBeTruthy();
    const suffix = Date.now().toString(36);
    const password = `Safe-E2E-${suffix}!Aa4`;
    const email = `outsider-${suffix}@example.test`;
    const user = await page.request.post(`${API_BASE}/api/v1/admin/users`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { email, username: `outsider-${suffix}`, name: "Disposable outsider", role: "user", password },
    });
    expect(user.ok(), `User provisioning failed: ${user.status()}`).toBeTruthy();
    const outsiderId = (await user.json()).id as string;
    try {
      const guest = await page.request.get(`${API_BASE}/api/v1/prompts/${draftId}`);
      expect(guest.status()).toBe(401);
      const login = await page.request.post(`${API_BASE}/api/v1/auth/login`, { data: { email, password } });
      expect(login.ok()).toBeTruthy();
      const token = (await login.json()).access_token as string;
      const headers = { Authorization: `Bearer ${token}` };
      expect((await page.request.get(`${API_BASE}/api/v1/prompts/${draftId}`, { headers })).status()).toBe(404);
      const update = await page.request.put(`${API_BASE}/api/v1/prompts/${draftId}/draft`, {
        headers, data: { description: "Unauthorized change" },
      });
      expect([403, 404]).toContain(update.status());
      const submit = await page.request.post(`${API_BASE}/api/v1/prompts/${draftId}/submit`, { headers });
      expect([403, 404]).toContain(submit.status());
      await page.evaluate((value) => {
        sessionStorage.setItem("observal_access_token", value);
        localStorage.setItem("observal_user_role", "user");
      }, token);
      await page.goto(`/build?kind=prompts&draft=${draftId}`);
      await expect(page.getByText("This draft is unavailable.")).toBeVisible();
    } finally {
      const removed = await page.request.delete(`${API_BASE}/api/v1/admin/users/${outsiderId}`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      });
      expect(removed.status()).toBe(204);
    }
  });
});
