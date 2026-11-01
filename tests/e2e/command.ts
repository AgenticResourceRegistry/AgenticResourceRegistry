// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { execFileSync, spawnSync } from "node:child_process";

export const REPO_ROOT = execFileSync(
  "git",
  ["rev-parse", "--show-toplevel"],
  { encoding: "utf-8" },
).trim();

type RunOptions = {
  allowFailure?: boolean;
  cwd?: string;
  env?: Record<string, string | undefined>;
  timeout?: number;
};

export function runCommand(
  command: string,
  args: string[] = [],
  options: RunOptions = {},
): string {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? REPO_ROOT,
    encoding: "utf-8",
    env: { ...process.env, ...options.env },
    shell: false,
    timeout: options.timeout ?? 30_000,
  });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  if (result.error) throw result.error;
  if (result.status !== 0 && !options.allowFailure) {
    const error = new Error(
      `${command} exited with status ${result.status}: ${output.trim()}`,
    );
    Object.assign(error, { stdout: output, stderr: result.stderr });
    throw error;
  }
  return output;
}
