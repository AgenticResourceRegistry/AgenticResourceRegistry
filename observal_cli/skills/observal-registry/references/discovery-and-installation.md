<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Discovery and installation

## Contents

- Search and inspect
- Personalized recommendations
- Install components
- Verification

## Search and inspect

For "is there something that does X?" start with cross-kind discovery, which ranks agents, MCP servers, skills, hooks, prompts, and sandboxes together and tells you whether each result can be used right now:

```bash
observal discover search 'review a pull request for authentication bugs' --output json
observal discover inspect urn:air:... --output json
observal discover use urn:air:... --output json
```

Skills and prompts load into the current session; other kinds return the install command below as `next_step`. The `observal` skill's Discovery reference covers the fields in detail.

For kind-specific browsing and filters, use the registry list commands. Start broad with natural-language search, then narrow only when needed.

```bash
observal registry mcp list --search 'github docker' --output json
observal registry mcp list --category developer-tools --output json
observal registry skill list --search 'frontend design' --harness claude-code --output json
observal registry skill list --team platform-tools --output json
observal registry hook list --event UserPromptSubmit --output json
observal registry prompt list --category code-generation --output json
observal registry sandbox list --runtime docker --output json
observal registry models --harness kiro --output json
```

Summarize matches by `qualified_name`, description, version, supported harnesses, and why they match the request. If no result appears, retry once with fewer keywords.

Inspect a selected component with its canonical identity:

```bash
observal registry mcp show NAMESPACE/SLUG --output json
observal registry skill show NAMESPACE/SLUG --output json
observal registry hook show NAMESPACE/SLUG --output json
observal registry prompt show NAMESPACE/SLUG --output json
observal registry sandbox show NAMESPACE/SLUG --output json
```

## Personalized recommendations

Use recommendations for open-ended requests such as "what should I install?" or "what am I missing?"

```bash
observal registry recommend --output json
observal registry recommend --limit 12 --type mcp --refresh --output json
```

Interpret fields precisely:

- `personalized: true`: ranked from this user's sessions.
- `personalized: false`: popularity fallback because no usable personal profile exists.
- Low `profile_sessions`: answer, but say evidence is thin.
- Empty `items`: successful result, not an error.
- `items[].reason`: quote or summarize this reason without inventing another.

Dismiss only after user confirmation because the preference is durable:

```bash
observal registry recommend dismiss skill NAMESPACE/SLUG --action not_relevant --output json
```

## Install components

Choose the exact harness and scope before writing files.

```bash
observal registry mcp install NAMESPACE/SLUG --harness kiro --no-prompt --output json
observal registry mcp install NAMESPACE/SLUG --harness cursor --version 2.1.0 --no-prompt --output json
observal registry mcp install NAMESPACE/SLUG --harness pi --managed --output json
observal registry skill install NAMESPACE/SLUG --harness claude-code --scope project --output json
observal registry skill install NAMESPACE/SLUG --harness kiro --scope user --version 1.2.0 --output json
observal registry skill install NAMESPACE/SLUG --harness kiro --scope user --version 1.3.0 --check-upgrade --output json
observal registry skill install NAMESPACE/SLUG --harness kiro --scope user --version 1.3.0 --upgrade --output json
observal registry skill backups list --output json
observal registry skill backups restore BACKUP_ID --output json
observal registry skill backups prune BACKUP_ID --output json
observal registry hook install NAMESPACE/SLUG --harness kiro --output json
observal registry hook install NAMESPACE/SLUG --harness claude-code --platform darwin --dir . --output json
observal registry hook install NAMESPACE/SLUG --harness claude-code --version 1.0.0 --dir . --output json
```

Use raw output only when the user explicitly asks for a config snippet or raw response:

```bash
observal registry mcp install NAMESPACE/SLUG --harness claude-code --raw
```

Without `--managed`, MCP install still prints a snippet and writes nothing. `--managed` is for user-scope installs on Pi and Claude Code. On Pi it creates and tracks an Observal-owned `~/.pi/agent/mcp.json` (credentials you supply stay in that private file), or updates another entry in that already-owned file. On Claude Code it adds one user entry with `claude mcp add` and tracks it; it accepts no credentials. It refuses existing pasted configs, an existing entry with the same name, setup warnings, renamed keys and foreign edits. After `observal unfreeze`, an unpinned managed entry can be updated at startup (Pi carries saved credentials forward unchanged and refuses new or changed ones); reload Pi, or start a new Claude Code session, to use it. Do not infer ownership for pasted snippets.

Complete registry folders have managed receipts; upgrades require both exact reviewed provenance and an unchanged complete installed tree. Preview with `--check-upgrade` (it lists `added`, `removed`, `modified` and `mode_changed` paths), then opt in with `--upgrade`. A first install needs no backup root; replacing a folder does, so use `--backup-root DIR` only for a private, Git-ignored, same-filesystem root outside harness skill discovery. Backups are retained until explicitly pruned; restore refuses local modifications. Older unreceipted folders need an unambiguous exact-version lock entry and matching reviewed folder bytes; untracked folders cannot be silently adopted. Git and resource-less direct skills retain their existing install semantics. Never combine raw and JSON modes. JSON MCP installation requires `--no-prompt`; missing required values return a nonzero `error.result.needs_input` response before install generation. Raw mode is the only template workflow that may intentionally contain placeholders. Never print supplied environment or header values.

## Verification

Inspect returned files, setup instructions, warnings, and version. For harness writes, verify with:

```bash
observal scan --harness kiro --output json
```

If installation reports a failed setup command or file write, report partial failure rather than success.
