---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Skill helper"
---

Use skill components when an agent needs reusable instructions, checklists, scripts, templates, or reference docs.

## What to fill in

| Field | What it means | Example |
|-------|---------------|---------|
| Name | Registry slug for the skill | `summarize-changes` |
| Task type | Broad category for browsing | `code-review` |
| Delivery mode | `git_fetch` for repo-backed skills, `registry_direct` for inline content | `registry_direct` |
| Git URL | Repository containing `SKILL.md` | `https://github.com/acme/agent-skills` |
| Git ref | Branch, tag, or commit to read from | `main` |
| Skill path | Directory containing `SKILL.md` inside the repo | `skills/summarize-changes` |
| Skill MD content | Inline `SKILL.md` when using registry direct | see below |
| Script content | Optional helper script delivered with the skill | `scripts/validate.py` |

Use `registry_direct` for small self-contained skills. Use `git_fetch` when the skill has multiple files or should track a repository.

## Minimal SKILL.md

```markdown
---
name: summarize-changes
description: Summarizes uncommitted changes and flags risky edits. Use when the user asks what changed, wants a commit message, or wants a quick diff review.
---

## Current changes

!`git diff HEAD`

## Instructions

Summarize the diff in two or three bullets. Then list risks, missing tests, or follow-up work.
```

## Supporting files shape

```text
summarize-changes/
  SKILL.md
  references/review-rules.md
  scripts/check-risk.py
```

Reference supporting files from `SKILL.md` only when they are useful. Keep the main file short.

## CLI example

Submit a skill directly from its `SKILL.md` file:

```bash
observal registry skill submit --skill-md ./SKILL.md --git-url https://github.com/org/repo
```

## Sources

- [Claude Code skills documentation](https://code.claude.com/docs/en/skills)
