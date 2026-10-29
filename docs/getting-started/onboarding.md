---
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Setup guide"
---

Connect your machine to Agentic Resource Registry with two commands. It takes about two minutes and does not need Python or Docker.

## 1. Run the two commands for your OS

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.sh | bash
observal auth login --server https://internal.observal.io
```

**Windows** (in PowerShell, not Command Prompt)

```powershell
irm https://raw.githubusercontent.com/AgenticResourceRegistry/AgenticResourceRegistry/main/install.ps1 | iex
observal auth login --server https://internal.observal.io
```

The first command installs the `observal` CLI. The second connects it to the server.

## 2. Answer the login prompts

1. **Login method**: pick the one your team uses. If unsure, choose **Web sign-in** and approve the code in your browser.
2. **`Fix all warnings? (configures telemetry and installs AI skills for detected harnesses) [Y/n]`**: answer **Y** (or press Enter).

   Login installs the bundled Agentic Resource Registry skills, and the Pi extension when Pi is detected, before this prompt. Answering **Y** also applies the supported telemetry hooks for your detected coding tools. If you answer **n**, other harnesses may still need instrumentation before sessions appear. Run `observal doctor` to check, then `observal doctor patch --all-harnesses` to install missing hooks.

3. Restart any coding tool that was open during setup so it picks up the changes.

## 3. Check it worked

```bash
observal auth whoami
```

Then open [internal.observal.io](https://internal.observal.io). Your sessions appear under Traces after your next coding session.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `observal: command not found` | Open a new terminal window. On macOS/Linux, also check that `/usr/local/bin` is on your `PATH`. |
| Intel Mac: download fails | There is no standalone binary for Intel Macs yet. Run `uv tool install observal-cli` instead of the first command. |
| PowerShell refuses to run `irm ... \| iex` | Run `Set-ExecutionPolicy -Scope Process Bypass` in the same window, then retry. |
| Login can't reach the server | Check that you're on the VPN, then run `curl https://internal.observal.io/health`. |
| Answered **n** to the prompt, or added a new coding tool later | Run `observal doctor patch --all-harnesses`. |

To upgrade later, run `observal self upgrade`.
