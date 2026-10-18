// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI, publishSubject, reviewNumber } from "./helpers";

test.describe("Agent File builder recovery", () => {
  test("an incomplete Agent File saves without a prompt; empty composition is honestly reviewed", async ({ page }) => {
    await loginToWebUI(page);
    const name = `pw-agent-draft-${crypto.randomUUID()}`;
    let id: string | null = null;
    try {
      await page.goto("/agents/builder");
      await page.getByRole("textbox", { name: "Agent File name" }).fill(name);
      await page.getByRole("button", { name: "Save draft" }).click();
      await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
      id = new URL(page.url()).searchParams.get("draft");
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue(name);
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("button", { name: "Continue to review" }).click();
      await expect(page.getByText("No components selected. You can submit instructions alone.")).toBeVisible();
      await expect(page.getByText("Add at least one component.")).toHaveCount(0);
      await page.getByRole("button", { name: "Submit for review" }).click();
      await expect(page.getByRole("heading", { name: "Give the Agent File a stable identity" })).toBeFocused();
      await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue(name);
    } finally {
      if (id) await page.request.delete(`${API_BASE}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${await getAccessToken()}` },
      });
    }
  });

  test("inline Prompt is a real draft, survives Agent save, and is not created twice", async ({ page }, testInfo) => {
    await loginToWebUI(page);
    const token = await getAccessToken();
    const suffix = crypto.randomUUID().slice(0, 12);
    const name = `pw-agent-inline-${suffix}`;
    const prompt = `pw-prompt-inline-${suffix}`;
    let agentId: string | null = null;
    let promptId: string | null = null;
    try {
      await page.goto("/agents/builder");
      await page.getByRole("textbox", { name: "Agent File name" }).fill(name);
      await page.getByRole("textbox", { name: "Description" }).fill("Agent with a saved Prompt draft");
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("textbox", { name: "Agent File prompt" }).fill("Use the linked Prompt when reviewing changes.");
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("tab", { name: "Prompts" }).click();
      await page.getByRole("button", { name: "Create new" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("textbox", { name: "Name *" }).fill(prompt);
      await dialog.getByRole("textbox", { name: "Description *" }).fill("Inline creation must persist independently");
      const result = page.waitForResponse((res) => res.url().endsWith("/api/v1/prompts/draft") && res.request().method() === "POST");
      await dialog.getByRole("button", { name: "Save Draft" }).click();
      const response = await result;
      expect(response.ok(), await response.text()).toBeTruthy();
      promptId = (await response.json()).id as string;
      await expect(dialog).toBeHidden();
      await expect(page.getByText(prompt, { exact: true }).last()).toBeVisible();
      await expect(page.getByText("Your drafts & submissions")).toBeVisible();
      await expect(page.getByRole("button", { name: `Remove ${prompt}` })).toBeVisible();
      await page.getByRole("button", { name: `Remove ${prompt}` }).click();
      await page.getByRole("textbox", { name: "Search Prompts" }).fill(prompt);
      await page.getByRole("button", { name: new RegExp(prompt) }).click();
      await expect(page.getByText(prompt, { exact: true }).last()).toBeVisible();
      await expect(page.getByText(/saved as a draft and added to your Agent File/)).toBeHidden({ timeout: 15_000 });
      await expect(page.getByText("All components valid")).toBeVisible();
      await expect(page.getByText("Needs target")).toHaveCount(0);
      await page.getByRole("heading", { name: "Choose building blocks" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath("inline-prompt-desktop.png"), fullPage: true });
      await page.setViewportSize({ width: 320, height: 800 });
      await page.getByRole("button", { name: new RegExp(prompt) }).first().scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      await page.screenshot({ path: testInfo.outputPath("owner-draft-picker-320.png"), fullPage: true });
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.getByRole("textbox", { name: "Search Prompts" }).clear();
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await page.getByRole("button", { name: "Save draft" }).click();
      await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
      agentId = new URL(page.url()).searchParams.get("draft");
      await page.reload();
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("tab", { name: "Prompts" }).click();
      await expect(page.getByText(prompt, { exact: true }).last()).toBeVisible();
      await page.getByRole("button", { name: "Continue to review" }).click();
      await expect(page.getByText(/linked component is not yet approved/)).toBeVisible();
      await expect(page.getByRole("link", { name: "Open Prompts submissions" })).toHaveAttribute("href", "/resources/prompts?view=my");
      await page.screenshot({ path: testInfo.outputPath("inline-review-desktop.png"), fullPage: true });
      await page.locator("footer").last().scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath("inline-review-desktop-actions.png") });
      await page.setViewportSize({ width: 320, height: 800 });
      await page.screenshot({ path: testInfo.outputPath("inline-prompt-mobile.png"), fullPage: true });
      await page.locator("footer").last().scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath("inline-review-mobile-actions.png") });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      const component = await page.request.get(`${API_BASE}/api/v1/prompts/${promptId}`, { headers: { Authorization: `Bearer ${token}` } });
      expect(component.ok()).toBeTruthy();
      expect((await component.json()).status).toBe("draft");
    } finally {
      for (const [type, id] of [["agents", agentId], ["prompts", promptId]] as const) {
        if (id) await page.request.delete(`${API_BASE}/api/v1/${type}/${id}`, { headers: { Authorization: `Bearer ${token}` } });
      }
    }
  });

  test("inline uploaded Skill keeps the folder version identity when saving an Agent draft", async ({ page }) => {
    test.skip(process.env.OBSERVAL_ISOLATED_STACK !== "true", "Creates an immutable Skill folder draft; requires isolated stack");
    await loginToWebUI(page);
    const token = await getAccessToken();
    const skillName = `pw-inline-folder-${crypto.randomUUID().slice(0, 8)}`;
    const agentName = `pw-folder-agent-${crypto.randomUUID().slice(0, 8)}`;
    let agentId: string | null = null;
    await page.goto("/agents/builder");
    try {
      await page.getByRole("textbox", { name: "Agent File name" }).fill(agentName);
      await page.getByRole("textbox", { name: "Description" }).fill("Agent links an exact Skill folder draft");
      await page.getByRole("button", { name: "3 Components Tools and capabilities" }).click();
      await page.getByRole("tab", { name: "Skills" }).click();
      await page.getByRole("button", { name: "Create new" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("tab", { name: "Upload" }).click();
      await dialog.locator("#skill-file-upload").setInputFiles({
        name: "SKILL.md", mimeType: "text/markdown", buffer: Buffer.from(`---\nname: ${skillName}\ndescription: Inline folder creation\n---\n\n# Instructions\nReview safely.\n`),
      });
      const result = page.waitForResponse((response) => response.url().endsWith("/api/v1/skills/folder-drafts") && response.request().method() === "POST");
      await dialog.getByRole("button", { name: "Save folder draft" }).click();
      const response = await result;
      expect(response.ok(), await response.text()).toBeTruthy();
      const folder = await response.json() as { listing_id: string; version_id: string };
      await expect(page.getByText(skillName, { exact: true })).toBeVisible();
      await page.getByRole("button", { name: /Identity Name and ownership/ }).click();
      await page.getByRole("button", { name: "Save draft" }).click();
      await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
      agentId = new URL(page.url()).searchParams.get("draft");
      const saved = await page.request.get(`${API_BASE}/api/v1/agents/${agentId}`, { headers: { Authorization: `Bearer ${token}` } });
      expect(saved.ok(), await saved.text()).toBeTruthy();
      expect((await saved.json()).component_links).toEqual(expect.arrayContaining([expect.objectContaining({
        component_id: folder.listing_id, version_ref: "1.0.0",
      })]));
      const manifest = await page.request.get(`${API_BASE}/api/v1/skills/${folder.listing_id}/versions/${folder.version_id}/manifest`, { headers: { Authorization: `Bearer ${token}` } });
      expect(manifest.ok()).toBeTruthy();
    } finally {
      if (agentId) await page.request.delete(`${API_BASE}/api/v1/agents/${agentId}`, { headers: { Authorization: `Bearer ${token}` } });
    }
  });

  test("all five inline component drafts survive Agent save, reload and review submission", async ({ page }) => {
    test.setTimeout(150_000);
    await loginToWebUI(page);
    const token = await getAccessToken();
    const headers = { Authorization: `Bearer ${token}` };
    const suffix = crypto.randomUUID().slice(0, 8);
    const agentName = `pw-inline-all-${suffix}`;
    const created: { kind: string; id: string; name: string }[] = [];
    let agentId: string | null = null;
    try {
      await page.goto("/agents/builder");
      await page.getByRole("textbox", { name: "Agent File name" }).fill(agentName);
      await page.getByRole("textbox", { name: "Description" }).fill("End-to-end five-kind composition");
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("textbox", { name: "Agent File prompt" }).fill("Review all five linked components.");
      await page.getByRole("button", { name: "Continue to components" }).click();
      for (const [tab, kind] of [["MCPs", "mcps"], ["Skills", "skills"], ["Hooks", "hooks"], ["Prompts", "prompts"], ["Sandboxes", "sandboxes"]] as const) {
        const name = `pw-inline-${kind}-${suffix}`;
        await page.getByRole("tab", { name: tab }).click();
        await page.getByRole("button", { name: "Create new" }).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("textbox", { name: "Name *" }).fill(name);
        await dialog.getByRole("textbox", { name: "Description *" }).fill(`Real ${kind} draft`);
        if (kind === "mcps") await dialog.getByRole("textbox", { name: /Git URL/ }).fill("https://github.com/example/mcp-server");
        if (kind === "skills") {
          await dialog.getByRole("tab", { name: "Paste" }).click();
          await dialog.getByRole("textbox", { name: "SKILL.md *" }).fill(`---\nname: ${name}\ndescription: Inline test\n---\n\n# Review instructions\n`);
        }
        if (kind === "hooks") await dialog.getByRole("textbox", { name: "Handler Config (JSON)" }).fill('{"command":"echo reviewed"}');
        if (kind === "prompts") await dialog.getByRole("textbox", { name: "Template *" }).fill("Review {{task}}.");
        if (kind === "sandboxes") await dialog.getByRole("textbox", { name: /Image \/ Artifact Ref/ }).fill("ubuntu:22.04");
        const result = page.waitForResponse((res) => res.url().endsWith(`/api/v1/${kind}/draft`) && res.request().method() === "POST");
        await dialog.getByRole("button", { name: "Save Draft" }).click();
        const response = await result;
        expect(response.ok(), await response.text()).toBeTruthy();
        created.push({ kind, id: (await response.json()).id as string, name });
        await expect(dialog).toBeHidden();
        await expect(page.getByText(name, { exact: true }).last()).toBeVisible();
      }
      await page.getByRole("button", { name: "Continue to review" }).click();
      for (const tab of ["MCPs", "Skills", "Hooks", "Prompts", "Sandboxes"]) {
        await expect(page.getByRole("link", { name: `Open ${tab} submissions` })).toHaveAttribute("href", new RegExp("/resources/"));
      }
      await page.getByRole("button", { name: /Identity Name and ownership/ }).click();
      await page.getByRole("button", { name: "Save draft" }).click();
      await expect(page).toHaveURL(/\/agents\/builder\?draft=[a-f0-9-]+/);
      agentId = new URL(page.url()).searchParams.get("draft");
      const saved = await page.request.get(`${API_BASE}/api/v1/agents/${agentId}`, { headers });
      expect(saved.ok(), await saved.text()).toBeTruthy();
      const links = (await saved.json()).component_links as { component_id: string; version_ref: string }[];
      expect(links).toHaveLength(5);
      expect(links.map((link) => link.component_id).sort()).toEqual(created.map((item) => item.id).sort());
      expect(links.every((link) => link.version_ref === "0.1.0" || link.version_ref === "1.0.0")).toBeTruthy();
      await page.reload();
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("button", { name: "Continue to components" }).click();
      for (const [tab, kind] of [["MCPs", "mcps"], ["Skills", "skills"], ["Hooks", "hooks"], ["Prompts", "prompts"], ["Sandboxes", "sandboxes"]] as const) {
        await page.getByRole("tab", { name: tab }).click();
        await expect(page.getByText(created.find((item) => item.kind === kind)!.name, { exact: true }).last()).toBeVisible();
      }
      await page.getByRole("button", { name: "Continue to review" }).click();
      await page.getByRole("button", { name: "Submit for review" }).click();
      await expect(page).toHaveURL(new RegExp(`/agents/${agentId}$`));
      const submitted = await page.request.get(`${API_BASE}/api/v1/agents/${agentId}`, { headers });
      expect(submitted.ok(), await submitted.text()).toBeTruthy();
      expect((await submitted.json()).status).toBe("pending");
    } finally {
      if (agentId) await page.request.delete(`${API_BASE}/api/v1/agents/${agentId}`, { headers });
      for (const { kind, id } of created) {
        await page.request.delete(`${API_BASE}/api/v1/${kind}/${id}`, { headers });
      }
    }
  });

  test("a failed inline save preserves its form and retry creates only one component", async ({ page }) => {
    await loginToWebUI(page);
    const token = await getAccessToken();
    const name = `pw-inline-retry-${crypto.randomUUID()}`;
    let promptId: string | null = null;
    await page.goto("/agents/builder");
    try {
      await page.getByRole("button", { name: "3 Components Tools and capabilities" }).click();
      await page.getByRole("tab", { name: "Prompts" }).click();
      await page.getByRole("button", { name: "Create new" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("textbox", { name: "Name *" }).fill(name);
      await dialog.getByRole("textbox", { name: "Description *" }).fill("Keep this draft on network failure");
      await dialog.getByRole("textbox", { name: "Template *" }).fill("Review {{task}} safely.");
      await page.route("**/api/v1/prompts/draft", (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Temporary outage"}' }));
      const failed = page.waitForResponse((res) => res.url().endsWith("/api/v1/prompts/draft") && res.status() === 503);
      await dialog.getByRole("button", { name: "Save Draft" }).click();
      await failed;
      await expect(dialog.getByRole("button", { name: "Save Draft" })).toBeEnabled();
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("textbox", { name: "Template *" })).toHaveValue("Review {{task}} safely.");
      await page.unroute("**/api/v1/prompts/draft");
      const result = page.waitForResponse((response) => response.url().endsWith("/api/v1/prompts/draft") && response.request().method() === "POST");
      await dialog.getByRole("button", { name: "Save Draft" }).click();
      const response = await result;
      expect(response.ok(), await response.text()).toBeTruthy();
      promptId = (await response.json()).id as string;
      await expect(dialog).toBeHidden();
      await expect(page.getByText(name, { exact: true })).toHaveCount(1);
      const saved = await page.request.get(`${API_BASE}/api/v1/prompts/${promptId}`, { headers: { Authorization: `Bearer ${token}` } });
      expect(saved.ok(), await saved.text()).toBeTruthy();
      expect((await saved.json()).template).toBe("Review {{task}} safely.");
    } finally {
      if (promptId) await page.request.delete(`${API_BASE}/api/v1/prompts/${promptId}`, { headers: { Authorization: `Bearer ${token}` } });
    }
  });

  test("component search reaches server rather than filtering a truncated first page", async ({ page }) => {
    await loginToWebUI(page);
    const searches: string[] = [];
    await page.route("**/api/v1/prompts?**", async (route) => {
      const query = new URL(route.request().url()).searchParams.get("search") ?? "";
      searches.push(query);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(query
        ? [{ id: "00000000-0000-4000-8000-000000000027", name: "Deep catalog prompt", description: "Searchable beyond page one", status: "approved", version: "1.0.0" }]
        : []),
      });
    });
    await page.goto("/agents/builder");
    await page.getByRole("button", { name: "3 Components Tools and capabilities" }).click();
    await page.getByRole("tab", { name: "Prompts" }).click();
    await page.getByRole("textbox", { name: "Search Prompts" }).fill("deep catalog");
    await expect(page.getByRole("button", { name: /Deep catalog prompt/ })).toBeVisible();
    expect(searches).toContain("deep catalog");
  });

  test("owner draft lookup failure leaves approved resources usable and offers retry", async ({ page }) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/prompts/my?**", (route) => route.fulfill({
      status: 503, contentType: "application/json", body: '{"detail":"Temporarily unavailable"}',
    }));
    await page.route("**/api/v1/prompts?**", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify([{
        id: "00000000-0000-4000-8000-000000000027", name: "Approved team prompt",
        status: "approved", version: "1.0.0", visibility: "public",
      }]),
    }));
    await page.goto("/agents/builder");
    await page.getByRole("button", { name: "3 Components Tools and capabilities" }).click();
    await page.getByRole("tab", { name: "Prompts" }).click();
    const failure = page.getByRole("alert", { name: "" }).filter({ hasText: "Could not load your saved components" });
    await expect(failure).toBeVisible();
    await expect(page.getByRole("button", { name: /Approved team prompt/ })).toBeVisible();
    await page.unroute("**/api/v1/prompts/my?**");
    await failure.getByRole("button", { name: "Retry" }).click();
    await expect(failure).toBeHidden();
  });

  test("reviewer changes return a regular user's Agent for correction and resubmission", async ({ page }, testInfo) => {
    test.skip(process.env.OBSERVAL_ISOLATED_STACK !== "true", "Creates a disposable user and review; requires isolated stack");
    test.setTimeout(100_000);
    await loginToWebUI(page);
    const adminToken = await getAccessToken();
    const suffix = crypto.randomUUID().slice(0, 8);
    const email = `builder-${suffix}@example.test`;
    const password = `Safe-E2E-${suffix}!Aa4`;
    const createdUser = await page.request.post(`${API_BASE}/api/v1/admin/users`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { email, username: `builder-${suffix}`, name: "Builder user", role: "user", password },
    });
    expect(createdUser.ok(), await createdUser.text()).toBeTruthy();
    let id: string | null = null;
    let token = "";
    try {
      const login = await page.request.post(`${API_BASE}/api/v1/auth/login`, { data: { email, password } });
      expect(login.ok(), await login.text()).toBeTruthy();
      token = (await login.json()).access_token as string;
      const headers = { Authorization: `Bearer ${token}` };
      await page.evaluate((userToken) => {
        sessionStorage.setItem("observal_access_token", userToken);
        localStorage.setItem("observal_user_role", "user");
      }, token);
      await page.reload();
      const name = `pw-agent-feedback-${suffix}`;
      await page.goto("/agents/builder");
      await page.getByRole("textbox", { name: "Agent File name" }).fill(name);
      await page.getByRole("textbox", { name: "Description" }).fill("Before reviewer feedback");
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("textbox", { name: "Agent File prompt" }).fill("Review code.");
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("button", { name: "Continue to review" }).click();
      const created = page.waitForResponse((res) => res.url().endsWith("/api/v1/agents") && res.request().method() === "POST");
      await page.getByRole("button", { name: "Submit for review" }).click();
      const response = await created;
      expect(response.ok(), await response.text()).toBeTruthy();
      id = (await response.json()).id as string;
      await expect(page).toHaveURL(new RegExp(`/agents/${id}$`));
      const number = await reviewNumber("agent", id);
      const reviewer = await page.request.post(`${API_BASE}/api/v1/auth/login`, {
        data: { email: process.env.DEMO_SUPER_EMAIL ?? "super@demo.example", password: process.env.DEMO_SUPER_PASSWORD ?? "super-changeme" },
      });
      expect(reviewer.ok(), await reviewer.text()).toBeTruthy();
      const verdict = await page.request.post(`${API_BASE}/api/v1/reviews/${number}/submissions`, {
        headers: { Authorization: `Bearer ${(await reviewer.json()).access_token}` },
        data: { verdict: "request_changes", body: "Clarify safe code-review boundaries." },
      });
      expect(verdict.ok(), await verdict.text()).toBeTruthy();
      await page.goto("/resources/agents?view=my");
      const ownSubmission = page.locator("div.flex.flex-wrap.items-center.gap-3.py-3").filter({ hasText: name });
      await expect(ownSubmission).toBeVisible();
      await ownSubmission.getByRole("button", { name: "Read feedback" }).click();
      await expect(page).toHaveURL(new RegExp(`/review/${number}$`));
      await expect(page.getByText("Clarify safe code-review boundaries.")).toBeVisible();
      await page.goto("/resources/agents?view=my");
      await page.locator("div.flex.flex-wrap.items-center.gap-3.py-3").filter({ hasText: name })
        .getByRole("button", { name: "Edit", exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/agents/builder\\?draft=${id}$`));
      await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue(name);
      await expect(page.getByRole("link", { name: "Read reviewer feedback" })).toHaveAttribute("href", `/review/${number}`);
      await page.screenshot({ path: testInfo.outputPath("agent-feedback-direct.png"), fullPage: true });
      await page.setViewportSize({ width: 320, height: 800 });
      await expect(page.getByRole("link", { name: "Read reviewer feedback" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      await page.screenshot({ path: testInfo.outputPath("agent-feedback-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.getByRole("textbox", { name: "Description" }).fill("Clarified after reviewer feedback");
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("button", { name: "Continue to review" }).click();
      await page.getByRole("button", { name: "Submit revision" }).click();
      await expect(page).toHaveURL(new RegExp(`/agents/${id}$`));
      const revised = await page.request.get(`${API_BASE}/api/v1/agents/${id}`, { headers });
      expect(revised.ok(), await revised.text()).toBeTruthy();
      expect((await revised.json()).description).toBe("Clarified after reviewer feedback");
    } finally {
      if (id && token) await page.request.delete(`${API_BASE}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      // Review history keeps non-null user FKs. This disposable account lives only
      // on the isolated stack; admin hard-deletion of reviewed users is a separate bug.
    }
  });

  test("editing a pending Agent updates its own ID; an approved edit makes a new reviewable version", async ({ page }) => {
    test.setTimeout(120_000);
    await loginToWebUI(page);
    const token = await getAccessToken();
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const name = `pw-agent-release-${crypto.randomUUID()}`;
    const seeded = await page.request.post(`${API_BASE}/api/v1/agents/draft`, { headers, data: {
      name, description: "Before edits", prompt: "Review safely", owner: "test", model_name: "", version: "1.0.0", components: [],
    } });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();
    const id = (await seeded.json()).id as string;
    try {
      await page.goto(`/agents/builder?edit=${id}`);
      await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue(name);
      await page.getByRole("textbox", { name: "Description" }).fill("Pending edit without duplicate Agent");
      await page.getByRole("button", { name: "Save draft" }).click();
      await expect(page.getByText("Draft updated", { exact: true })).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/agents/builder\\?edit=${id}`));
      const saved = await page.request.get(`${API_BASE}/api/v1/agents/${id}`, { headers });
      expect(saved.ok()).toBeTruthy();
      expect((await saved.json()).description).toBe("Pending edit without duplicate Agent");
      await page.getByRole("link", { name: "Builder", exact: true }).click();
      await page.getByRole("link", { name: /^Agent File / }).click();
      await expect(page.getByRole("textbox", { name: "Agent File name" })).toHaveValue("");
      await page.goto(`/agents/builder?edit=${id}`);
      await expect(page.getByRole("textbox", { name: "Description" })).toHaveValue("Pending edit without duplicate Agent");
      const submitted = await page.request.post(`${API_BASE}/api/v1/agents/${id}/submit`, { headers });
      expect(submitted.ok(), await submitted.text()).toBeTruthy();
      await publishSubject("agent", id);
      await page.goto(`/agents/builder?edit=${id}`);
      await expect(page.getByText("This release is immutable.", { exact: false })).toBeVisible();
      await expect(page.getByRole("button", { name: "Save draft" })).toHaveCount(0);
      await page.getByRole("textbox", { name: "Description" }).fill("Reviewed second version");
      await page.getByRole("button", { name: "Continue to behavior" }).click();
      await page.getByRole("button", { name: "Continue to components" }).click();
      await page.getByRole("button", { name: "Continue to review" }).click();
      await page.getByRole("button", { name: "Submit new version" }).click();
      await expect(page.getByRole("dialog", { name: "Submit a new Agent File version" })).toBeVisible();
      const result = page.waitForResponse((res) => res.url().endsWith(`/api/v1/agents/${id}/versions`) && res.request().method() === "POST");
      await page.getByRole("dialog").getByRole("button", { name: "Submit for review" }).click();
      const version = await result;
      expect(version.ok(), await version.text()).toBeTruthy();
      await expect(page).toHaveURL(new RegExp(`/agents/${id}$`));
      const versions = await page.request.get(`${API_BASE}/api/v1/agents/${id}/versions`, { headers });
      expect(versions.ok()).toBeTruthy();
      expect(JSON.stringify(await versions.json())).toContain("1.0.1");
    } finally {
      await page.request.delete(`${API_BASE}/api/v1/agents/${id}`, { headers });
    }
  });
});
