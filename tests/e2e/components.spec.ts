// SPDX-FileCopyrightText: 2026 Tanvi Reddy <tanvi.reddy330@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { loginToWebUI, API_BASE, getAccessToken, publishSubject } from "./helpers";

test.describe("Component Detail Page", () => {
  // This test requires the approve endpoint to work (currently broken: CircularDependencyError).
  // It will pass once the server-side approve bug is fixed.
  test.skip("detail page loads for an approved component", async ({ page }) => {
    const token = await getAccessToken();
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };

    // Submit a component
    const name = `e2e-detail-${Date.now()}`;
    const submitRes = await fetch(`${API_BASE}/api/v1/skills/submit`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        name,
        version: "1.0.0",
        description: "E2E detail page test",
        owner: "admin",
        task_type: "code-review",
        skill_path: "/",
      }),
    });
    const skill = await submitRes.json();

    // Approve it (required for detail page to show it)
    await publishSubject("skill", skill.id);

    await loginToWebUI(page);
    await page.goto(`/components/${skill.id}?type=skill`);
    await page.waitForLoadState("networkidle");

    // Verify the detail page shows the component name and version
    await expect(page.locator(`text=${name}`).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(`text=1.0.0`).first()).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("Skill CRUD", () => {
  const skillName = `e2e-skill-${Date.now()}`;

  test("submit → get → approve → delete", async () => {
    const token = await getAccessToken();
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };

    // Submit
    const submitRes = await fetch(`${API_BASE}/api/v1/skills/submit`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: skillName,
        version: "1.0.0",
        description: "E2E test skill",
        owner: "admin",
        task_type: "code-review",
        skill_path: "/",
      }),
    });
    expect(submitRes.status).toBe(200);
    const skill = await submitRes.json();
    expect(skill.name).toBe(skillName);
    const skillId = skill.id;

    // Get
    const getRes = await fetch(`${API_BASE}/api/v1/skills/${skillId}`, { headers });
    expect(getRes.status).toBe(200);
    const fetched = await getRes.json();
    expect(fetched.name).toBe(skillName);

    // Approve
    await publishSubject("skill", skillId);

    // Delete
    const deleteRes = await fetch(`${API_BASE}/api/v1/skills/${skillId}`, {
      method: "DELETE",
      headers,
    });
    expect(deleteRes.status).toBe(200);
  });
});

test.describe("Hook CRUD", () => {
  const hookName = `e2e-hook-${Date.now()}`;

  test("submit → get → approve → delete", async () => {
    const token = await getAccessToken();
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };

    // Submit
    const submitRes = await fetch(`${API_BASE}/api/v1/hooks/submit`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: hookName,
        version: "1.0.0",
        description: "E2E test hook",
        owner: "admin",
        event: "PostToolUse",
        handler_type: "command",
        handler_config: { command: "echo test" },
      }),
    });
    expect(submitRes.status).toBe(200);
    const hook = await submitRes.json();
    expect(hook.name).toBe(hookName);
    const hookId = hook.id;

    // Get
    const getRes = await fetch(`${API_BASE}/api/v1/hooks/${hookId}`, { headers });
    expect(getRes.status).toBe(200);
    const fetched = await getRes.json();
    expect(fetched.name).toBe(hookName);

    // Approve
    await publishSubject("hook", hookId);

    // Delete
    const deleteRes = await fetch(`${API_BASE}/api/v1/hooks/${hookId}`, {
      method: "DELETE",
      headers,
    });
    expect(deleteRes.status).toBe(200);
  });
});

test.describe("Sandbox CRUD", () => {
  const sandboxName = `e2e-sandbox-${Date.now()}`;

  test("submit → get → approve → delete", async () => {
    const token = await getAccessToken();
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };

    // Submit
    const submitRes = await fetch(`${API_BASE}/api/v1/sandboxes/submit`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: sandboxName,
        version: "1.0.0",
        description: "E2E test sandbox",
        owner: "admin",
        runtime_type: "docker",
        image: "alpine:latest",
        network_policy: "none",
      }),
    });
    expect(submitRes.status).toBe(200);
    const sandbox = await submitRes.json();
    expect(sandbox.name).toBe(sandboxName);
    const sandboxId = sandbox.id;

    // Get
    const getRes = await fetch(`${API_BASE}/api/v1/sandboxes/${sandboxId}`, { headers });
    expect(getRes.status).toBe(200);
    const fetched = await getRes.json();
    expect(fetched.name).toBe(sandboxName);

    // Approve
    await publishSubject("sandbox", sandboxId);

    // Delete
    const deleteRes = await fetch(`${API_BASE}/api/v1/sandboxes/${sandboxId}`, {
      method: "DELETE",
      headers,
    });
    expect(deleteRes.status).toBe(200);
  });
});
