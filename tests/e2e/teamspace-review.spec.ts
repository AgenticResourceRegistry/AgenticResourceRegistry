// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { expect, Page, test } from "@playwright/test";

import { API_BASE } from "./helpers";

/**
 * The teamspace Review tab is the ONLY surface a team reviewer has.
 *
 * The admin review page is gated to global reviewer and admin roles, so a team
 * reviewer whose global role is plain "user" cannot reach it. If this tab stops
 * rendering, that role loses its entire purpose and team-private submissions
 * pile up with nobody able to clear them.
 */

const PASSWORD = "F3-Adversarial-Pw!7";
const TEAM_HANDLE = "f3acme";

type Principal = { email: string; role: string };

const OWNER: Principal = { email: "f3.alice@demo.example", role: "user" };
const TEAM_REVIEWER: Principal = { email: "f3.bob@demo.example", role: "user" };
const PLAIN_MEMBER: Principal = { email: "f3.carol@demo.example", role: "user" };
const NON_MEMBER: Principal = { email: "f3.mallory@demo.example", role: "user" };

// Cached per email: the login limiter allows only a handful of attempts per
// bucket per minute, and every test here logs in.
const _tokens = new Map<string, string>();

async function tokenFor(email: string): Promise<string> {
  const cached = _tokens.get(email);
  if (cached) return cached;
  // The limiter keys on a bearer token hash before falling back to the client
  // IP, so a distinct dummy token per principal keeps principals out of one
  // another's bucket.
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer e2e-${email}` },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  const data = await res.json();
  if (!data.access_token) throw new Error(`login failed for ${email}: ${JSON.stringify(data)}`);
  _tokens.set(email, data.access_token as string);
  return data.access_token as string;
}

async function loginAs(page: Page, principal: Principal) {
  const token = await tokenFor(principal.email);
  await page.goto("/");
  await page.evaluate(
    ([t, r]) => {
      sessionStorage.setItem("observal_access_token", t);
      sessionStorage.setItem("observal_user_role", r);
    },
    [token, principal.role],
  );
  await page.reload();
}

async function openTeamspace(page: Page) {
  await page.goto(`/teamspaces/${TEAM_HANDLE}`);
  await expect(page.getByRole("tab", { name: /members/i })).toBeVisible({ timeout: 15_000 });
}

test.describe("teamspace review tab", () => {
  test("a team owner sees the Review tab", async ({ page }) => {
    await loginAs(page, OWNER);
    await openTeamspace(page);
    await expect(page.getByRole("tab", { name: /review/i })).toBeVisible();
  });

  test("a team reviewer sees the Review tab and its pending items", async ({ page }) => {
    await loginAs(page, TEAM_REVIEWER);
    await openTeamspace(page);

    const reviewTab = page.getByRole("tab", { name: /review/i });
    await expect(reviewTab).toBeVisible();
    await reviewTab.click();

    // Either pending work with actions, or an explicit empty state. A blank
    // panel would mean the queue silently failed to load.
    const approve = page.getByRole("button", { name: /approve/i }).first();
    const empty = page.getByText(/nothing waiting|no pending|all clear/i).first();
    await expect(approve.or(empty)).toBeVisible({ timeout: 15_000 });
  });

  test("a plain team member does not see the Review tab", async ({ page }) => {
    await loginAs(page, PLAIN_MEMBER);
    await openTeamspace(page);
    await expect(page.getByRole("tab", { name: /review/i })).toHaveCount(0);
  });

  test("the Agent Files and Components tabs are reachable from the teamspace", async ({ page }) => {
    // The teamspace card used to link only to /components, leaving team-published
    // agents findable only by setting a filter on the agents page by hand.
    await loginAs(page, OWNER);
    await openTeamspace(page);
    for (const name of [/agent files/i, /components/i]) {
      const tab = page.getByRole("tab", { name });
      await expect(tab).toBeVisible();
      await tab.click();
    }
  });

  test("a non-member cannot reach the teamspace review surface", async ({ page }) => {
    await loginAs(page, NON_MEMBER);
    await page.goto(`/teamspaces/${TEAM_HANDLE}`);
    await expect(page.getByRole("tab", { name: /review/i })).toHaveCount(0);
  });
});

test.describe("visibility confirmation", () => {
  test("making a team-private listing public asks for confirmation first", async ({ page }) => {
    const token = await tokenFor(OWNER.email);

    // Seed a team-private skill this owner can flip.
    const seeded = await fetch(`${API_BASE}/api/v1/skills/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        name: `e2e-vis-${Date.now()}`,
        version: "1.0.0",
        description: "visibility dialog fixture",
        owner: TEAM_HANDLE,
        task_type: "testing",
        delivery_mode: "registry_direct",
        skill_md_content: "# fixture\n",
        team_id: await teamId(token),
        visibility: "team",
      }),
    }).then((r) => r.json());
    expect(seeded.id).toBeTruthy();

    await loginAs(page, OWNER);
    // The route is /components/$componentId with the plural type as a search param.
    await page.goto(`/components/${seeded.id}?type=skills`);

    // Visibility is a PickerSelect labelled "Listing visibility", not a button.
    const picker = page.getByLabel("Listing visibility");
    await expect(picker).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Show options" }).first().click();
    await page.getByRole("button", { name: "Public", exact: true }).click();

    // The dialog must name both consequences: everyone can see it, and it leaves
    // the catalog until a reviewer approves. Users cannot guess the second one.
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/review/i);
    await expect(dialog).toContainText(/everyone|public|anyone/i);

    // Cancelling must leave the listing team-private.
    await dialog.getByRole("button", { name: /cancel|no|keep/i }).first().click();
    await expect(dialog).toHaveCount(0);

    const after = await fetch(`${API_BASE}/api/v1/skills/${seeded.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    }).then((r) => r.json());
    expect(after.is_private).toBe(true);
  });
});

async function teamId(token: string): Promise<string> {
  const teams = await fetch(`${API_BASE}/api/v1/teams`, {
    headers: { Authorization: `Bearer ${token}` },
  }).then((r) => r.json());
  const team = teams.find((t: { handle: string }) => t.handle === TEAM_HANDLE);
  if (!team) throw new Error(`teamspace ${TEAM_HANDLE} not seeded`);
  return team.id;
}
