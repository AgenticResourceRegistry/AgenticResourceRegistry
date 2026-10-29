<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# AI Policy

Agentic Resource Registry welcomes the use of AI coding tools. They can meaningfully accelerate development and help contributors tackle complex changes. At the same time, we have zero tolerance for **slop**: unreviewed, low-effort output that wastes reviewer time and degrades the codebase.

This policy tells you what is expected when AI tools are part of your workflow.

> [!NOTE]
> This policy was informed by the [AnkiDroid AI Policy](https://github.com/ankidroid/Anki-Android/blob/main/AI_POLICY.md), adapted with a more permissive stance that reflects the nature of an AI-native project. Attribution is given with thanks.

---

## Fully autonomous contributions without human authorship are not permitted

**Unattended agents that independently make material implementation choices and submit code without meaningful human direction, review, and accountability are not allowed to contribute to this project.**

Interactive coding agents and assistants, including Claude Code, Pi, Cursor, Copilot, and similar tools, are allowed when an accountable human directs the work, reviews the complete change, can explain it, and explicitly authorizes publication. The distinction is meaningful human authorship and accountability, not whether the tool can edit files, run commands, commit, push, or open a pull request.

This is not a quality judgement, it is a legal one.

The [US Copyright Office's January 2025 report on AI copyrightability](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf) explains that material without sufficient human-authored expressive elements may not qualify for copyright protection. Copyrightability is fact-specific, and prompting alone does not necessarily establish human authorship.

For this project, unattended submissions create three structural risks:

1. **The CLA may be unsupported.** Our CLA requires the contributor to assert that the contribution is their original creation and that they have the right to license it. An unattended submission provides no accountable human who can make and support that assertion.

2. **The license chain becomes uncertain.** Apache-2.0 depends on contributors having rights they can grant. Insufficient human authorship can leave the project unable to establish a reliable licensing chain for the contribution.

3. **Training data provenance is unknowable.** Autonomous agents may reproduce verbatim or substantially similar GPL-licensed code from their training data without attribution, a risk confirmed by active litigation (_Doe 1 v. GitHub, Inc._, N.D. Cal. 2022) and studied by the [Software Freedom Conservancy](https://sfconservancy.org/blog/2022/feb/03/github-copilot-copyleft-gpl/) and [FSF](https://www.fsf.org/news/publication-of-the-fsf-funded-white-papers-on-questions-around-copilot).

This is the same position taken by [curl](https://curl.se/dev/contribute.html) and documented by the FSF and SFC. **Any PR produced and submitted by an unattended agent without an accountable human author will be closed immediately.**

> [!NOTE]
> Using an interactive AI coding tool under human direction is explicitly welcome. A human may ask the tool to edit code, run checks, prepare commits, push a branch, draft pull request content, or publish an approved pull request. The human must review the result, make or approve the material choices, accept responsibility for the contribution, and satisfy the CLA.

---

## What is allowed

- Using AI tools (Copilot, Cursor, Claude Code, Pi, etc.) to write, refactor, review, or test code, provided you direct the work and review and own the result
- Using AI tools to help understand the codebase, generate test cases, draft documentation, or prepare pull request content
- Authorizing an interactive coding agent to commit, push, create or update a pull request, or perform other repository actions after you have reviewed the relevant changes and content
- Submitting AI-assisted contributions, provided all requirements below are met

---

## Requirements for AI-assisted contributions

### You must be able to explain every line

If a reviewer asks you to explain a change, you must be able to do so clearly and accurately. "The AI wrote it" is not an acceptable answer. If you cannot explain it, do not submit it.

### It must compile and the tests must pass

Run `make test` and verify CI passes before opening a PR. Do not submit code you have not executed locally.

### Read through the full diff yourself

Before opening a PR, read every line of your diff. AI tools make confident-looking mistakes. You are responsible for catching them.

### Frontend changes require screenshots

If your PR touches the web frontend, attach screenshots of all affected screens to the PR body. This is required regardless of whether the change was AI-assisted.

### Label AI use and include the tool version

If AI tools made a nontrivial contribution to your PR, state so in the PR description. Include the tool name and version (for example: `Claude Sonnet 4`, `GPT-4o`, `Cursor 0.48`). A nontrivial contribution means the AI wrote, restructured, or significantly modified the code, not just autocomplete suggestions.

---

## What is not allowed

- Unattended coding agents choosing work, implementing it, and submitting PRs without meaningful human direction and review
- Publishing AI-generated GitHub comments, review replies, or PR template content without human review and explicit approval
- Submitting output you have not read and understood
- Claiming human authorship when no accountable human made or approved the material creative choices
- Repeating the same AI-generated mistakes across multiple PRs after being told about them

> [!WARNING]
> PRs that show clear signs of unreviewed AI output, boilerplate that does not match the codebase, incorrect variable names, placeholder text, hallucinated API calls, will be **closed without review**. A second instance may result in a contribution ban.
