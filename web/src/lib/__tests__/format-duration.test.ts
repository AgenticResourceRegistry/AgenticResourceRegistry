// SPDX-FileCopyrightText: 2026 Deepesh Kumar <akadeepesh1710@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { formatDuration } from "../format-duration";

describe("formatDuration", () => {
	it("shows whole milliseconds under one second", () => {
		expect(formatDuration(0)).toBe("0 ms");
		expect(formatDuration(42.4)).toBe("42 ms");
		expect(formatDuration(999)).toBe("999 ms");
	});

	it("shows seconds with two decimals under one minute", () => {
		expect(formatDuration(1000)).toBe("1.00 s");
		expect(formatDuration(1234)).toBe("1.23 s");
		expect(formatDuration(59_990)).toBe("59.99 s");
	});

	it("shows minutes and seconds from one minute up", () => {
		expect(formatDuration(60_000)).toBe("1m 0s");
		expect(formatDuration(125_000)).toBe("2m 5s");
		expect(formatDuration(3_600_000)).toBe("60m 0s");
	});

	it("rolls over at unit boundaries instead of showing 1000 ms or 60.00 s", () => {
		expect(formatDuration(999.6)).toBe("1.00 s");
		expect(formatDuration(59_999)).toBe("1m 0s");
	});

	it("accepts numeric strings", () => {
		expect(formatDuration("850")).toBe("850 ms");
		expect(formatDuration("1234.5")).toBe("1.23 s");
	});

	it("returns a dash for non-numeric input", () => {
		expect(formatDuration("")).toBe("—");
		expect(formatDuration("abc")).toBe("—");
		expect(formatDuration(Number.NaN)).toBe("—");
	});
});
