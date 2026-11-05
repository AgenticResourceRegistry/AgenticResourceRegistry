// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

const agentId = "d196727f-733d-476a-bd16-7a0de7b91340";
const componentId = "c196727f-733d-476a-bd16-7a0de7b91340";

// An approved release may still be shown on the overview while Edit must amend
// the pending version, keeping its pin overrides when saving other fields.
test("amending a pending agent preserves its component config override", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "edit-test-token");
    localStorage.setItem("observal_user_role", "user");
  });
  const agent = {
    id: agentId, name: "Review helper", version: "1.1.0", status: "pending", user_permission: "owner",
    description: "Pending description", prompt: "Review carefully", model_name: "auto",
    created_by: "author", owner: "author", supported_harnesses: ["pi"],
    component_links: [{ component_type: "mcp", component_id: componentId, component_name: "GitHub",
      version_ref: "1.2.3", config_override: { env: { MODE: "safe" } }, order: 0 }],
  };
  const versions = [
    { version: "1.0.0", status: "approved", description: "Approved description", component_count: 1 },
    { version: "1.1.0", status: "pending", description: "Pending description", component_count: 1 },
  ];
  const saved: unknown[] = [];
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], total: 0, components: [] } }));
  await page.route(`**/api/v1/feedback/agent/${agentId}`, route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/teams**", route => route.fulfill({ json: [] }));
  await page.route(/\/api\/v1\/(mcps|skills|hooks|prompts|sandboxes)(\?.*)?$/, route => route.fulfill({ json: [] }));
  await page.route(`**/api/v1/reviews/${agentId}`, route => route.fulfill({ status: 404, json: { detail: "No open review" } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id: "author", role: "user", username: "author" } }));
  await page.route(`**/api/v1/agents/${agentId}**`, route => {
    const url = new URL(route.request().url());
    const suffix = url.pathname.replace(`/api/v1/agents/${agentId}`, "");
    if (suffix === "/versions" && route.request().method() === "GET") return route.fulfill({ json: { items: versions, total: 2 } });
    if (suffix === "/version-suggestions") return route.fulfill({ json: { suggestions: { patch: "1.1.1", minor: "1.2.0", major: "2.0.0" } } });
    if (suffix === "/versions/1.0.0" || suffix === "/versions/1.1.0") {
      const pending = suffix.endsWith("1.1.0");
      return route.fulfill({ json: {
        ...agent, version: pending ? "1.1.0" : "1.0.0",
        description: pending ? "Pending description" : "Approved description",
        components: [{ component_type: "mcp", component_id: componentId, name: "GitHub",
          config_override: pending ? { env: { MODE: "safe" } } : { env: { MODE: "old" } } }],
      } });
    }
    if (suffix === "/draft" && route.request().method() === "PUT") {
      saved.push(route.request().postDataJSON());
      return route.fulfill({ json: { ...agent, review_revision: 2 } });
    }
    if (suffix === "/submit" && route.request().method() === "POST") return route.fulfill({ json: agent });
    if (suffix === "") return route.fulfill({ json: agent });
    return route.fulfill({ json: { items: [], total: 0, components: [] } });
  });

  await page.goto(`/agents/${agentId}`);
  await page.getByRole("tab", { name: "Edit" }).click();
  const description = page.getByLabel("Description", { exact: true });
  await expect(description).toHaveValue("Pending description");
  await description.fill("Corrected pending release");
  await page.getByRole("button", { name: "Save & update review" }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0]).toMatchObject({
    description: "Corrected pending release",
    components: [{ component_type: "mcp", component_id: componentId, config_override: { env: { MODE: "safe" } } }],
  });
});


test("edit tab shows a retryable error instead of a form when the version fails to load", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "edit-test-token");
    localStorage.setItem("observal_user_role", "user");
  });
  const agent = {
    id: agentId, name: "Review helper", version: "1.1.0", status: "pending", user_permission: "owner",
    description: "Pending description", prompt: "Review carefully", model_name: "auto",
    created_by: "author", owner: "author", supported_harnesses: ["pi"], component_links: [],
  };
  let versionRequests = 0;
  const saves: unknown[] = [];
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], total: 0, components: [] } }));
  await page.route(`**/api/v1/feedback/agent/${agentId}`, route => route.fulfill({ json: [] }));
  await page.route("**/api/v1/teams**", route => route.fulfill({ json: [] }));
  await page.route(/\/api\/v1\/(mcps|skills|hooks|prompts|sandboxes)(\?.*)?$/, route => route.fulfill({ json: [] }));
  await page.route(`**/api/v1/reviews/${agentId}`, route => route.fulfill({ status: 404, json: { detail: "No open review" } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id: "author", role: "user", username: "author" } }));
  await page.route(`**/api/v1/agents/${agentId}**`, route => {
    const suffix = new URL(route.request().url()).pathname.replace(`/api/v1/agents/${agentId}`, "");
    if (suffix === "/versions") return route.fulfill({ json: { items: [{ version: "1.1.0", status: "pending" }], total: 1 } });
    if (suffix === "/version-suggestions") return route.fulfill({ json: { suggestions: { patch: "1.1.1", minor: "1.2.0", major: "2.0.0" } } });
    if (suffix === "/versions/1.1.0") {
      versionRequests += 1;
      return route.fulfill({ status: 500, json: { detail: "Version store unavailable" } });
    }
    if (suffix === "/draft") { saves.push(route.request().postDataJSON()); return route.fulfill({ json: agent }); }
    if (suffix === "") return route.fulfill({ json: agent });
    return route.fulfill({ json: { items: [], total: 0, components: [] } });
  });

  await page.goto(`/agents/${agentId}`);
  await page.getByRole("tab", { name: "Edit" }).click();
  const retry = page.getByRole("button", { name: /try again|retry/i });
  await expect(retry).toBeVisible({ timeout: 15_000 });
  await expect(page.getByLabel("Description", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save & update review" })).toHaveCount(0);
  const before = versionRequests;
  await retry.click();
  await expect.poll(() => versionRequests).toBeGreaterThan(before);
  expect(saves).toHaveLength(0);
});
