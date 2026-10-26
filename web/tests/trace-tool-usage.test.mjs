// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { test } from "node:test";
import { summarizeToolCalls } from "../src/lib/trace-tool-usage.ts";

const event = (event_name, tool_name, attributes = {}) => ({
	timestamp: "2026-10-26T08:00:00Z",
	event_name,
	attributes: { tool_name, ...attributes },
});

test("trace tool badges sum to completed tool calls, not twice as many", () => {
	const completed = [
		...Array.from({ length: 3 }, () => event("hook_posttooluse", "get-salary-percentiles")),
		event("hook_posttooluse", "Bash"),
		event("hook_posttooluse", "ToolSearch"),
	];
	const lifecycle = [
		event("hook_pretooluse", "Bash"),
		event("hook_userpromptsubmit", "user_prompt"),
	];
	const stats = summarizeToolCalls([...lifecycle, ...completed]);
	assert.equal(stats.toolCalls, 5);
	assert.deepEqual(stats.tools, {
		"get-salary-percentiles": 3,
		Bash: 1,
		ToolSearch: 1,
	});
	assert.equal(Object.values(stats.tools).reduce((sum, count) => sum + count, 0), stats.toolCalls);
});

test("uses normalized event names and shows unknown when no tool name is present", () => {
	assert.deepEqual(
		summarizeToolCalls([
		 event("ignored", "Bash", { "event.name": "hook_posttooluse" }),
		 event("hook_posttooluse", ""),
		 event("hook_pretooluse", "Edit"),
		]),
		{ toolCalls: 3, tools: { Bash: 1, unknown: 1, Edit: 1 } },
	);
});

test("Copilot pretooluse-only and orphaned tool results retain tool badges", () => {
	assert.deepEqual(
		summarizeToolCalls([event("hook_pretooluse", "Bash"), event("hook_pretooluse", "Bash")]),
		{ toolCalls: 2, tools: { Bash: 2 } },
	);
	assert.deepEqual(
		summarizeToolCalls([event("hook_toolresult", "Edit")]),
		{ toolCalls: 1, tools: { Edit: 1 } },
	);
});

test("mixed Copilot results do not suppress unrelated completed pretooluse calls", () => {
	assert.deepEqual(
		summarizeToolCalls([
			event("hook_pretooluse", "Bash", { tool_use_id: "call-1", tool_response: "done" }),
			event("hook_toolresult", "Edit", { tool_use_id: "orphan-2" }),
		]),
		{ toolCalls: 2, tools: { Edit: 1, Bash: 1 } },
	);
});

test("Codex outputs inherit tool names from their matching pre-use call", () => {
	assert.deepEqual(
		summarizeToolCalls([
			event("hook_pretooluse", "apply_patch", { tool_use_id: "call-1" }),
			event("hook_posttooluse", "", { tool_use_id: "call-1" }),
			event("hook_pretooluse", "exec_command", { tool_use_id: "call-2" }),
			event("hook_posttooluse", "", { tool_use_id: "call-2" }),
		]),
		{ toolCalls: 2, tools: { apply_patch: 1, exec_command: 1 } },
	);
});

test("idless Copilot tool results pair with the preceding named call", () => {
	assert.deepEqual(
		summarizeToolCalls([
			event("hook_pretooluse", "read_file", { tool_use_id: "agent-1" }),
			event("hook_toolresult", ""),
		]),
		{ toolCalls: 1, tools: { read_file: 1 } },
	);
});

test("special tool names remain numeric and do not change the counter prototype", () => {
	const stats = summarizeToolCalls([
		event("hook_posttooluse", "constructor"),
		event("hook_posttooluse", "__proto__"),
	]);
	assert.equal(stats.toolCalls, 2);
	assert.deepEqual(Object.entries(stats.tools), [["constructor", 1], ["__proto__", 1]]);
});

test("failed tool outcomes count once, independently of their pre-use hook", () => {
	assert.deepEqual(
		summarizeToolCalls([
			event("hook_pretooluse", "Edit"),
			event("hook_posttoolusefailure", "Edit"),
			event("hook_posttooluse", "Bash"),
		]),
		{ toolCalls: 2, tools: { Edit: 1, Bash: 1 } },
	);
});
