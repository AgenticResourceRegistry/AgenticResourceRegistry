// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { RawSessionEvent } from "./types/sessions";

/** Count tool invocations once even when both pre-use and outcome hooks exist. */
export function summarizeToolCalls(events: RawSessionEvent[]) {
	const counts = new Map<string, number>();
	const eventName = (event: RawSessionEvent) =>
		event.attributes?.["event.name"] || event.event_name;
	const isOutcome = (name: string) =>
		name === "hook_posttooluse" ||
		name === "hook_posttoolusefailure" ||
		name === "hook_toolresult";

	// Active pre-use calls live in three stacks. Each index is popped at most
	// once per stack, even when an ID match invalidates an entry in another.
	const allPre: number[] = [];
	const preByName = new Map<string, number[]>();
	const preById = new Map<string, number[]>();
	const matchedPre = new Set<number>();
	const takeLatest = (stack?: number[]) => {
		while (stack?.length) {
			const index = stack.pop()!;
			if (!matchedPre.has(index)) return index;
		}
		return undefined;
	};

	for (const [index, event] of events.entries()) {
		const name = eventName(event);
		if (name === "hook_pretooluse") {
			allPre.push(index);
			const toolName = event.attributes?.tool_name;
			if (toolName) {
				if (!preByName.has(toolName)) preByName.set(toolName, []);
				preByName.get(toolName)!.push(index);
			}
			const id = event.attributes?.tool_use_id;
			if (id) {
				if (!preById.has(id)) preById.set(id, []);
				preById.get(id)!.push(index);
			}
			continue;
		}
		if (!isOutcome(name)) continue;

		const id = event.attributes?.tool_use_id;
		const explicitName = event.attributes?.tool_name;
		// A present ID is authoritative: never attach an outcome to a pre-use
		// with another ID, even if its tool name matches.
		const preIndex = id
			? takeLatest(preById.get(id))
			: takeLatest(explicitName ? preByName.get(explicitName) : allPre);
		if (preIndex !== undefined) matchedPre.add(preIndex);
		const toolName = explicitName || (preIndex !== undefined && events[preIndex].attributes?.tool_name) || "unknown";
		counts.set(toolName, (counts.get(toolName) || 0) + 1);
	}
	for (const [index, event] of events.entries()) {
		if (eventName(event) !== "hook_pretooluse" || matchedPre.has(index)) continue;
		const name = event.attributes?.tool_name || "unknown";
		counts.set(name, (counts.get(name) || 0) + 1);
	}
	return {
		toolCalls: [...counts.values()].reduce((sum, count) => sum + count, 0),
		tools: Object.fromEntries(counts),
	};
}
