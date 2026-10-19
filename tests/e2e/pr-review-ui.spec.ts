// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from "@playwright/test";

const id = "e2d6c522-969d-4725-81ab-ed87c17af09c";
const now = new Date().toISOString();
const summary = (number: number, title: string, subject_type: "agent" | "skill", depends_on: number[] = []) => ({
  id: `${number}0000000-0000-4000-8000-000000000000`, number, title, subject_type, subject_id: id,
  version: "1.0.0", team_id: null, state: "open", author_id: id, author_name: "harish", head_revision: 1, updated_at: now,
  gate: { ready: false, approvals: 0, required: 2, policy_source: "organization", requirements: [] },
  threads: { total: 1, unresolved: 1 }, checks: { pass: 2, fail: 0, warn: 1, skipped: 0 },
  depends_on, requested_from_me: true, my_last_submission: null,
});

test("review queue shows pinned dependencies in the Observal shell", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "reviewer");
  });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0, by_kind: {}, by_subject_type: {} } }));
  await page.route("**/api/v1/config/public", route => route.fulfill({ json: { experimental_review_checks: true } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id, username: "reviewer", name: "Review Tester", role: "reviewer" } }));
  const queueRequests: string[] = [];
  await page.route("**/api/v1/reviews?*", route => { queueRequests.push(route.request().url()); return route.fulfill({ json: {
    items: [summary(42, "Agent: repository assistant", "agent", [43]), summary(43, "Skill: verify source", "skill")],
    total: 2, limit: 100, offset: 0,
  } }); });
  await page.goto("/review");
  await expect(page.locator("h1")).toHaveClass(/sr-only/);
  await expect(page.getByText("Review and publish registry changes.")).toHaveCount(0);
  await expect(page.getByText("Agent: repository assistant")).toBeVisible();
  await expect(page.getByText("Skill: verify source")).toBeVisible();
  await expect(page.getByText("Waits on #43")).toBeVisible();
  await expect(page.getByText("Checks have warnings", { exact: true }).filter({ visible: true })).toHaveCount(2);
  await expect(page.locator("[aria-label='1 unresolved conversations']:visible")).toHaveCount(2);
  await expect(page.locator("[aria-label='1 unresolved conversations']:visible + span[aria-hidden='true']")).toHaveCount(2);
  await expect(page.getByTestId("review-list").locator(".lucide-shield-check")).toHaveCount(0);
  const allOpen = page.getByRole("button", { name: "All open" });
  await expect(allOpen).toHaveAttribute("aria-pressed", "true");
  expect(await allOpen.evaluate(button => button.parentElement?.querySelector("button") === button)).toBe(true);
  expect(queueRequests.some(url => new URL(url).searchParams.get("type") === "agent" && new URL(url).searchParams.has("needs"))).toBe(false);
  await expect(page.getByRole("button", { name: "Needs my review" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Search reviews" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New review" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("pr-review-queue-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 2077, height: 900 });
  const list = await page.getByTestId("review-list").boundingBox();
  expect(list).not.toBeNull();
  expect(list!.x + list!.width).toBeGreaterThan(2030);
  await page.screenshot({ path: testInfo.outputPath("pr-review-queue-wide.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText("Skill: verify source")).toBeVisible();
  await expect(page.getByText("Checks have warnings", { exact: true }).filter({ visible: true })).toHaveCount(2);
  await expect(page.locator("[aria-label='1 unresolved conversations']:visible")).toHaveCount(2);
  await expect(page.locator("[aria-label='1 unresolved conversations']:visible + span[aria-hidden='true']")).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("pr-review-queue-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Needs my review" }).click();
  await expect.poll(() => queueRequests.some(url => url.includes("needs=my_review"))).toBe(true);
  await page.getByRole("button", { name: "Ready to publish" }).click();
  await expect.poll(() => queueRequests.some(url => url.includes("needs=ready_to_publish"))).toBe(true);
  await page.getByRole("textbox", { name: "Search reviews" }).fill("verify source");
  await expect.poll(() => queueRequests.some(url => url.includes("q=verify+source"))).toBe(true);
});

for (const checksEnabled of [true, false]) test(`review queue ${checksEnabled ? "distinguishes failures, warnings, skipped, and absent checks" : "hides checks while the experiment is off"}`, async ({ page }) => {
  const cases = [
    { title: "Failed checks", checks: { pass: 2, fail: 1, warn: 1, skipped: 0 }, label: "Checks failing" },
    { title: "Warnings", checks: { pass: 2, fail: 0, warn: 1, skipped: 0 }, label: "Checks have warnings" },
    { title: "Partially skipped", checks: { pass: 2, fail: 0, warn: 0, skipped: 1 }, label: "Checks passed · 1 skipped" },
    { title: "All skipped", checks: { pass: 0, fail: 0, warn: 0, skipped: 2 }, label: "Checks skipped" },
    { title: "All passed", checks: { pass: 2, fail: 0, warn: 0, skipped: 0 }, label: "Checks passed" },
    { title: "No checks", checks: { pass: 0, fail: 0, warn: 0, skipped: 0 }, label: "No checks recorded" },
  ];
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "reviewer");
  });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0 } }));
  await page.route("**/api/v1/config/public", route => route.fulfill({ json: { experimental_review_checks: checksEnabled } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id, username: "reviewer", role: "reviewer" } }));
  await page.route("**/api/v1/reviews?*", route => route.fulfill({ json: {
    items: new URL(route.request().url()).searchParams.get("type") === "component" ? []
      : cases.map((item, index) => ({ ...summary(index + 51, item.title, "agent"), checks: item.checks })),
    next_cursor: null,
  } }));
  await page.goto("/review");
  if (!checksEnabled) {
    // Experimental feature off: nothing about checks appears in the queue.
    for (const item of cases) await expect(page.getByRole("link", { name: new RegExp(item.title) })).toBeVisible();
    await expect(page.getByText(/^Checks /)).toHaveCount(0);
    await expect(page.getByText("No checks recorded")).toHaveCount(0);
    return;
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const item of cases) {
      const row = page.getByRole("link", { name: new RegExp(item.title) });
      await expect(row.getByText(item.label, { exact: true }).filter({ visible: true })).toHaveCount(1);
    }
  }
});

test("ordinary authors can open the review queue and paginate", async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "user");
  });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0, by_kind: {} } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id, username: "author", name: "Author", role: "user" } }));
  await page.route("**/api/v1/reviews?*", route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("type") === "component") return route.fulfill({ json: { items: [], next_cursor: null } });
    return route.fulfill({ json: url.searchParams.has("cursor")
      ? { items: [summary(40, "Second page agent", "agent")], next_cursor: null }
      : { items: [summary(42, "My pending agent", "agent")], next_cursor: 41 } });
  });
  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
  await expect(page.getByText("My pending agent")).toBeVisible();
  await expect(page.getByRole("button", { name: "Teamspaces" })).toHaveCount(0);
  await page.getByRole("button", { name: "Load more reviews" }).click();
  await expect(page.getByText("Second page agent")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("author-review-queue.png"), fullPage: true });
});

test("review detail exposes the gate, revision diff, and a separate verdict", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "reviewer");
  });
  const detail = {
    ...summary(42, "Skill: verify source", "skill"), body: "Check the **permission boundary** before publishing.",
    opened_at: now, closed_at: null, closed_reason: null, published_at: null, published_by: null,
    base_version_id: null, head_revision_id: id, my_subscription: null, self_approval_allowed: false,
    revisions: [{ id, number: 1, message: "Initial release", created_by: id, created_at: now, pruned: false }],
    requested_reviewers: [id], reviewer_names: { [id]: "harish" },
  };
  const diff = [{ path: "SKILL.md", status: "added", lang: "markdown", pinned: false, generated: false,
    additions: 1, deletions: 0, too_large: false,
    hunks: [{ base_start: 0, base_lines: 0, head_start: 1, head_lines: 1, lines: [{ t: "add", h: 1, s: "# Verify source" }] }],
  }];
  const verdicts: unknown[] = [];
  const reviewerRequests: unknown[] = [];
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0 } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id: "other-reviewer", username: "reviewer", name: "Review Tester", role: "reviewer" } }));
  await page.route("**/api/v1/reviews/42**", route => {
    const url = new URL(route.request().url());
    const suffix = url.pathname.replace("/api/v1/reviews/42", "");
    if (suffix === "/reviewers" && route.request().method() === "POST") {
      reviewerRequests.push(route.request().postDataJSON());
      return route.fulfill({ status: 201, json: { requested: true } });
    }
    if (suffix === "/submissions" && route.request().method() === "POST") {
      verdicts.push(route.request().postDataJSON());
      return route.fulfill({ json: { state: "approved" } });
    }
    const value = suffix === "/gate" ? detail.gate : suffix === "/timeline" ? { items: [
      { id, kind: "opened", actor_id: id, actor_name: "harish", created_at: now },
      { id: "10000000-0000-4000-8000-000000000001", kind: "verdict", actor_id: id, actor_name: "harish", created_at: now, verdict: "comment", body: "Please verify permissions.", state: "submitted" },
      { id: "10000000-0000-4000-8000-000000000002", kind: "comment", actor_id: id, actor_name: "harish", created_at: now, payload: { thread_id: "20000000-0000-4000-8000-000000000001" } },
    ], next_cursor: null }
      : suffix === "/threads" ? [{ id: "20000000-0000-4000-8000-000000000001", path: "SKILL.md", side: "head", start_line: 1, end_line: 1, outdated: false, resolved_at: null, revision_id: id, comments: [{ id, thread_id: "20000000-0000-4000-8000-000000000001", author_id: id, body: "The command needs a permission check.", suggestion: null, created_at: now, pending: false }] }]
      : suffix === "/checks" ? [] : suffix === "/diff" || suffix === "/files" ? diff : detail;
    return route.fulfill({ json: value });
  });
  await page.goto("/review/42");
  await expect(page.getByRole("heading", { name: "Skill: verify source" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Publication gate" })).toBeVisible();
  await expect(page.getByText("Please verify permissions.")).toBeVisible();
  await expect(page.getByText("The command needs a permission check.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("pr-review-conversation-desktop.png"), fullPage: true });
  await page.getByRole("textbox", { name: "Reviewer user ID" }).fill(id);
  await page.getByRole("button", { name: "Request reviewer" }).click();
  await expect.poll(() => reviewerRequests.length).toBe(1);
  expect(reviewerRequests[0]).toEqual({ user_id: id });
  await page.getByRole("button", { name: "Files changed" }).click();
  await expect(page.getByRole("table", { name: "Diff for SKILL.md" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("pr-review-detail-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("table", { name: "Diff for SKILL.md" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("pr-review-detail-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("navigation", { name: "Review sections" }).getByRole("button", { name: "Conversation" }).click();
  await expect(page.getByRole("heading", { name: "Publication gate" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("pr-review-conversation-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Review changes" }).click();
  await page.getByRole("radio", { name: "Approve" }).check();
  await page.getByRole("button", { name: "Submit review" }).click();
  await expect.poll(() => verdicts.length).toBe(1);
  expect(verdicts[0]).toEqual({ verdict: "approve", body: "" });
});

for (const override of [false, true]) test(`publish confirmation ${override ? "explains an override" : "shows advisory items"} without a browser prompt`, async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "super_admin");
  });
  const gate = {
    ready: !override, approvals: override ? 0 : 1, required: 1, policy_source: "organization",
    requirements: override ? ["required_approvals", "unresolved_threads"] : ["unresolved_threads"],
  };
  const detail = {
    ...summary(42, "Skill: verify source", "skill"), state: "approved", gate,
    body: "", opened_at: now, closed_at: null, closed_reason: null, published_at: null, published_by: null,
    base_version_id: null, head_revision_id: id, my_subscription: null, self_approval_allowed: false,
    revisions: [{ id, number: 1, message: "First release", created_by: id, created_at: now, pruned: false }],
    requested_reviewers: [], reviewer_names: {},
    threads: { total: 1, unresolved: 1 },
    checks: { pass: 0, fail: 0, warn: 0, skipped: 0, queued: 0, running: 0, error: 0 },
  };
  const published: unknown[] = [];
  page.on("dialog", () => { throw new Error("Publish must not open a native browser dialog"); });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0 } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id: "admin", username: "admin", role: "super_admin" } }));
  await page.route("**/api/v1/reviews/42**", route => {
    const suffix = new URL(route.request().url()).pathname.replace("/api/v1/reviews/42", "");
    if (suffix === "/publish") {
      published.push(route.request().postDataJSON());
      return route.fulfill({ json: { state: "published" } });
    }
    return route.fulfill({ json: suffix === "/gate" ? gate : suffix === "/timeline" ? { items: [], next_cursor: null }
      : suffix === "/threads" || suffix === "/files" ? [] : detail });
  });
  await page.goto("/review/42");
  // A super admin facing a blocked gate sees only the override action; the reason is asked in the dialog.
  if (override) {
    await expect(page.getByRole("button", { name: "Publish v1.0.0" })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Override reason" })).toHaveCount(0);
  }
  await expect(page.getByRole("textbox", { name: "Agent File category" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Close reason" })).toHaveCount(0);
  await page.getByRole("button", { name: override ? "Publish with override" : "Publish v1.0.0" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Publish v1.0.0?" })).toBeVisible();
  await expect(dialog.getByText("Conversations are unresolved")).toBeVisible();
  if (override) {
    await expect(dialog.getByText("0 of 1 required approvals (1 more needed)")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Confirm override" })).toBeDisabled();
    await dialog.getByRole("textbox", { name: "Reason (required)" }).fill("Urgent release");
    await expect(dialog.getByText(/Confidentiality restrictions still apply/)).toBeVisible();
  }
  await page.screenshot({ path: testInfo.outputPath(`publish-confirm-${override ? "override" : "standard"}-desktop.png`) });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  expect(published).toHaveLength(0);
  if (override) await page.getByRole("button", { name: "Publish with override" }).click();
  else await page.getByRole("button", { name: "Publish v1.0.0" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog).toBeVisible();
  if (override) await dialog.getByRole("textbox", { name: "Reason (required)" }).fill("Urgent release");
  await expect.poll(async () => {
    const box = await dialog.boundingBox();
    return !!box && box.x >= 0 && box.x + box.width <= 390;
  }).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`publish-confirm-${override ? "override" : "standard"}-mobile.png`) });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByRole("button", { name: override ? "Confirm override" : "Publish v1.0.0" }).click();
  await expect.poll(() => published.length).toBe(1);
  expect(published[0]).toEqual(override ? { override_reason: "Urgent release" } : {});
  await expect(dialog).toHaveCount(0);
});

test("close review asks for a reason in a dialog", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("observal_access_token", "review-ui-test");
    localStorage.setItem("observal_user_role", "reviewer");
  });
  const gate = { ready: false, approvals: 0, required: 1, policy_source: "organization", requirements: ["required_approvals"] };
  const detail = {
    ...summary(42, "Skill: verify source", "skill"), gate,
    body: "", opened_at: now, closed_at: null, closed_reason: null, published_at: null, published_by: null,
    base_version_id: null, head_revision_id: id, my_subscription: null, self_approval_allowed: false,
    revisions: [{ id, number: 1, message: "First release", created_by: id, created_at: now, pruned: false }],
    requested_reviewers: [], reviewer_names: {},
    checks: { pass: 0, fail: 0, warn: 0, skipped: 0, queued: 0, running: 0, error: 0 },
  };
  const closed: unknown[] = [];
  await page.route("**/api/v1/**", route => route.fulfill({ json: { items: [], unread: 0, action_required: 0 } }));
  await page.route("**/api/v1/auth/whoami", route => route.fulfill({ json: { id: "reviewer", username: "reviewer", role: "reviewer" } }));
  await page.route("**/api/v1/reviews/42**", route => {
    const suffix = new URL(route.request().url()).pathname.replace("/api/v1/reviews/42", "");
    if (suffix === "/close") { closed.push(route.request().postDataJSON()); return route.fulfill({ json: { state: "closed" } }); }
    return route.fulfill({ json: suffix === "/gate" ? gate : suffix === "/timeline" ? { items: [], next_cursor: null }
      : suffix === "/threads" || suffix === "/files" ? [] : detail });
  });
  await page.goto("/review/42");
  await expect(page.getByRole("textbox", { name: "Close reason" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close review" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("heading", { name: "Close review #42?" })).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Close review" });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("textbox", { name: "Reason (required)" }).fill("Duplicate of #41");
  await confirm.click();
  await expect.poll(() => closed.length).toBe(1);
  expect(closed[0]).toEqual({ reason: "Duplicate of #41" });
  await expect(dialog).toHaveCount(0);
});
