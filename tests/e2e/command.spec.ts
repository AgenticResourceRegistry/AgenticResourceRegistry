// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { test, expect } from "@playwright/test";
import { existsSync, rmSync } from "node:fs";
import { runCommand } from "./command";

test("runCommand passes metacharacters as literal arguments", () => {
  const marker = `/tmp/observal-command-injection-${process.pid}`;
  const argument = `; touch ${marker}`;
  rmSync(marker, { force: true });

  const output = runCommand(process.execPath, [
    "-e",
    "process.stdout.write(process.argv.at(-1))",
    argument,
  ]);

  expect(output).toBe(argument);
  expect(existsSync(marker)).toBe(false);
});
