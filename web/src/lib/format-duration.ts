// SPDX-FileCopyrightText: 2026 Deepesh Kumar <akadeepesh1710@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Format a duration in milliseconds for display.
 *
 *   < 1 s   -> "850 ms"
 *   < 60 s  -> "1.23 s"
 *   longer  -> "2m 5s"
 *
 * Accepts a number or a numeric string (span attributes arrive as strings).
 * Returns "—" for values that are not finite numbers.
 */
export function formatDuration(ms: number | string): string {
	const n = typeof ms === "string" ? parseFloat(ms) : ms;
	if (!Number.isFinite(n)) return "—";

	const rounded = Math.round(n);
	if (Math.abs(rounded) < 1000) return `${rounded} ms`;

	const seconds = n / 1000;
	// Compare the rounded value so 59 999 ms shows as "1m 0s", not "60.00 s".
	if (Math.abs(Number(seconds.toFixed(2))) < 60) return `${seconds.toFixed(2)} s`;

	const sign = n < 0 ? "-" : "";
	const totalSeconds = Math.round(Math.abs(seconds));
	const minutes = Math.floor(totalSeconds / 60);
	const rest = totalSeconds % 60;
	return `${sign}${minutes}m ${rest}s`;
}
