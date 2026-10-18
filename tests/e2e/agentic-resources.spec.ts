// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";
import { loginToWebUI } from "./helpers";

const kinds = [
  ["Agent Files", "agents"], ["MCP Clients", "mcps"], ["Skills", "skills"],
  ["Hooks", "hooks"], ["Prompts", "prompts"], ["Sandboxes", "sandboxes"],
] as const;

test.describe("Agentic Resources wayfinding", () => {
  test("task filters survive reload and lead to the native catalogue", async ({ page }) => {
    await loginToWebUI(page);
    await page.goto("/discover");
    await page.getByRole("textbox", { name: "Describe the task" }).fill("review security");
    await page.getByRole("combobox", { name: "Resource kind" }).click();
    await page.getByRole("option", { name: "Skills" }).click();
    await expect(page).toHaveURL(/q=review(?:\+|%20)security.*kind=skill/);
    await page.reload();
    await expect(page.getByRole("textbox", { name: "Describe the task" })).toHaveValue("review security");
    await expect(page.getByRole("combobox", { name: "Resource kind" })).toContainText("Skills");
    await page.goto("/discover");
    await page.getByRole("link", { name: "Browse Agentic Resources by kind instead" }).click();
    await expect(page).toHaveURL(/\/resources\/agents/);
  });

  for (const [, kind] of kinds) {
    test(`${kind} catalogue supports Back and URL pagination`, async ({ page }) => {
      await loginToWebUI(page);
      const offsets: number[] = [];
      const route = `**/api/v1/${kind}?**`;
      await page.route(route, async (request) => {
        const url = new URL(request.request().url());
        const offset = Number(url.searchParams.get("offset") ?? 0);
        offsets.push(offset);
        const count = offset === 0 ? 20 : 1;
        await request.fulfill({
          status: 200,
          contentType: "application/json",
          headers: { "X-Total-Count": "21" },
          body: JSON.stringify(Array.from({ length: count }, (_, i) => ({
            id: `00000000-0000-4000-8000-${String(offset + i + 1).padStart(12, "0")}`,
            name: `Example ${kind} ${offset + i + 1}`, namespace: "demo", slug: `example-${kind}-${offset + i + 1}`,
            description: "A published example", version: "1.0.0", status: "approved", download_count: 0,
          }))),
        });
      });
      await page.goto(`/resources/${kind}`);
      await expect(page.getByRole("button", { name: "Next" })).toBeVisible();
      await page.getByRole("button", { name: "Next" }).click();
      await expect(page).toHaveURL(new RegExp(`/resources/${kind}\\?page=2`));
      await expect(page.getByText(`Example ${kind} 21`)).toBeVisible();
      await page.goBack();
      await expect(page.getByText(`Example ${kind} 1`, { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Next" }).click();
      await expect(page).toHaveURL(new RegExp(`/resources/${kind}\\?page=2`));
      await page.reload();
      await expect(page.getByText(`Example ${kind} 21`)).toBeVisible();
      expect(offsets).toContain(20);
    });
  }

  test("overlong owner search bookmarks stay within the server search limit", async ({ page }) => {
    await loginToWebUI(page);
    const searches: string[] = [];
    await page.route("**/api/v1/prompts/my?**", (request) => {
      searches.push(new URL(request.request().url()).searchParams.get("search") ?? "");
      return request.fulfill({ status: 200, contentType: "application/json", headers: { "X-Total-Count": "0" }, body: "[]" });
    });
    await page.goto(`/resources/prompts?view=my&search=${"x".repeat(201)}`);
    await expect.poll(() => searches.some((term) => term.length === 200)).toBe(true);
    await expect(page.getByRole("combobox", { name: /Search Prompts or filter by/ })).toHaveValue("x".repeat(200));
  });

  for (const [, kind] of kinds) {
    test(`${kind} owner search finds results past page one`, async ({ page }) => {
      await loginToWebUI(page);
      const searches: string[] = [];
      const route = `**/api/v1/${kind}/my?**`;
      await page.route(route, async (request) => {
        const url = new URL(request.request().url());
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const search = url.searchParams.get("search") ?? "";
        searches.push(search);
        const rows = search ? [{ name: `Hidden ${kind} match`, slug: `hidden-${kind}`, id: "00000000-0000-4000-8000-000000000021" }]
          : Array.from({ length: offset === 0 ? 20 : 1 }, (_, i) => ({
            name: `My ${kind} ${offset + i + 1}`, slug: `my-${kind}-${offset + i + 1}`,
            id: `00000000-0000-4000-8000-${String(offset + i + 1).padStart(12, "0")}`,
          }));
        await request.fulfill({
          status: 200, contentType: "application/json",
          headers: { "X-Total-Count": String(search ? 1 : 21) },
          body: JSON.stringify(rows.map((item) => ({ ...item, namespace: "demo", status: "draft",
            description: "Private owner draft", version: "0.1.0" }))),
        });
      });
      await page.goto(`/resources/${kind}?view=my`);
      await page.getByRole("button", { name: "Next" }).click();
      await expect(page.getByText(`My ${kind} 21`).first()).toBeVisible();
      const searchBox = page.getByRole("combobox", { name: /Search .* or filter by/ });
      await searchBox.fill("hidden");
      await expect(page).not.toHaveURL(/page=2/);
      await expect(page.getByText(`Hidden ${kind} match`).first()).toBeVisible();
      expect(searches).toContain("hidden");
    });
  }

  test("Discover hands pending and rejected Agent Files to the owner workspace", async ({ page }) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/agents/my?**", (request) => {
      const status = new URL(request.request().url()).searchParams.get("status");
      return request.fulfill({ status: 200, contentType: "application/json",
        headers: { "X-Total-Count": status ? "1" : "2" },
        body: JSON.stringify(status ? [] : [{ id: "00000000-0000-4000-8000-000000000009",
          name: "Owner draft", slug: "owner-draft", namespace: "demo", status: "pending",
          description: "Owner pending", version: "0.1.0" }]),
      });
    });
    await page.goto("/resources/agents");
    await expect(page.getByText("Your Agent File submissions · all teamspaces")).toBeVisible();
    await page.getByRole("button", { name: "Open submissions" }).click();
    await expect(page).toHaveURL(/view=my/);
    await expect(page.getByText("Owner draft").first()).toBeVisible();
  });

  test("all six kinds offer a draft-only owner view", async ({ page }) => {
    await loginToWebUI(page);
    for (const [, kind] of kinds) {
      const statuses: string[] = [];
      const route = `**/api/v1/${kind}/my?**`;
      await page.route(route, (request) => {
        statuses.push(new URL(request.request().url()).searchParams.get("status") ?? "");
        return request.fulfill({ status: 200, contentType: "application/json", headers: { "X-Total-Count": "1" },
          body: JSON.stringify([{ id: "00000000-0000-4000-8000-000000000043", name: `Saved ${kind}`,
            slug: `saved-${kind}`, namespace: "demo", status: "draft", description: "Unpublished work",
            version: "0.1.0", user_permission: "owner" }]),
        });
      });
      await page.goto(`/resources/${kind}?view=drafts`);
      await expect(page.getByText(`Saved ${kind}`).first()).toBeVisible();
      expect(statuses).toContain("draft");
      if (kind === "skills") {
        await expect(page.getByRole("note")).toContainText("approved Skill’s folder versions");
        await page.getByRole("note").getByRole("button", { name: "My submissions" }).click();
        await expect(page).toHaveURL(/view=my/);
      }
      await page.unroute(route);
    }
  });

  test("requested changes lead to feedback before editing in My submissions", async ({ page }) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/prompts/my?**", (request) => request.fulfill({
      status: 200, contentType: "application/json", headers: { "X-Total-Count": "1" },
      body: JSON.stringify([{ id: "00000000-0000-4000-8000-000000000042", name: "Needs changes",
        slug: "needs-changes", namespace: "demo", status: "changes_requested", description: "Revise this prompt",
        version: "0.1.0", user_permission: "owner" }]),
    }));
    await page.goto("/resources/prompts?view=my");
    await expect(page.getByText("Needs changes").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Resubmit" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit" })).toBeVisible();
    await page.route("**/api/v1/agents/my?**", (request) => request.fulfill({
      status: 200, contentType: "application/json", headers: { "X-Total-Count": "1" },
      body: JSON.stringify([{ id: "00000000-0000-4000-8000-000000000044", name: "Agent needs changes",
        slug: "agent-needs-changes", namespace: "demo", status: "changes_requested",
        description: "Revise behavior", version: "1.0.0", review_number: 42, user_permission: "owner" }]),
    }));
    await page.goto("/resources/agents?view=my");
    await expect(page.getByText("Agent needs changes").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Read feedback" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Resubmit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit" })).toBeVisible();
    await page.getByRole("button", { name: "Read feedback" }).click();
    await expect(page).toHaveURL(/\/review\/42$/);
  });

  test("removed team bookmark never silently widens catalogue results", async ({ page }) => {
    await loginToWebUI(page);
    const requests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/v1/mcps?")) requests.push(request.url());
    });
    await page.goto("/resources/mcps?team=never-a-member");
    await expect(page.getByText("Teamspace unavailable")).toBeVisible();
    expect(requests).toHaveLength(0);
    await page.getByRole("button", { name: "Clear team filter" }).click();
    await expect(page).not.toHaveURL(/team=/);
    await expect.poll(() => requests.length).toBeGreaterThan(0);
  });

  test("stale page bookmarks recover in one click", async ({ page }) => {
    await loginToWebUI(page);
    await page.route("**/api/v1/prompts?**", (request) => request.fulfill({
      status: 200, contentType: "application/json", headers: { "X-Total-Count": "1" },
      body: JSON.stringify(Number(new URL(request.request().url()).searchParams.get("offset")) === 0
        ? [{ id: "00000000-0000-4000-8000-000000000001", name: "Available prompt", namespace: "demo",
          slug: "available-prompt", version: "1.0.0", status: "approved", description: "Still here" }] : []),
    }));
    await page.goto("/resources/prompts?page=99999");
    await expect(page.getByText("Page 99999 is no longer available")).toBeVisible();
    await page.getByRole("button", { name: "Previous" }).click();
    await expect(page).not.toHaveURL(/page=/);
    await expect(page.getByText("Available prompt")).toBeVisible();
  });

  test("untrusted harness bookmarks do not execute unfiltered searches", async ({ page }) => {
    await loginToWebUI(page);
    const searchRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/v1/ard/search")) searchRequests.push(request.url());
    });
    await page.goto("/discover?q=review&harness=%24%28echo%20unsafe%29");
    await expect(page.getByText("Unknown harness", { exact: true })).toBeVisible();
    expect(searchRequests).toHaveLength(0);
    await page.getByRole("button", { name: "Clear harness filter" }).click();
    await expect(page).toHaveURL(/\/discover\?q=review/);
    await expect(page).not.toHaveURL(/harness=/);
  });

  test("all six native kinds remain accessible with a narrow keyboard viewport", async ({ page }) => {
    await loginToWebUI(page);
    await page.setViewportSize({ width: 320, height: 740 });
    await page.goto("/resources/agents");
    for (const [label, kind] of kinds) {
      const selector = page.getByRole("button", { name: label, exact: true });
      await selector.scrollIntoViewIfNeeded();
      await selector.focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`/resources/${kind}(?:\\?|$)`));
      await expect(selector).toHaveAttribute("aria-current", "page");
      await expect.poll(() => selector.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left >= 0 && rect.right <= window.innerWidth;
      })).toBe(true);
      if (kind === "agents") {
        await expect(page.getByRole("link", { name: "Create Agent File" })).toBeVisible();
      } else {
        const name = kind === "sandboxes" ? "Sandbox" : kind === "mcps" ? "MCP Client" : label.slice(0, -1);
        await expect(page.getByRole("button", { name: `Create ${name}`, exact: true })).toBeVisible();
      }
    }
    await page.evaluate(() => localStorage.setItem("observal-mode", "dark"));
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("button", { name: "Sandboxes", exact: true })).toHaveAttribute("aria-current", "page");
  });
});
