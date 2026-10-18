// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { API_BASE, getAccessToken, loginToWebUI, reviewNumber } from "./helpers";

const kinds = [
  ["Agent File", "agents"], ["MCP Client", "mcps"], ["Skill", "skills"],
  ["Hook", "hooks"], ["Prompt", "prompts"], ["Sandbox", "sandboxes"],
] as const;

test.describe("Resource builder", () => {
  test("offers six distinct creation paths and retains the Agent File composer", async ({ page }, testInfo) => {
    await loginToWebUI(page);
    await page.goto("/build");
    for (const [label, kind] of kinds) {
      await expect(page.getByRole("link", { name: new RegExp(`^${label} `) })).toHaveAttribute(
        "href", kind === "agents" ? "/agents/builder" : `/build?kind=${kind}`,
      );
    }
    await expect(page.getByRole("heading", { name: "Build a component" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("chooser-agent-first.png"), fullPage: true });
    await page.setViewportSize({ width: 320, height: 800 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath("chooser-agent-first-320.png"), fullPage: true });
    await page.evaluate(() => localStorage.setItem("observal-mode", "dark"));
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("heading", { name: "What will you build?" })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Agent File / })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("chooser-agent-first-dark-320.png"), fullPage: true });
    await page.getByRole("link", { name: /^Agent File / }).click();
    await expect(page).toHaveURL(/\/agents\/builder/);
  });

  test("five components save, reopen, edit and submit real drafts", async ({ page }) => {
    test.setTimeout(120_000);
    await loginToWebUI(page);
    for (const kind of ["mcps", "skills", "hooks", "prompts", "sandboxes"] as const) {
      const name = `pw-builder-${kind}-${Date.now()}`;
      await page.goto(`/build?kind=${kind}`);
      await page.getByRole("textbox", { name: "Name *" }).fill(name);
      await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
      await page.getByRole("textbox", { name: "Description *" }).fill(`First ${kind} description`);
      await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
      await page.getByRole("button", { name: "Save Draft" }).click();
      await expect(page).toHaveURL(new RegExp(`/build\\?kind=${kind}&draft=[a-f0-9-]+`));
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
      await page.getByRole("textbox", { name: "Description *" }).fill(`Revised ${kind} description`);
      await page.getByRole("button", { name: "Save Changes" }).click();
      await expect(page.getByText("Draft updated")).toBeVisible();
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Description *" })).toHaveValue(`Revised ${kind} description`);
      if (kind === "mcps") {
        await page.getByRole("button", { name: "Insert example" }).click();
        await expect(page.getByText(/Config updated/)).toBeVisible();
      } else if (kind === "skills") {
        await page.getByRole("tab", { name: "Paste" }).click();
        await page.getByRole("textbox", { name: "SKILL.md *" }).fill(`---\nname: ${name}\ndescription: A tested skill\n---\n\n## Instructions\n\nReview the resource.`);
      } else if (kind === "hooks") {
        await page.getByRole("textbox", { name: "Handler Config (JSON)" }).fill('{"command":"echo verified"}');
      } else if (kind === "prompts") {
        await page.getByRole("textbox", { name: "Template *" }).fill("Review this task carefully.");
      } else {
        await page.getByRole("textbox", { name: /Image \/ Artifact Ref/ }).fill("ubuntu:22.04");
      }
      await page.getByRole("button", { name: "Save & Submit for Review" }).click();
      await expect(page).toHaveURL(new RegExp(`/components/|/${kind}/`));
      await expect(page.getByText(name).first()).toBeVisible();
    }
  });

  test("requested Prompt changes open the exact reviewer feedback from the editor", async ({ page }) => {
    test.setTimeout(90_000);
    await loginToWebUI(page);
    const token = await getAccessToken();
    const name = `pw-prompt-review-${crypto.randomUUID()}`;
    let id: string | null = null;
    try {
      await page.goto("/build?kind=prompts");
      await page.getByRole("textbox", { name: "Name *" }).fill(name);
      await page.getByRole("textbox", { name: "Description *" }).fill("Revisit after a reviewer comment");
      await page.getByRole("textbox", { name: "Template *" }).fill("Review {{task}}.");
      const created = page.waitForResponse((response) => response.url().endsWith("/api/v1/prompts/submit") && response.request().method() === "POST");
      await page.getByRole("button", { name: "Submit for Review" }).click();
      const response = await created;
      expect(response.ok(), await response.text()).toBeTruthy();
      id = (await response.json()).id as string;
      const number = await reviewNumber("prompt", id);
      const reviewer = await page.request.post(`${API_BASE}/api/v1/auth/login`, {
        data: { email: process.env.DEMO_SUPER_EMAIL ?? "super@demo.example", password: process.env.DEMO_SUPER_PASSWORD ?? "super-changeme" },
      });
      expect(reviewer.ok(), await reviewer.text()).toBeTruthy();
      const verdict = await page.request.post(`${API_BASE}/api/v1/reviews/${number}/submissions`, {
        headers: { Authorization: `Bearer ${(await reviewer.json()).access_token}` },
        data: { verdict: "request_changes", body: "Explain how the template handles unsafe requests." },
      });
      expect(verdict.ok(), await verdict.text()).toBeTruthy();
      await page.goto(`/build?kind=prompts&draft=${id}`);
      await expect(page.getByRole("textbox", { name: "Template *" })).toHaveValue("Review {{task}}.");
      await expect(page.getByRole("link", { name: "Read review feedback" })).toHaveAttribute("href", `/review/${number}`);
      await page.getByRole("link", { name: "Read review feedback" }).click();
      await expect(page.getByText("Explain how the template handles unsafe requests.")).toBeVisible();
    } finally {
      if (id) await page.request.delete(`${API_BASE}/api/v1/prompts/${id}`, { headers: { Authorization: `Bearer ${token}` } });
    }
  });

  test("failed draft save focuses the error and keeps the editor intact", async ({ page }) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/prompts/draft", (route) => route.fulfill({
      status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Service temporarily unavailable" }),
    }));
    await page.goto("/build?kind=prompts");
    await page.getByRole("textbox", { name: "Name *" }).fill("unsaved-prompt");
    await page.getByRole("button", { name: "Save Draft" }).click();
    const error = page.locator('[role="alert"][tabindex="-1"]');
    await expect(error).toBeFocused();
    await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue("unsaved-prompt");
    await page.unroute("**/api/v1/prompts/draft");
  });

  test("pasted Skill draft reopens, edits and submits without a folder revision", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=skills");
    const name = `pw-pasted-skill-${Date.now()}`;
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("An editable pasted Skill");
    await page.getByRole("tab", { name: "Paste" }).click();
    await page.getByRole("textbox", { name: "SKILL.md *" }).fill(`---\nname: ${name}\ndescription: A tested skill\n---\n\n## Instructions\n\nReview changes.`);
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page).toHaveURL(/\/build\?kind=skills&draft=[a-f0-9-]+/);
    await page.reload();
    await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("A revised pasted Skill");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect(page.getByText("Draft updated")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("textbox", { name: "Description *" })).toHaveValue("A revised pasted Skill");
    await page.getByRole("button", { name: "Save & Submit for Review" }).click();
    await expect(page).toHaveURL(/\/components\/skills\//);
  });

  test("My submissions resumes a complete Prompt without discarding its template", async ({ page }) => {
    await loginToWebUI(page);
    const name = `pw-owner-prompt-${Date.now()}`;
    await page.goto("/build?kind=prompts");
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Owner resume regression");
    await page.getByRole("textbox", { name: "Template *" }).fill("Keep this unique template");
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page).toHaveURL(/draft=[a-f0-9-]+/);
    const draftUrl = page.url();
    await page.goto("/resources/prompts?view=my");
    const row = page.getByText(name, { exact: true }).last().locator("xpath=ancestor::div[contains(@class,'py-3')][1]");
    await row.getByRole("link", { name: "Continue draft" }).click();
    await expect(page).toHaveURL(draftUrl);
    await expect(page.getByRole("textbox", { name: "Template *" })).toHaveValue("Keep this unique template");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await page.goto(draftUrl);
    await expect(page.getByRole("textbox", { name: "Template *" })).toHaveValue("Keep this unique template");
  });

  test("pending Prompt edit loads the full template instead of a summary row", async ({ page }) => {
    await loginToWebUI(page);
    const name = `pw-pending-prompt-${Date.now()}`;
    await page.goto("/build?kind=prompts");
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Pending content proof");
    await page.getByRole("textbox", { name: "Template *" }).fill("Keep the pending template");
    await page.getByRole("button", { name: "Submit for Review" }).click();
    await expect(page).toHaveURL(/\/components\/prompts\//);
    const detailUrl = page.url();
    await page.goto("/resources/prompts?view=my");
    const row = page.getByText(name, { exact: true }).last().locator("xpath=ancestor::div[contains(@class,'py-3')][1]");
    await row.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Template *" })).toHaveValue("Keep the pending template");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await page.goto(detailUrl);
    await expect(page.getByText("Keep the pending template").first()).toBeVisible();
  });

  test("accepts Git-only MCP submissions without requiring a pasted command", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=mcps");
    await page.getByRole("textbox", { name: "Name *" }).fill(`pw-git-mcp-${Date.now()}`);
    await page.getByRole("textbox", { name: "Description *" }).fill("A Git-only MCP source");
    await page.getByRole("textbox", { name: /Git URL/ }).fill("https://github.com/example/mcp-server");
    await page.getByRole("button", { name: "Submit for Review" }).click();
    await expect(page).toHaveURL(/\/components\/mcps\//);
  });

  test("locks only the editable form while a draft request is pending", async ({ page }) => {
    await loginToWebUI(page);
    let startRequest!: () => void;
    let finishRequest!: () => void;
    const requested = new Promise<void>((resolve) => { startRequest = resolve; });
    const release = new Promise<void>((resolve) => { finishRequest = resolve; });
    await page.route("**/api/v1/prompts/draft", async (route) => {
      startRequest();
      await release;
      await route.continue();
    });
    await page.goto("/build?kind=prompts");
    const name = `pw-save-lock-${Date.now()}`;
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("button", { name: "Save Draft" }).click();
    try {
      await requested;
      await expect(page.getByRole("region", { name: "Prompt editor" })).toHaveAttribute("aria-busy", "true");
      await expect(page.locator("#prompt-template").locator("xpath=ancestor::div[@inert][1]")).toHaveCount(1);
      await expect(page.getByRole("button", { name: "Save Draft" })).toBeVisible();
    } finally {
      finishRequest();
    }
    await expect(page).toHaveURL(/\/build\?kind=prompts&draft=/);
    await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
  });

  test("retains save focus and announces JSON validation on the focused editor", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=sandboxes");
    await page.getByRole("textbox", { name: "Name *" }).fill(`pw-a11y-sandbox-${Date.now()}`);
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page).toHaveURL(/draft=[a-f0-9-]+/);
    const save = page.getByRole("button", { name: "Save Changes" });
    await save.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Draft updated")).toBeVisible();
    await expect(save).toBeFocused();
    const editor = page.locator("#sandbox-limits [contenteditable=true]");
    await editor.fill("{ invalid");
    await save.click();
    await expect(editor).toBeFocused();
    await expect(editor).toHaveAttribute("aria-label", "Resource Limits JSON");
    await expect(editor).toHaveAttribute("aria-invalid", "true");
    await expect(editor).toHaveAttribute("aria-describedby", "component-form-error");
  });

  test("blocks accidental navigation and allows correction of invalid submissions", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=prompts");
    await page.getByRole("textbox", { name: "Name *" }).fill(`pw-guard-${Date.now()}`);
    await page.getByRole("link", { name: "Builder", exact: true }).click();
    await expect(page.getByRole("alertdialog", { name: "Discard unsaved changes?" })).toBeVisible();
    await page.getByRole("button", { name: "Keep editing" }).click();
    await expect(page).toHaveURL(/\/build\?kind=prompts/);
    await page.getByRole("textbox", { name: "Description *" }).fill("Prompt with inline validation");
    await page.getByRole("button", { name: "Submit for Review" }).click();
    await expect(page.getByRole("alert")).toContainText("Template is required");
    await expect(page.locator("#prompt-template")).toBeFocused();
    await page.getByRole("link", { name: "Builder", exact: true }).click();
    await page.getByRole("button", { name: "Discard changes" }).click();
    await expect(page).toHaveURL(/\/build$/);
  });

  test("submitted prompt draft reaches its native review detail", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=prompts");
    const name = `pw-reviewed-prompt-${Date.now()}`;
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Browser-verified prompt submission");
    await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page).toHaveURL(/draft=[a-f0-9-]+/);
    await page.getByRole("textbox", { name: "Template *" }).fill("You are a resource author.");
    await page.getByRole("button", { name: "Save & Submit for Review" }).click();
    await expect(page).toHaveURL(/\/prompts\/[a-f0-9-]+/);
    await expect(page.getByText(name).first()).toBeVisible();
  });

  test("valid direct submit reaches review without first creating a draft", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=prompts");
    const name = `pw-direct-prompt-${Date.now()}`;
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Direct browser submission");
    await page.getByRole("textbox", { name: "Template *" }).fill("Write a focused summary.");
    await page.getByRole("button", { name: "Submit for Review" }).click();
    await expect(page).toHaveURL(/\/prompts\/[a-f0-9-]+/);
    await expect(page.getByText(name).first()).toBeVisible();
  });

  test("invalid sandbox JSON stays editable and focuses the failing code editor", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=sandboxes");
    await page.getByRole("textbox", { name: "Name *" }).fill(`pw-invalid-json-${Date.now()}`);
    const limits = page.locator('#sandbox-limits [contenteditable="true"]');
    await limits.click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText("{bad");
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page.getByRole("alert")).toContainText("Resource limits must be valid JSON");
    await expect(limits).toBeFocused();
    await expect(page).toHaveURL(/\/build\?kind=sandboxes$/);
  });

  test("mobile authoring puts the editor before the chooser", async ({ page }) => {
    await loginToWebUI(page);
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto("/build?kind=hooks");
    await expect(page.getByRole("link", { name: "Change resource kind" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Resource details" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Choose resource kind" })).toHaveCount(0);
    const editorTop = await page.getByRole("heading", { name: "Resource details" }).evaluate((node) => node.getBoundingClientRect().top);
    expect(editorTop).toBeLessThan(780);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(321);
  });

  test("new folder Skill draft hands off to the exact version editor", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/build?kind=skills");
    const name = `pw-folder-${Date.now()}`;
    await page.getByRole("textbox", { name: "Name *" }).fill(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Versioned folder browser draft");
    await page.getByRole("tab", { name: "Upload" }).click();
    await page.locator("#skill-file-upload").setInputFiles({
      name: "SKILL.md", mimeType: "text/markdown",
      buffer: Buffer.from(`---\nname: ${name}\ndescription: Versioned folder browser draft\n---\n\n## Instructions\n\nCheck this exact version.`),
    });
    await expect(page.getByRole("button", { name: "Edit SKILL.md" })).toBeVisible();
    await page.getByRole("button", { name: "Save folder draft" }).click();
    await expect(page).toHaveURL(/\/resources\/skills\?.*folderVersionId=[a-f0-9-]+/);
    await expect(page.getByRole("button", { name: "Save folder changes" })).toBeVisible();
  });

  test("rejected Git Skill resubmits its observed exact version", async ({ page }) => {
    const admin = await getAccessToken();
    const timestamp = `${Date.now()}${crypto.randomUUID().slice(0, 8)}`;
    const email = `pw-git-${timestamp}@example.test`;
    const create = await fetch(`${API_BASE}/api/v1/admin/users`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${admin}` },
      body: JSON.stringify({ email, name: "Git Skill author", username: `pw-git-${timestamp}`, role: "user" }),
    });
    expect(create.status).toBe(200);
    const { password } = await create.json() as { password: string };
    const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
    });
    expect(login.status).toBe(200);
    const { access_token: author } = await login.json() as { access_token: string };
    const apiPost = async (path: string, body: object, token = author) => fetch(`${API_BASE}/api/v1/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(body),
    });
    const name = `pw-git-review-${timestamp}`;
    const saved = await apiPost("skills/draft", {
      name, version: "1.0.0", description: "Review the Git Skill", owner: "skill-author",
      git_url: "https://github.com/example/skill-demo", skill_path: "skills/demo", delivery_mode: "git_fetch", task_type: "general",
    });
    expect(saved.status).toBe(200);
    const { id } = await saved.json() as { id: string };
    const submitted = await apiPost(`skills/${id}/submit`, {});
    expect(submitted.status).toBe(200);
    const { review_number: number } = await submitted.json() as { review_number: number };
    const requested = await apiPost(`reviews/${number}/submissions`, { verdict: "request_changes", body: "Clarify the description" }, admin);
    expect(requested.status).toBe(201);

    await page.goto("/");
    await page.evaluate((token) => {
      sessionStorage.setItem("observal_access_token", token);
      localStorage.setItem("observal_user_role", "user");
    }, author);
    await page.goto(`/build?kind=skills&draft=${id}`);
    await expect(page.getByRole("link", { name: "Read review feedback" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Name *" })).toHaveValue(name);
    await page.getByRole("textbox", { name: "Description *" }).fill("Updated after reviewer requested changes");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect(page.getByText("Draft updated")).toBeVisible();
    await page.getByRole("button", { name: "Save & Resubmit" }).click();
    await expect(page).toHaveURL(/\/components\/skills\//);
    await expect(page.getByText(name).first()).toBeVisible();
  });
});
