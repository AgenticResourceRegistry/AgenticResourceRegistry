---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Adding a CLI Command"
---

Read this guide before adding a command, subcommand, or directly executable command group under `observal`.
A CLI command is an agent-facing API. Its help, output, errors, exit status, side effects, documentation, and bundled-skill instructions are one contract and must change together.

## Required contract

Every new command must provide all of the following:

1. One to three copyable examples on its help screen.
2. Human-friendly table output by default when the command returns structured data.
3. JSON output for agents and scripts when the command returns structured data.
4. Categorized failures through the shared error contract.
5. Complete non-interactive inputs for agent and CI use.
6. Tests for help, table output, JSON output, empty results, and failures.
7. Updated CLI documentation and bundled skills.

Do not add a separate example flag, a new output mode, a command-specific error renderer, or a second HTTP client.

## 1. Register the command in the existing hierarchy

Use the existing Typer application for the relevant domain. Register new top-level groups in `observal_cli/main.py` only when no existing group fits.

Canonical paths matter. Verify the final path with the CLI help tree before documenting it. For example, use `observal agent pull` and `observal doctor support`, not historical top-level aliases.

When adding a command changes the command inventory, update the executable-path assertion in `tests/test_cli_errors.py`.

## 2. Add help and examples

Put command examples in the command docstring. Put group examples in the `help` value passed to `typer.Typer`.

Requirements:

- The root, every group, and every leaf help screen must contain one to three examples.
- Every example must begin with that screen's canonical command path.
- Examples must parse with current flags and use realistic values.
- Include a JSON example when the command supports JSON output.
- Keep help concise. Do not add an example-only option or payload printer.
- Dynamically generated commands, such as archive, ownership, and co-author commands, must generate context-specific help rather than showing an example for another component type.

Example:

```python
@widget_app.command("list")
def list_widgets(
    output: OutputMode = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """List widgets visible to the current user.

    Examples:
      observal registry widget list
      observal registry widget list --output json
    """
```

The command-tree help regression in `observal_cli/tests/test_cmd_component_submit_flags.py` must continue to pass.

## 3. Use the table and JSON output contract

Import the shared type and renderer:

```python
from observal_cli.render import OutputMode, output_json
```

Use only these modes:

- `table`: default human output.
- `json`: machine-readable output.

Rules:

- Type every format option as `OutputMode` and default it to `table`.
- Use `output_json`; do not render JSON through Rich.
- Return natural JSON objects. Detail and mutation commands return the direct result object.
- Every dedicated list command returns `{ "items": [...], "total": N, "page": N, "page_size": N }`.
- Unpaginated lists use `page: 1` and `page_size: len(items)`, including `page_size: 0` when empty.
- The `observal api` escape hatch preserves raw endpoint JSON and is the only top-level-array exception.
- Empty JSON results are still valid JSON. Do not return early with a human-only empty message before the JSON branch.
- JSON mode must not emit prompts, spinners, banners, tables, or Rich markup.
- Streams use JSON Lines, one object per line.
- Formatting must not change prompting, confirmation, dry-run behavior, file writes, or side effects.
- Do not overload a format option as a file destination. File paths need a separately named destination option.
- Use external `jq` for querying JSON. Do not embed another query language.

Keep table rendering after the JSON branch:

```python
if output == "json":
    output_json({"items": rows, "total": total, "page": page, "page_size": page_size})
    return

if not rows:
    rprint("[dim]No widgets found.[/dim]")
    return

console.print(table)
```

## 4. Use the shared error contract

HTTP commands must use the shared functions in `observal_cli/client.py`. They map HTTP status, connection, timeout, invalid JSON, and content-type failures into the CLI contract and preserve server request IDs.

Every shared client call receives audited human context through `observal_cli/error_context.py`:

- Add the enclosing function to `OPERATION_LABELS`.
- Add a resource label for a new command module to `RESOURCE_LABELS`.
- If one function performs meaningfully different operations, pass explicit `operation` and `resource` values to that client call.

For local validation or filesystem failures, use `fail` rather than printing an error and raising `typer.Exit`:

```python
from observal_cli.errors import ErrorCategory, fail

fail(
    ErrorCategory.VALIDATION,
    "Widget name is required.",
    operation="Create widget",
    resource="widget payload",
    remediation="Provide --name and retry.",
)
```

Every new error needs:

- A safe, precise message.
- A human operation label.
- A resource label when one exists.
- A concrete remediation.
- Internal detail only in the `detail` field, which is shown only in debug mode.

Never include tokens, passwords, API keys, authorization headers, secret payload fields, or credentials in messages, resources, remediation, details, or logs.

### Exit codes

| Code | Category |
| ---: | --- |
| 0 | Success |
| 1 | Unexpected or uncategorized failure |
| 2 | Usage error |
| 3 | Authentication failure |
| 4 | Permission denied |
| 5 | Resource not found |
| 6 | State conflict |
| 7 | Validation failure |
| 8 | Rate limit reached |
| 9 | Network, service, or dependency unavailable |
| 10 | CLI and server version mismatch |
| 11 | Batch completed with one or more item errors |

When JSON formatting is selected, errors go to stderr as one JSON object and stdout stays clean. Safe repair data belongs in `error.result`; internal diagnostics remain debug-only. Finite partial batches keep one result document on stdout, set `partial: true`, and exit 11. File-destination options named `output` do not activate JSON error rendering.

## 5. Preserve non-interactive and side-effect safety

Agents must be able to run the command without hidden prompts.

- Expose every required input as an argument or option.
- A JSON invocation must never prompt.
- Destructive commands need an explicit confirmation bypass consistent with neighboring commands.
- Add dry-run support when the operation writes files or makes a multi-resource mutation and preview is meaningful.
- Repeating the command must be safe or return a clear conflict.
- Validate all input before the first mutation whenever possible.
- Never report success before all required side effects complete.

## 6. Update documentation and bundled skills

When a command, path, argument, option, or behavior changes:

1. Update the matching page under `docs/cli/`.
2. Update every applicable bundled skill under `observal_cli/skills/`.
3. Regenerate the command reference:

```bash
python scripts/sync_observal_skill.py
```

Bundled skills should use JSON explicitly for machine-readable workflows and must describe all inputs needed to avoid prompts.

## 7. Add the smallest complete tests

At minimum, cover:

- Help renders and includes canonical examples.
- Default table output.
- JSON output parses with `json.loads`.
- Empty JSON output remains valid and correctly shaped.
- Invalid output modes are rejected as usage errors.
- API failures produce the expected category, exit code, operation, resource, remediation, and request ID.
- JSON failures produce zero stdout bytes and one JSON object on stderr.
- Partial batches preserve per-item JSON, set `partial: true`, and exit 11.
- Debug-only detail is absent by default.
- Confirmation, dry-run, idempotence, file writes, and secret handling when applicable.

Mock external services and subprocesses. Do not require Docker for CLI unit tests.

## Done checklist

Before declaring the command complete:

- [ ] Canonical path is registered in the correct group.
- [ ] Root, group, and command help remain covered.
- [ ] The command has one to three current examples.
- [ ] Structured output supports shared `table` and `json` modes.
- [ ] JSON output and JSON errors contain no Rich or progress noise.
- [ ] Paginated JSON has `items`, `total`, `page`, and `page_size`.
- [ ] Errors use shared categories, context, remediation, and request IDs.
- [ ] Non-interactive execution has no hidden prompts.
- [ ] Side effects are confirmation-safe and dry-run-safe where applicable.
- [ ] CLI docs and bundled skills are updated.
- [ ] Generated skill command references are synchronized.
- [ ] Focused tests, `make lint`, and `make test` pass.
- [ ] `observal_cli/tests` is run explicitly because `make test` does not include it.
