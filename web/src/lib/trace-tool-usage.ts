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
	const preEvents = events
		.map((event, index) => ({ event, index }))
		.filter(({ event }) => eventName(event) === "hook_pretooluse");
	const preById = new Map<string, number>();
	for (const { event, index } of preEvents) {
		const id = event.attributes?.tool_use_id;
		if (id && !preById.has(id)) preById.set(id, index);
	}

	const matchedPre = new Set<number>();
	for (const [index, event] of events.entries()) {
		if (!isOutcome(eventName(event))) continue;
		const id = event.attributes?.tool_use_id;
		const explicitName = event.attributes?.tool_name;
		let preIndex = id ? preById.get(id) : undefined;
		if (preIndex === undefined) {
			// Some parsers omit the ID on results. Pair only with an earlier,
			// unmatched pre-use of the same name, or the nearest one if unnamed.
			preIndex = preEvents.findLast(
				({ event: pre, index: i }) =>
					i < index &&
					!matchedPre.has(i) &&
					(!explicitName || pre.attributes?.tool_name === explicitName),
			)?.index;
		}
		if (preIndex !== undefined) matchedPre.add(preIndex);
		const name = explicitName || (preIndex !== undefined && events[preIndex].attributes?.tool_name) || "unknown";
		counts.set(name, (counts.get(name) || 0) + 1);
	}
	for (const { event, index } of preEvents) {
		if (matchedPre.has(index)) continue;
		const name = event.attributes?.tool_name || "unknown";
		counts.set(name, (counts.get(name) || 0) + 1);
	}
	return {
		toolCalls: [...counts.values()].reduce((sum, count) => sum + count, 0),
		tools: Object.fromEntries(counts),
	};
}
