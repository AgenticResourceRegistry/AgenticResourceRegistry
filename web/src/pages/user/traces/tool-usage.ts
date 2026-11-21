// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-License-Identifier: Apache-2.0

interface ToolHook {
	eventName: string;
	toolName?: string;
	toolUseId?: string;
}

/** Count invocations, not hook events: pre/result hooks can describe one call. */
export function countToolUsage(events: ReadonlyArray<ToolHook>): Record<string, number> {
	const invocations: string[] = [];
	const byId = new Map<string, number>();

	for (const { eventName, toolName, toolUseId } of events) {
		if (
			eventName !== "hook_pretooluse" &&
			eventName !== "hook_posttooluse" &&
			eventName !== "hook_posttoolusefailure" &&
			eventName !== "hook_toolresult"
		) continue;
		const name = toolName || "unknown";
		if (toolUseId) {
			const index = byId.get(toolUseId);
			if (index !== undefined) {
				// A result without a tool name can still use the pre-hook's name.
				if (invocations[index] === "unknown" && toolName) invocations[index] = toolName;
			} else {
				byId.set(toolUseId, invocations.push(name) - 1);
			}
			continue;
		}

		// Without an invocation ID, a matching name cannot prove these hooks
		// belong to the same call (other calls may be interleaved).
		invocations.push(name);
	}

	// Object.fromEntries avoids special keys such as "__proto__" mutating the
	// counts object while preserving the plain-object shape expected by the UI.
	const counts = new Map<string, number>();
	for (const name of invocations) counts.set(name, (counts.get(name) ?? 0) + 1);
	return Object.fromEntries(counts);
}
