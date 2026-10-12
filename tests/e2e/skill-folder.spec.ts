// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Browser and HTTP acceptance for the strict skill-folder draft contract. */
import { test, expect } from "@playwright/test";
import { getAccessToken, API_BASE, loginToWebUI } from "./helpers";
import { randomUUID } from "node:crypto";

function skillName(prefix: string) {
	return `${prefix}-${randomUUID().slice(0, 8)}`;
}
function skillMd(name: string) {
	return `---\nname: ${name}\ndescription: Skill folder acceptance example\n---\n\n# Instructions\nRead the linked resources.\n`;
}
async function headers() {
	return { "Content-Type": "application/json", Authorization: `Bearer ${await getAccessToken()}` };
}
async function createDraft(name: string, extra_files: Array<{ path: string; content: string; encoding?: "base64"; executable?: boolean }>) {
	const auth = await headers();
	const response = await fetch(`${API_BASE}/api/v1/skills/folder-drafts`, {
		method: "POST", headers: auth,
		body: JSON.stringify({ name, version: "1.0.0", description: "Skill folder acceptance example",
			owner: "admin", task_type: "general", skill_md_content: skillMd(name), extra_files }),
	});
	return { response, auth };
}
// No DELETE /skills/{id} exists. These uniquely named drafts may persist,
// so this suite must only run on a disposable test stack.
test.beforeEach(() => {
	test.skip(process.env.OBSERVAL_ISOLATED_STACK !== "true", "Creates drafts; use an isolated disposable test stack");
});

test("API preserves executable, empty and binary files in a versioned draft", async () => {
	const name = skillName("e2e-folder");
	const { response, auth } = await createDraft(name, [
		{ path: "scripts/run.sh", content: "echo ok\n", executable: true },
		{ path: "templates/empty.txt", content: "" },
		{ path: "assets/icon.bin", content: Buffer.from([0, 255]).toString("base64"), encoding: "base64" },
	]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	expect(draft.listing_id).toBeTruthy();
	expect(draft.version_id).toBeTruthy();
	{
		const manifestRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/manifest`, { headers: auth });
		expect(manifestRes.status).toBe(200);
		const manifest = await manifestRes.json();
		expect(manifest.version_id).toBe(draft.version_id);
		expect(manifest.files.map((file: {path: string}) => file.path)).toEqual(
			expect.arrayContaining(["SKILL.md", "scripts/run.sh", "templates/empty.txt", "assets/icon.bin"])
		);
		expect(manifest.files.find((file: {path: string}) => file.path === "scripts/run.sh").mode).toBe("0755");
		expect(manifest.files.find((file: {path: string}) => file.path === "templates/empty.txt").size).toBe(0);
		const textRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/files/scripts/run.sh`, { headers: auth });
		expect(textRes.status).toBe(200);
		expect((await textRes.json()).content).toBe("echo ok\n");
		const binaryRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/files/assets/icon.bin`, { headers: auth });
		expect(binaryRes.headers.get("content-type")).toContain("application/octet-stream");
		expect(Buffer.from(await binaryRes.arrayBuffer())).toEqual(Buffer.from([0, 255]));
	}
});

test("browser creates a complete draft without submitting it for review", async ({ page }) => {
	const name = skillName("e2e-browser-folder");
	await loginToWebUI(page);
	await page.goto("/components");
	await page.getByRole("button", { name: "Skills", exact: true }).click();
	await page.getByRole("button", { name: "Create", exact: true }).click();
	const dialog = page.getByRole("dialog");
	await dialog.getByRole("tab", { name: "Upload" }).click();
	await expect(dialog.locator("#comp-version")).toHaveValue("1.0.0");
	if (process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true") {
		await expect(dialog.getByRole("status").filter({ hasText: "Complete-folder review and installation are not enabled" })).toBeVisible();
	}
	await expect(dialog.getByRole("button", { name: "Choose Folder" })).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Save Draft", exact: true })).toHaveCount(0);
	await expect(dialog.getByRole("button", { name: "Save folder draft" })).toBeDisabled();
	await expect(dialog.getByRole("status").filter({ hasText: "Name is required" })).toBeVisible();
	await dialog.locator("#skill-file-upload").setInputFiles([
		{ name: "SKILL.md", mimeType: "text/markdown", buffer: Buffer.from(skillMd(name)) },
		{ name: "run.sh", mimeType: "text/plain", buffer: Buffer.from("echo browser\n") },
		{ name: "icon.bin", mimeType: "application/octet-stream", buffer: Buffer.from([0, 255]) },
	]);
	await expect(dialog.locator("#comp-name")).toHaveValue(name);
	await expect(dialog.getByRole("button", { name: "Replace Whole Folder" })).toBeVisible();
	const responsePromise = page.waitForResponse((response) => response.url().endsWith("/api/v1/skills/folder-drafts") && response.request().method() === "POST");
	await dialog.getByRole("button", { name: "Save folder draft" }).click();
	const response = await responsePromise;
	expect(response.status(), await response.text()).toBe(200);
	const draft = await response.json();
	{
		expect(draft.files.map((file: {path: string}) => file.path)).toEqual(
			expect.arrayContaining(["SKILL.md", "run.sh", "icon.bin"])
		);
		expect(draft.files.find((file: {path: string}) => file.path === "icon.bin").size).toBe(2);
	}
});

test("owner draft detail shows its files and routes Edit to the exact folder editor", async ({ page }) => {
	const name = skillName("e2e-draft-detail");
	const { response } = await createDraft(name, [
		{ path: "scripts/run.sh", content: "echo ok\n", executable: true },
		{ path: "templates/summary.md", content: "## Summary\n" },
	]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	await loginToWebUI(page);
	await page.goto(`/components/${draft.listing_id}?type=skills`);
	await expect(page.getByRole("heading", { name: "Instructions" })).toBeVisible();
	if (process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED === "true") {
		await expect(page.getByRole("button", { name: "Submit for review" })).toBeEnabled();
	} else {
		await expect(page.getByRole("status")).toContainText("Complete-folder review is not enabled");
		await expect(page.getByRole("button", { name: "Submit for review" })).toBeDisabled();
	}
	await expect(page.getByRole("tab", { name: "Reviews" })).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Archive", exact: true })).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Mark as recommended" })).toHaveCount(0);
	const files = page.getByRole("region", { name: "Saved skill draft files" });
	await expect(files.getByRole("button", { name: "Select SKILL.md" })).toBeVisible();
	await expect(files.getByRole("button", { name: "Select scripts/run.sh" })).toBeVisible();
	await expect(files.getByRole("button", { name: "Select templates/summary.md" })).toBeVisible();
	await files.getByRole("button", { name: "Select scripts/run.sh" }).click();
	await expect(files.getByText("Executable", { exact: true })).toBeVisible();
	await expect(files.getByText("echo ok")).toBeVisible();
	await page.setViewportSize({ width: 2400, height: 1000 });
	await expect.poll(async () => (await page.locator(".page-body").boundingBox())?.width).toBeLessThanOrEqual(1441);
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/draft-detail-files.png`, fullPage: true, animations: "disabled" });
	}
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(files.getByRole("button", { name: "Select templates/summary.md" })).toBeVisible();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/draft-detail-mobile.png`, fullPage: true, animations: "disabled" });
	}
	await page.getByRole("tab", { name: "Edit", exact: true }).click();
	await expect(page.getByRole("link", { name: "Edit draft v1.0.0" })).toBeVisible();
	await expect(page.getByRole("tab", { name: "Upload (unavailable)" })).toHaveCount(0);
	await page.getByRole("link", { name: "Edit draft v1.0.0" }).click();
	await expect(page.getByRole("dialog").getByText("templates/summary.md")).toBeVisible();
});

test("browser resumes and saves the exact existing folder version", async ({ page }) => {
	const name = skillName("e2e-resume-folder");
	const { response, auth } = await createDraft(name, [
		{ path: "templates/note.txt", content: "before\n" },
		{ path: "assets/icon.bin", content: Buffer.from([0, 255]).toString("base64"), encoding: "base64" },
	]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	await loginToWebUI(page);
	await page.goto("/components?type=skills");
	await page.getByRole("button", { name: /My submissions/ }).first().click();
	const row = page.locator("section .divide-y > div").filter({ hasText: name });
	await expect(row).toBeVisible();
	await row.getByRole("button", { name: "Edit" }).click();
	const choice = page.getByRole("dialog");
	await expect(choice.getByText("Choose the exact folder version")).toBeVisible();
	await choice.getByRole("button", { name: "Edit selected version" }).click();
	const editor = page.getByRole("dialog");
	await expect(editor.getByRole("tab", { name: "Upload" })).toHaveAttribute("data-state", "active");
	await expect(editor.getByText("assets/icon.bin")).toBeVisible();
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await editor.getByText("assets/icon.bin").scrollIntoViewIfNeeded();
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/folder-draft-editor.png`, fullPage: true, animations: "disabled" });
	}
	await editor.getByText("templates/note.txt").click();
	await editor.getByPlaceholder("File content...").fill("after\n");
	const save = page.waitForResponse((r) => r.url().includes(`/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/draft`) && r.request().method() === "PUT");
	await editor.getByRole("button", { name: "Save folder changes" }).click();
	expect((await save).status()).toBe(200);
	const versionsRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions`, { headers: auth });
	const versions = await versionsRes.json();
	expect(versions.total).toBe(1);
	expect(versions.items[0].id).toBe(draft.version_id);
	const textRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/files/templates/note.txt`, { headers: auth });
	expect((await textRes.json()).content).toBe("after\n");
	const binaryRes = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/files/assets/icon.bin`, { headers: auth });
	expect(Buffer.from(await binaryRes.arrayBuffer())).toEqual(Buffer.from([0, 255]));
	await row.getByRole("button", { name: "Submit", exact: true }).click();
	await expect(page.getByRole("dialog").getByText("Choose the exact folder version")).toBeVisible();
	if (process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED === "true") {
		await expect(page.getByRole("dialog").getByRole("button", { name: "Submit selected version" })).toBeEnabled();
	} else {
		await expect(page.getByRole("dialog").getByRole("status")).toContainText("Complete-folder review is not enabled");
		await expect(page.getByRole("dialog").getByRole("button", { name: "Submit selected version" })).toBeDisabled();
	}
	const current = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions`, { headers: auth });
	expect((await current.json()).items[0].status).toBe("draft");
});

test("owner submits the exact saved draft directly from its detail page", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const name = skillName("e2e-direct-submit");
	const { response } = await createDraft(name, [{ path: "templates/summary.md", content: "## Summary\n" }]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	await loginToWebUI(page);
	await page.goto(`/components/${draft.listing_id}?type=skills`);
	const submit = page.getByRole("button", { name: "Submit for review" });
	await expect(submit).toBeEnabled();
	const sent = page.waitForResponse((result) => result.url().endsWith(`/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/submit`));
	await submit.click();
	expect((await sent).status()).toBe(200);
	await expect(page.getByRole("region", { name: "Submitted skill files" })).toBeVisible();
	await expect(submit).toHaveCount(0);
});

test("owner submits the selected folder draft for review in the browser", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const name = skillName("e2e-submit-folder");
	const { response } = await createDraft(name, [{ path: "templates/summary.md", content: "## Summary\n" }]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	await loginToWebUI(page);
	await page.goto("/components?type=skills");
	await page.getByRole("button", { name: /My submissions/ }).first().click();
	const row = page.locator("section .divide-y > div").filter({ hasText: name });
	await expect(row).toBeVisible();
	await row.getByRole("button", { name: "Submit", exact: true }).click();
	const choice = page.getByRole("dialog", { name: "Choose the exact folder version" });
	await expect(choice.getByRole("combobox")).toHaveValue(new RegExp(draft.version_id.slice(0, 8)));
	await expect(choice.getByRole("button", { name: "Submit selected version" })).toBeEnabled();
	const sent = page.waitForResponse((result) => result.url().endsWith(`/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/submit`));
	await choice.getByRole("button", { name: "Submit selected version" }).click();
	expect((await sent).status()).toBe(200);
	await page.goto(`/components/${draft.listing_id}?type=skills`);
	await expect(page.getByRole("region", { name: "Submitted skill files" }).getByRole("button", { name: "Select templates/summary.md" })).toBeVisible();
});

test("owner withdraws a pending folder for another edit without losing its files", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const name = skillName("e2e-withdraw-folder");
	const { response, auth } = await createDraft(name, [
		{ path: "scripts/run.sh", content: "echo retained\n", executable: true },
	]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	const submit = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/submit`, {
		method: "POST", headers: auth, body: JSON.stringify({ observed_revision: draft.revision }),
	});
	expect(submit.status, await submit.text()).toBe(200);
	await loginToWebUI(page);
	await page.goto(`/components/${draft.listing_id}?type=skills`);
	await expect(page.getByRole("region", { name: "Submitted skill files" }).getByRole("button", { name: "Select scripts/run.sh" })).toBeVisible();
	await page.getByRole("tab", { name: /Versions/ }).click();
	await page.getByRole("button", { name: "Withdraw to draft" }).click();
	await expect(page.getByRole("link", { name: "Edit exact draft" })).toBeVisible();
	await page.getByRole("tab", { name: "Overview" }).click();
	await expect(page.getByRole("region", { name: "Saved skill draft files" }).getByRole("button", { name: "Select scripts/run.sh" })).toBeVisible();
});

test("gated reviewer and installer compare exact candidate with reviewed base", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Run only on isolated temporary gate-on stack");
	const listingId = process.env.OBSERVAL_GATED_LISTING_ID!;
	const versionId = process.env.OBSERVAL_GATED_VERSION_ID!;
	await loginToWebUI(page);
	await page.goto("/review?tab=components");
	await page.getByRole("tab", { name: /components/i }).click();
	await page.locator(`[data-review-item="skill:${versionId}"]`).click();
	await page.getByRole("button", { name: "View full diff" }).click();
	const sheet = page.getByRole("dialog");
	await expect(sheet.getByText("Compared to base: 2 added, 1 modified, 1 removed.")).toBeVisible();
	await sheet.getByRole("button", { name: "templates", exact: true }).click();
	await sheet.getByRole("button", { name: /note.txt/ }).click();
	await expect(sheet.getByText("after", { exact: true })).toBeVisible();
	await expect(sheet.getByText("before", { exact: true })).toBeVisible();
	await expect(sheet.getByRole("button", { name: "templates/removed.txt Removed" })).toBeVisible();
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/folder-review-diff.png`, fullPage: true });
	}
	const review = await fetch(`${API_BASE}/api/v1/review/skills/${listingId}/versions/${versionId}`, { headers: await headers() });
	expect(review.status).toBe(200);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/components/${listingId}?type=skills`);
	const install = page.locator("aside");
	await expect(install.getByText("Install", { exact: true })).toBeVisible();
	const panelBounds = await install.locator("div.border.rounded-md").first().boundingBox();
	for (const label of ["Skill release selection", "Skill installation scope", "Harness"]) {
		const input = install.getByRole("combobox", { name: label });
		const bounds = await input.boundingBox();
		expect(bounds && panelBounds && bounds.x >= panelBounds.x && bounds.x + bounds.width <= panelBounds.x + panelBounds.width,
			`${label} must fit within the Install panel`).toBeTruthy();
	}
	const harnessResponse = await fetch(`${API_BASE}/api/v1/config/harnesses`);
	const harnessList = await harnessResponse.json();
	const skillHarnesses = harnessList.harnesses.filter((harness: {capabilities: string[]}) => harness.capabilities.includes("skills"));
	await install.getByRole("combobox", { name: "Harness" }).click();
	await expect(page.getByRole("option")).toHaveCount(skillHarnesses.length);
	await page.getByRole("option").first().click();
	const copy = install.getByRole("button", { name: "Copy command" });
	await copy.scrollIntoViewIfNeeded();
	await expect(copy).toBeVisible();
	await expect(install.locator("code")).toContainText("observal registry skill install");
	await expect(install.locator("code")).toHaveCSS("white-space", "nowrap");
	await expect(install.locator("code")).toContainText(/--version \d+\.\d+\.\d+.*--scope user/);
	await install.getByRole("combobox", { name: "Skill installation scope" }).click();
	await page.getByRole("option", { name: "Project" }).click();
	await expect(install.locator("code")).toContainText("--scope project");
	await expect(install.getByText(/Run from the project root/)).toBeVisible();
	await install.getByRole("combobox", { name: "Skill release selection" }).click();
	await page.getByRole("option", { name: "Follow latest" }).click();
	await expect(install.locator("code")).not.toContainText("--version");
	await expect(install.getByText(/may install a newer release/)).toBeVisible();
	await expect(page.getByRole("region", { name: "Reviewed skill files" })).toBeVisible();
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/skill-install-mobile.png`, fullPage: true, animations: "disabled" });
	}
});

test("approved skill owner can inspect pending successor files without confusing them with the reviewed base", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	await loginToWebUI(page);
	await page.goto(`/components/${process.env.OBSERVAL_GATED_LISTING_ID}?type=skills`);
	await expect(page.getByText("Your separate submission v1.1.0 (not part of the selected approved release)")).toBeVisible();
	await expect(page.getByRole("region", { name: "Reviewed skill files" })).toBeVisible();
	await expect(page.getByRole("region", { name: "Submitted skill files" })).toBeVisible();
});

test("owner can submit one saved draft while a different successor is pending", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const listingId = process.env.OBSERVAL_GATED_LISTING_ID!;
	const auth = { ...(await headers()), "Content-Type": "application/json" };
	const baseResponse = await fetch(`${API_BASE}/api/v1/skills/${listingId}/approved-base`, { headers: auth });
	expect(baseResponse.status).toBe(200);
	const base = await baseResponse.json();
	const version = "1.1.1";
	const saved = await fetch(`${API_BASE}/api/v1/skills/${listingId}/drafts`, { method: "POST", headers: auth,
		body: JSON.stringify({ base_version_id: base.version_id, observed_base_revision: base.revision,
			version, description: "Independent saved successor" }) });
	expect(saved.status).toBe(200);
	await loginToWebUI(page);
	await page.goto(`/components/${listingId}?type=skills`);
	await expect(page.getByText("Your separate submission v1.1.0 (not part of the selected approved release)")).toBeVisible();
	await expect(page.getByText(`Draft v${version}`, { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Submit for review", exact: true })).toBeEnabled();
});

test("review approval stays blocked when an exact candidate file preview fails", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const listingId = process.env.OBSERVAL_GATED_LISTING_ID!;
	const versionId = process.env.OBSERVAL_GATED_VERSION_ID!;
	await page.route(`**/api/v1/skills/${listingId}/versions/${versionId}/files/SKILL.md`, (route) =>
		route.fulfill({ status: 503, body: "Preview unavailable" }));
	await loginToWebUI(page);
	await page.goto("/review?tab=components");
	await page.getByRole("tab", { name: /components/i }).click();
	await page.locator(`[data-review-item="skill:${versionId}"]`).click();
	await page.getByRole("button", { name: "View full diff" }).click();
	const sheet = page.getByRole("dialog");
	await sheet.getByRole("button", { name: "SKILL.md" }).click();
	await expect(sheet.getByText("File preview unavailable")).toBeVisible();
	await expect(sheet.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
});

test("review approval blocks same-size tampered text even when its preview returns HTTP 200", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const listingId = process.env.OBSERVAL_GATED_LISTING_ID!;
	const versionId = process.env.OBSERVAL_GATED_VERSION_ID!;
	let tampered = false;
	await page.route(`**/api/v1/skills/${listingId}/versions/${versionId}/files/SKILL.md`, async (route) => {
		const response = await route.fetch();
		const file = await response.json();
		if (response.status() !== 200 || !file.content.startsWith("---")) throw new Error("Expected the candidate SKILL.md response");
		tampered = true;
		await route.fulfill({ response, json: { ...file, content: `+${file.content.slice(1)}` } });
	});
	await loginToWebUI(page);
	await page.goto("/review?tab=components");
	await page.getByRole("tab", { name: /components/i }).click();
	await page.locator(`[data-review-item="skill:${versionId}"]`).click();
	await page.getByRole("button", { name: "View full diff" }).click();
	const sheet = page.getByRole("dialog");
	await sheet.getByRole("button", { name: "SKILL.md" }).click();
	await expect(sheet.getByText("File checksum differs from its reviewed manifest.")).toBeVisible();
	expect(tampered).toBe(true);
	await expect(sheet.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
});

test("review approval blocks a binary whose downloaded bytes disagree with the exact manifest", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const listingId = process.env.OBSERVAL_GATED_LISTING_ID!;
	const versionId = process.env.OBSERVAL_GATED_VERSION_ID!;
	await page.route(new RegExp(`/api/v1/skills/${listingId}/versions/${versionId}/files/.*icon\\.bin`), (route) =>
		route.fulfill({ status: 200, contentType: "application/octet-stream", body: Buffer.from([0, 1]) }));
	await loginToWebUI(page);
	await page.goto("/review?tab=components");
	await page.getByRole("tab", { name: /components/i }).click();
	await page.locator(`[data-review-item="skill:${versionId}"]`).click();
	await page.getByRole("button", { name: "View full diff" }).click();
	const sheet = page.getByRole("dialog");
	await sheet.getByRole("button", { name: "assets", exact: true }).click();
	await sheet.getByRole("button", { name: /icon.bin/ }).click();
	await expect(sheet.getByText("File checksum differs from its reviewed manifest.")).toBeVisible();
	await expect(sheet.getByRole("link", { name: /Download candidate binary/ })).toHaveCount(0);
	await expect(sheet.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
});

test("Agent review explains blocked linked components instead of reporting missing skill files", async ({ page }) => {
	test.skip(process.env.OBSERVAL_ISOLATED_STACK !== "true", "Creates an Agent only on an isolated stack");
	const auth = { ...(await headers()), "Content-Type": "application/json" };
	const response = await fetch(`${API_BASE}/api/v1/agents`, { method: "POST", headers: auth,
		body: JSON.stringify({ name: `blocked-agent-review-${Date.now()}`, version: "1.0.0", owner: "admin",
			description: "Isolated reviewer tooltip test", model_name: "claude-sonnet-4", prompt: "Review the test input.", components: [] }) });
	expect(response.status).toBe(200);
	const agent = await response.json();
	try {
		await page.route("**/api/v1/review?tab=agents", async (route) => {
			const original = await route.fetch();
			const items = await original.json();
			if (!items.some((item: { id: string }) => item.id === agent.id)) throw new Error("Test Agent not present in review queue");
			await route.fulfill({ response: original, json: items.map((item: { id: string }) =>
				item.id === agent.id ? { ...item, components_ready: false } : item) });
		});
		await loginToWebUI(page);
		await page.goto("/review?tab=agents");
		await page.locator(`[data-review-item="${agent.id}"]`).click();
		await page.getByRole("button", { name: "View full diff" }).click();
		const approve = page.getByRole("dialog").getByRole("button", { name: "Approve", exact: true });
		await expect(approve).toBeDisabled();
		await approve.hover({ force: true });
		await expect(page.getByRole("tooltip").getByText("Approve the pending linked components before approving this item.")).toBeVisible();
	} finally {
		await fetch(`${API_BASE}/api/v1/agents/${agent.id}`, { method: "DELETE", headers: auth });
	}
});

test("review queue approves a folder by exact reviewed version instead of legacy listing route", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED !== "true", "Only run on isolated temporary gate-on stack");
	const name = `review-route-audit-${Date.now()}`;
	const auth = { ...(await headers()), "Content-Type": "application/json" };
	const draft = await fetch(`${API_BASE}/api/v1/skills/folder-drafts`, { method: "POST", headers: auth,
		body: JSON.stringify({ name, owner: "admin", version: "1.0.0", task_type: "general", description: "Exact review route test",
			skill_md_content: `---\nname: ${name}\ndescription: Test\n---\n\n# Test\n`,
			extra_files: [{ path: "templates/note.txt", content: "review these bytes" }] }) });
	expect(draft.status).toBe(200);
	const created = await draft.json();
	const listingId = created.listing_id;
	const versionId = created.version_id;
	const submit = await fetch(`${API_BASE}/api/v1/skills/${listingId}/versions/${versionId}/submit`, { method: "POST",
		headers: auth, body: JSON.stringify({ observed_revision: created.revision }) });
	expect(submit.status).toBe(200);
	await loginToWebUI(page);
	await page.goto("/review?tab=components");
	await page.getByRole("tab", { name: /components/i }).click();
	await page.locator(`[data-review-item="skill:${versionId}"]`).click();
	await page.getByRole("button", { name: "View full diff" }).click();
	const sheet = page.getByRole("dialog");
	await expect(sheet.getByText("Exact reviewed files")).toBeVisible();
	await expect(sheet.getByRole("button", { name: "SKILL.md" })).toBeVisible();
	const approved = page.waitForResponse((r) => r.url().endsWith(`/api/v1/review/skills/${listingId}/versions/${versionId}/decision`) && r.request().method() === "POST");
	await sheet.getByRole("button", { name: "Approve", exact: true }).click();
	expect((await approved).status()).toBe(200);
	const version = await fetch(`${API_BASE}/api/v1/skills/${listingId}/versions/1.0.0`, { headers: await headers() });
	expect(version.status).toBe(200);
	expect((await version.json()).status).toBe("approved");
});

test("owner forks an exact approved folder and resumes the same version-bound web editor", async ({ page }) => {
	test.skip(!process.env.OBSERVAL_APPROVED_SKILL_LISTING_ID, "Provide an existing approved isolated-stack listing");
	const listingId = process.env.OBSERVAL_APPROVED_SKILL_LISTING_ID!;
	await loginToWebUI(page);
	await page.goto(`/components/${listingId}?type=skills`);
	await page.getByRole("button", { name: "Create next version" }).click();
	await expect(page.getByText("Reviewed base", { exact: false })).toBeVisible();
	const version = `99999.0.${Date.now()}`;
	await page.getByPlaceholder("1.1.0").fill(version);
	await page.getByRole("textbox", { name: "Description" }).fill("Browser successor authoring test");
	await page.getByRole("button", { name: "Create new folder version" }).click();
	await expect(page.getByRole("tab", { name: "Upload", exact: true })).toBeVisible({ timeout: 20_000 });
	await expect(page.getByRole("textbox", { name: "Version" })).toHaveValue(version);
	const url = page.url();
	await page.reload();
	await expect(page).toHaveURL(url);
	await expect(page.getByRole("tab", { name: "Upload", exact: true })).toBeVisible({ timeout: 20_000 });
	await page.goto(`/components/${listingId}?type=skills`);
	await expect(page.getByText(`Your separate draft v${version} (not part of the selected approved release)`)).toBeVisible();
	await expect(page.getByRole("region", { name: "Reviewed skill files" })).toBeVisible();
	await expect(page.getByRole("region", { name: "Saved skill draft files" })).toBeVisible();
	await page.getByRole("tab", { name: /Versions/ }).click();
	await expect(page.getByRole("button", { name: `Submit v${version} for review` })).toBeVisible();
	await expect(page.getByRole("button", { name: "Submit v1.2.0 for review" })).toBeVisible();
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/successor-folder-editor.png`, fullPage: true, animations: "disabled" });
	}
});

test("approved exact version exposes inert reviewed files without a misleading gate-off install command", async ({ page }) => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED === "true", "Gate-off status only");
	test.skip(!process.env.OBSERVAL_APPROVED_SKILL_LISTING_ID, "Provide an existing approved isolated-stack listing");
	const listingId = process.env.OBSERVAL_APPROVED_SKILL_LISTING_ID!;
	await loginToWebUI(page);
	await page.goto(`/components/${listingId}?type=skills`);
	const files = page.getByRole("region", { name: "Reviewed skill files" });
	await expect(page.getByRole("tab", { name: "Reviews" })).toBeVisible();
	if (process.env.OBSERVAL_APPROVED_DRAFT_VERSION_ID) {
		await expect(page.getByRole("link", { name: "Edit saved draft v1.2.0" })).toBeVisible();
	}
	await expect(files.getByText("Files in this release")).toBeVisible();
	await expect(files.getByRole("button", { name: /Select SKILL.md/ })).toBeVisible();
	await files.getByRole("button", { name: /Select SKILL.md/ }).click();
	await expect(files.locator("pre")).toContainText("name:");
	const install = page.locator("aside");
	await expect(install.getByRole("status")).toHaveText("Complete-folder installs are not available on this server yet.");
	await expect(install.getByRole("button", { name: "Copy command" })).toHaveCount(0);
	await expect(install.getByText("The command installs exactly the approved release shown.")).toHaveCount(0);
	await page.getByRole("tab", { name: "Edit", exact: true }).click();
	await expect(page.getByRole("tabpanel", { name: "Edit" }).getByRole("button", { name: "Create next version" })).toBeVisible();
	if (process.env.OBSERVAL_APPROVED_DRAFT_VERSION_ID) {
		await expect(page.getByRole("tabpanel", { name: "Edit" }).getByRole("link", { name: "Edit saved draft v1.2.0" })).toHaveAttribute("href", new RegExp(`folderVersionId=${process.env.OBSERVAL_APPROVED_DRAFT_VERSION_ID}`));
	}
	await expect(page.getByRole("tab", { name: "Upload (unavailable)" })).toHaveCount(0);
	await page.getByRole("tab", { name: "Overview" }).click();
	if (process.env.OBSERVAL_1730_SCREENSHOT_DIR) {
		await page.screenshot({ path: `${process.env.OBSERVAL_1730_SCREENSHOT_DIR}/approved-skill-consumer.png`, fullPage: true, animations: "disabled" });
	}
});

test("resource-bearing draft cannot be submitted while delivery gate remains off", async () => {
	test.skip(process.env.OBSERVAL_SKILL_FOLDER_DELIVERY_ENABLED === "true", "Use the gated reviewer flow on the isolated enabled stack");
	const { response, auth } = await createDraft(skillName("e2e-gated-review"), [{ path: "scripts/run.sh", content: "echo gated" }]);
	expect(response.status).toBe(200);
	const draft = await response.json();
	{
		const submit = await fetch(`${API_BASE}/api/v1/skills/${draft.listing_id}/versions/${draft.version_id}/submit`, {
			method: "POST", headers: auth, body: JSON.stringify({ observed_revision: draft.revision }),
		});
		expect(submit.status).toBe(409);
	}
});

test("accepts a representative 60-file skill with two scripts and five templates", async () => {
	const extra = [
		...Array.from({ length: 2 }, (_, index) => ({ path: `scripts/run-${index}.sh`, content: `#!/bin/sh\necho ${index}\n`, executable: true })),
		...Array.from({ length: 5 }, (_, index) => ({ path: `templates/template-${index}.txt`, content: `template ${index}\n` })),
		...Array.from({ length: 53 }, (_, index) => ({ path: `assets/file-${index}.txt`, content: `asset ${index}\n` })),
	];
	const { response } = await createDraft(skillName("e2e-capacity-60"), extra);
	if (response.status !== 200) throw new Error(`Representative upload failed (${response.status}): ${await response.text()}`);
	const manifest = await response.json();
	expect(manifest.files).toHaveLength(61);
	expect(manifest.files.filter((file: {mode: string}) => file.mode === "0755")).toHaveLength(2);
});

test("near-4-MiB binary folder survives the isolated load balancer and API without truncation", async () => {
	const binary = Buffer.alloc(2 * 1024 * 1024 - 100, 0xaf);
	const name = skillName("e2e-capacity-binary");
	const { response } = await createDraft(name, [
		{ path: "assets/large-a.bin", content: binary.toString("base64"), encoding: "base64" },
		{ path: "assets/large-b.bin", content: binary.toString("base64"), encoding: "base64" },
	]);
	if (response.status !== 200) throw new Error(`Binary upload failed (${response.status}): ${await response.text()}`);
	const manifest = await response.json();
	expect(manifest.files.filter((file: {path: string}) => file.path.endsWith(".bin")).map((file: {size: number}) => file.size))
		.toEqual([binary.length, binary.length]);
});

test("refuses invalid base64 rather than silently accepting partial bytes", async () => {
	const name = skillName("e2e-invalid-base64");
	const { response } = await createDraft(name, [{ path: "scripts/run.sh", content: "not base64!", encoding: "base64" }]);
	expect([400, 422]).toContain(response.status);
});

test("refuses traversal and excessive file counts", async () => {
	const traversal = await createDraft(skillName("e2e-traversal"), [{ path: "../outside.txt", content: "bad" }]);
	expect([400, 422]).toContain(traversal.response.status);
	const tooMany = await createDraft(skillName("e2e-file-count"),
		Array.from({ length: 129 }, (_, n) => ({ path: `templates/${n}.txt`, content: "" })));
	expect([400, 422]).toContain(tooMany.response.status);
});
