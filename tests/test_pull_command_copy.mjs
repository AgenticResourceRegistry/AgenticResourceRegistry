// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const root = fileURLToPath(new URL("../", import.meta.url));
const surfaces = [
	"web/src/pages/registry/home.tsx",
	"web/src/pages/registry/teamspace-detail.tsx",
	"web/src/pages/registry/teamspaces.tsx",
	"web/src/components/builder/preview-panel.tsx",
];

test("registry install instructions use the CLI agent pull subcommand", () => {
	for (const path of surfaces) {
		const content = readFileSync(new URL(path, `file://${root}`), "utf8");
		assert.match(content, /observal agent pull/, `${path}: missing valid CLI command`);
		assert.doesNotMatch(content, /observal pull/, `${path}: obsolete CLI command`);
	}
});

test("README skill invocation stays distinct from CLI usage", () => {
	const readme = readFileSync(new URL("README.md", `file://${root}`), "utf8");
	assert.match(readme, /\/observal pull security-auditor/);
});
