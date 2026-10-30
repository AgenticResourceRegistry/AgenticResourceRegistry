// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { test } from "node:test";
import { countToolUsage } from "./tool-usage.ts";

test("Codex pre/post events for one call ID count as one use", () => {
	assert.deepEqual(
		countToolUsage([
			{ eventName: "hook_pretooluse", toolName: "read", toolUseId: "call-1" },
			{ eventName: "hook_pretooluse", toolName: "read", toolUseId: "call-2" },
			{ eventName: "hook_posttooluse", toolName: "", toolUseId: "call-2" },
			{ eventName: "hook_posttooluse", toolName: "read", toolUseId: "call-1" },
		]),
		{ read: 2 },
	);
});

test("missing IDs pair pre/post hooks by tool name, retaining unpaired uses", () => {
	assert.deepEqual(
		countToolUsage([
			{ eventName: "hook_pretooluse", toolName: "read" },
			{ eventName: "hook_pretooluse", toolName: "write" },
			{ eventName: "hook_posttooluse", toolName: "read" },
			{ eventName: "hook_posttoolusefailure", toolName: "write" },
			{ eventName: "hook_posttooluse", toolName: "read" },
			{ eventName: "hook_pretooluse", toolName: "orphan" },
		]),
		{ read: 2, write: 1, orphan: 1 },
	);
});

test("PostToolUse-only sessions count each call once, including unnamed tools", () => {
	const tools = countToolUsage([
		...Array.from({ length: 3 }, () => ({ eventName: "hook_posttooluse", toolName: "read" })),
		{ eventName: "hook_posttooluse", toolName: "write" },
		{ eventName: "hook_posttooluse" },
	]);
	assert.deepEqual(tools, { read: 3, write: 1, unknown: 1 });
	assert.equal(Object.values(tools).reduce((total, count) => total + count, 0), 5);
});

test("other tool results count once; non-tool hooks and OTEL events do not", () => {
	assert.deepEqual(
		countToolUsage([
			{ eventName: "hook_pretooluse", toolName: "read", toolUseId: "a" },
			{ eventName: "hook_toolresult", toolName: "read", toolUseId: "a" },
			{ eventName: "hook_userpromptsubmit", toolName: "user_prompt" },
			{ eventName: "tool_result", toolName: "read" },
			{ eventName: "hook_pretooluse" },
		]),
		{ read: 1, unknown: 1 },
	);
});

test("tool names cannot corrupt the count dictionary", () => {
	assert.deepEqual(
		countToolUsage([{ eventName: "hook_posttooluse", toolName: "__proto__" }]),
		Object.fromEntries([["__proto__", 1]]),
	);
});
