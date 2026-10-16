# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com>
# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 EuanTop <euan@mail.bnu.edu.cn>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""observal doctor: diagnose and patch harness settings for Observal session telemetry.

Supports Claude Code and Kiro.  Injects 2 hooks (UserPromptSubmit + Stop) that
push session JSONL incrementally to the server.
"""

import hashlib
import json
import sys
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path

import typer
from loguru import logger as optic
from rich import print as rprint
from typer.models import OptionInfo

from observal_cli import config, pi_extension
from observal_cli.errors import ErrorCategory, fail
from observal_cli.harness import ensure_loaded, get_adapter
from observal_cli.harness_specs.claude_code_hooks_spec import (
    MANAGED_ENV_KEYS,
    get_desired_hooks,
)
from observal_cli.render import OutputMode, esc, output_json
from observal_cli.shared.utils import (
    atomic_write,
)
from observal_cli.shared.utils import (
    is_observal_hook_entry as _is_observal_hook_entry,
)
from observal_cli.shared.utils import (
    is_observal_matcher_group as _is_observal_matcher_group,
)
from observal_cli.shared.utils import (
    load_jsonc as _load_jsonc,
)
from observal_shared.harness_registry import get_valid_harnesses

doctor_app = typer.Typer(
    help=(
        "Diagnose and patch harness settings for Observal telemetry\n\n"
        "Examples:\n"
        "  observal doctor\n"
        "  observal doctor patch --all-harnesses\n"
        "  observal doctor cleanup --harness claude-code --dry-run"
    )
)


# ── Helpers ──────────────────────────────────────────────────


def _load_json(path: Path) -> dict | None:
    optic.trace("path={}", path)
    try:
        data = _load_jsonc(path)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _read_json_object(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _value(value):
    return value.default if isinstance(value, OptionInfo) else value


def _capture(output: OutputMode | str):
    return redirect_stdout(StringIO()) if _value(output) == "json" else nullcontext()


# ── Diagnose command ─────────────────────────────────────────


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-fix all warnings without prompting"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Diagnose local configuration, Registry metadata, and harness telemetry."""
    if ctx.invoked_subcommand is not None:
        return

    output = _value(output)
    yes = _value(yes)
    issues: list[str] = []
    warnings: list[str] = []
    lockfile_plan = None
    lockfile_changes: list[dict] = []
    skill_missing: list[str] = []
    fix_attempted = False
    patch_result: dict | None = None

    with _capture(output):
        rprint("[bold]Observal Doctor[/bold]\n")
        checks = (
            ("Observal config", _check_observal_config),
            ("Claude Code", _check_claude_code),
            ("Kiro", _check_kiro),
            ("Pi", _check_pi),
            ("Cursor", _check_cursor),
            ("Codex", _check_codex),
            ("Copilot (VS Code)", _check_copilot),
            ("Copilot CLI", _check_copilot_cli),
            ("OpenCode", _check_opencode),
            ("Antigravity", _check_antigravity),
            ("Goose", _check_goose),
        )

        rprint("[cyan]Checking Registry lockfile...[/cyan]")
        try:
            from observal_cli.lockfile_reconcile import plan_lockfile_reconciliation

            lockfile_plan = plan_lockfile_reconciliation()
            lockfile_changes = [
                {
                    "label": change.label,
                    "field": change.field,
                    "old": change.old,
                    "new": change.new,
                }
                for change in lockfile_plan.changes
            ]
            if lockfile_changes:
                warnings.append(f"Registry metadata drift found in {len(lockfile_changes)} lockfile field(s).")
                for change in lockfile_changes[:10]:
                    rprint(
                        f"  [dim]{esc(change['label'])}: {esc(change['field'])} "
                        f"{esc(repr(change['old']))} → {esc(repr(change['new']))}[/dim]"
                    )
                if len(lockfile_changes) > 10:
                    rprint(f"  [dim]...and {len(lockfile_changes) - 10} more change(s)[/dim]")
            issues.extend(str(warning) for warning in lockfile_plan.warnings)
        except Exception as error:
            issues.append(f"Lockfile reconciliation failed: {error}")

        for label, check in checks:
            rprint(f"[cyan]Checking {label}...[/cyan]")
            check(issues, warnings)

        skill_missing = _check_observal_skill_missing()
        if skill_missing:
            warnings.append(
                f"Observal AI skill not installed for: {', '.join(skill_missing)}. "
                "LLMs will not have Observal commands available."
            )

        rprint("")
        if not issues and not warnings:
            rprint("[bold green]All clear![/bold green] No issues found.")
        else:
            if issues:
                rprint(f"[bold red]{len(issues)} issue(s):[/bold red]")
                for index, issue in enumerate(issues, 1):
                    rprint(f"  [red]{index}.[/red] {esc(issue)}")
            if warnings:
                rprint(f"\n[bold yellow]{len(warnings)} warning(s):[/bold yellow]")
                for index, warning in enumerate(warnings, 1):
                    rprint(f"  [yellow]{index}.[/yellow] {esc(warning)}")

        fixable = bool(warnings)
        should_fix = fixable and yes
        if fixable and not yes and output != "json" and sys.stdin.isatty():
            rprint("")
            should_fix = typer.confirm(
                "Fix all warnings? (configures telemetry and installs AI skills for detected harnesses)",
                default=True,
            )
        if should_fix:
            fix_attempted = True
            if lockfile_plan and lockfile_plan.changes:
                if lockfile_plan.apply():
                    rprint(f"[green]✓ Reconciled {len(lockfile_plan.changes)} lockfile field(s)[/green]")
                else:
                    warnings.extend(str(warning) for warning in lockfile_plan.warnings)
                    rprint("[yellow]⚠ Installed state changed; registry reconciliation was skipped.[/yellow]")
            patch_result = _patch_targets(list(_VALID_HARNESSES), dry_run=False, output=output)
            from observal_cli.skill_installer import install_observal_skill

            install_observal_skill()
        elif fixable and output != "json":
            rprint("[dim]  Run [bold]observal doctor patch --all-harnesses[/bold] anytime to fix.[/dim]")

    result = {
        "healthy": not issues and not warnings,
        "issues": issues,
        "warnings": warnings,
        "lockfile_changes": lockfile_changes,
        "skill_missing": skill_missing,
        "fix_attempted": fix_attempted,
        "patch": patch_result,
    }
    if output == "json":
        output_json(result)
        return
    if issues:
        raise typer.Exit(1)


def _check_observal_skill_missing() -> list[str]:
    """Return list of harness display names where the observal skill is not installed."""
    from observal_cli.skill_installer import missing_observal_skill_harnesses

    return missing_observal_skill_harnesses()


def _check_observal_config(issues: list, warnings: list):
    optic.trace("issues={}, warnings={}", issues, warnings)
    config_path = Path.home() / ".observal" / "config.json"
    if not config_path.exists():
        issues.append("~/.observal/config.json not found. Run `observal auth login` first.")
        return

    data = _load_json(config_path)
    if data is None:
        issues.append("~/.observal/config.json is not valid JSON.")
        return

    if not data.get("access_token"):
        issues.append("No access token in ~/.observal/config.json. Run `observal auth login`.")

    if not data.get("server_url"):
        issues.append("No server_url in ~/.observal/config.json. Run `observal auth login`.")

    server_url = data.get("server_url", "")
    if server_url:
        try:
            import httpx

            resp = httpx.get(f"{server_url}/health", timeout=5)
            if resp.status_code != 200:
                issues.append(f"Observal server at {server_url} returned status {resp.status_code}.")
        except Exception as e:
            issues.append(f"Cannot reach Observal server at {server_url}: {e}")


def _check_claude_code(issues: list, warnings: list):
    optic.trace("issues={}, warnings={}", issues, warnings)
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        rprint("  [dim]No ~/.claude/settings.json found[/dim]")
        return

    data = _load_json(settings_path)
    if data is None:
        issues.append(f"{settings_path}: not valid JSON.")
        return

    if data.get("disableAllHooks"):
        issues.append(f"{settings_path}: `disableAllHooks` is true. Observal hooks will not fire.")

    # Check if session push hooks are installed
    hooks = data.get("hooks", {})
    has_session_push = False
    for event in ("UserPromptSubmit", "Stop"):
        groups = hooks.get(event, [])
        for g in groups:
            for h in g.get("hooks", []):
                if "observal_cli.hooks.session_push" in h.get("command", ""):
                    has_session_push = True
                    break

    if not has_session_push:
        warnings.append(
            "Claude Code session push hooks not installed. "
            "Run `observal doctor patch --harness claude-code` to inject them."
        )

    # Check for stale legacy hooks
    has_legacy = False
    for _event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            for h in g.get("hooks", []):
                cmd = h.get("command", "")
                if any(m in cmd for m in ("observal-hook", "observal-stop-hook", "/api/v1/telemetry/hooks")):
                    has_legacy = True
                    break

    if has_legacy:
        warnings.append(
            "Legacy Observal hooks detected (old hook scripts). "
            "Run `observal doctor cleanup --harness claude-code` to remove them."
        )


def _check_kiro(issues: list, warnings: list):
    optic.trace("issues={}, warnings={}", issues, warnings)
    agents_dir = Path.home() / ".kiro" / "agents"
    if not agents_dir.is_dir():
        rprint("  [dim]No ~/.kiro/agents/ found[/dim]")
        return

    agent_profiles = list(agents_dir.glob("*.json"))
    if not agent_profiles:
        rprint("  [dim]No Kiro agent configs found[/dim]")
        return

    has_session_push = False
    for af in agent_profiles:
        try:
            agent_data = _read_json_object(af)
        except Exception:
            continue
        hooks = agent_data.get("hooks", {})
        for _event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for h in entries:
                command = h.get("command", "")
                if "observal_cli.hooks.session_push --harness kiro" in command:
                    has_session_push = True
                    break

    if not has_session_push:
        warnings.append(
            "Kiro acknowledged session hooks not installed in any agent config. "
            "Pull the Kiro agent again to refresh its attributed hooks."
        )


def _check_pi(issues: list, warnings: list):
    """Check whether the Pi telemetry extension (npm or local) is current."""
    optic.debug("_check_pi")
    try:
        status = pi_extension.check_status()
    except json.JSONDecodeError:
        issues.append(f"{pi_extension.settings_path()}: not valid JSON.")
        return
    except (OSError, ValueError, TypeError) as exc:
        issues.append(f"Pi telemetry extension: {exc}")
        return

    if status.state == pi_extension.NOT_DETECTED:
        rprint("  [dim]Pi not detected[/dim]")
        return
    if status.message:
        warnings.append(status.message)


def _check_cursor(issues: list, warnings: list):
    """Check if Observal session push hooks are installed in Cursor."""
    optic.debug("_check_cursor")
    hooks_path = Path.home() / ".cursor" / "hooks.json"
    if not (Path.home() / ".cursor").exists():
        rprint("  [dim]Cursor not detected[/dim]")
        return

    if not hooks_path.exists():
        warnings.append(
            "Cursor session push hooks not installed. Run `observal doctor patch --harness cursor` to inject them."
        )
        return

    data = _load_json(hooks_path)
    if data is None:
        issues.append(f"{hooks_path}: not valid JSON.")
        return

    hooks = data.get("hooks", {})
    has_session_push = False
    for event in ("beforeSubmitPrompt", "stop"):
        entries = hooks.get(event, [])
        for e in entries:
            if "hooks.session_push --harness cursor" in e.get("command", ""):
                has_session_push = True
                break

    if not has_session_push:
        warnings.append(
            "Cursor session push hooks not installed. Run `observal doctor patch --harness cursor` to inject them."
        )


def _check_codex(issues: list, warnings: list):
    """Check if Observal session push hooks are installed in Codex."""
    optic.debug("_check_codex")
    codex_dir = Path.home() / ".codex"
    if not codex_dir.exists():
        rprint("  [dim]Codex not detected[/dim]")
        return

    hooks_path = codex_dir / "hooks.json"
    config_path = codex_dir / "config.toml"

    # Check hooks
    has_session_push = False
    if hooks_path.exists():
        data = _load_json(hooks_path)
        if data is not None:
            hooks = data.get("hooks", {})
            for event in ("UserPromptSubmit", "Stop"):
                groups = hooks.get(event, [])
                for g in groups:
                    if isinstance(g, dict):
                        for h in g.get("hooks", []):
                            if isinstance(h, dict) and "hooks.session_push --harness codex" in h.get("command", ""):
                                has_session_push = True
                                break

    if not has_session_push:
        warnings.append(
            "Codex session push hooks not installed. Run `observal doctor patch --harness codex` to inject them."
        )

    # Check codex_hooks flag
    if config_path.exists():
        try:
            content = config_path.read_text()
            if "codex_hooks = false" in content:
                issues.append(f"{config_path}: `codex_hooks = false`. Observal hooks will not fire.")
        except OSError as error:
            issues.append(f"{config_path}: cannot read Codex configuration: {error}")


def _check_copilot(issues: list, warnings: list):
    """Check if Observal session push hooks are installed for Copilot (VS Code agent mode)."""
    optic.debug("_check_copilot")
    # Copilot VS Code uses project-level hooks in .github/hooks/
    # and user-level hooks in ~/.copilot/hooks/
    user_hooks = Path.home() / ".copilot" / "hooks" / "observal.json"
    project_hooks = Path.cwd() / ".github" / "hooks" / "observal.json"

    # Check if VS Code / Copilot is even present
    has_vscode = (Path.home() / ".vscode").exists()
    if not has_vscode:
        rprint("  [dim]Copilot (VS Code) not detected[/dim]")
        return

    has_hooks = False
    for hooks_path in (user_hooks, project_hooks):
        if hooks_path.exists():
            data = _load_json(hooks_path)
            if data is not None:
                hooks = data.get("hooks", {})
                for entries in hooks.values():
                    if isinstance(entries, list):
                        for e in entries:
                            cmd = e.get("command", "") + e.get("bash", "")
                            if (
                                "copilot_vscode_session_push" in cmd
                                or "run_hook.ps1" in cmd
                                or "hooks.session_push --harness copilot" in cmd
                            ):
                                has_hooks = True
                                break

    if not has_hooks:
        warnings.append(
            "Copilot (VS Code) session push hooks not installed. "
            "Run `observal doctor patch --harness copilot` to inject them."
        )


def _check_copilot_cli(issues: list, warnings: list):
    """Check if Observal session push hooks are installed for Copilot CLI."""
    optic.debug("_check_copilot_cli")
    copilot_dir = Path.home() / ".copilot"
    if not copilot_dir.exists():
        rprint("  [dim]Copilot CLI not detected[/dim]")
        return

    hooks_path = copilot_dir / "hooks" / "observal.json"
    if not hooks_path.exists():
        warnings.append(
            "Copilot CLI session push hooks not installed. "
            "Run `observal doctor patch --harness copilot-cli` to inject them."
        )
        return

    data = _load_json(hooks_path)
    if data is None:
        issues.append(f"{hooks_path}: not valid JSON.")
        return

    hooks = data.get("hooks", {})
    has_session_push = False
    for event in ("sessionStart", "sessionEnd", "userPromptSubmitted"):
        entries = hooks.get(event, [])
        for e in entries:
            command = e.get("bash", "")
            if "copilot_cli_session_push" in command or "hooks.session_push --harness copilot-cli" in command:
                has_session_push = True
                break

    if not has_session_push:
        warnings.append(
            "Copilot CLI session push hooks not installed. "
            "Run `observal doctor patch --harness copilot-cli` to inject them."
        )


def _check_opencode(issues: list, warnings: list):
    """Check if Observal plugin is installed for OpenCode."""
    optic.debug("_check_opencode")
    opencode_dir = Path.home() / ".config" / "opencode"
    if not opencode_dir.exists():
        rprint("  [dim]OpenCode not detected[/dim]")
        return

    plugin_path = opencode_dir / "plugins" / "observal-plugin.ts"
    if not plugin_path.exists():
        warnings.append(
            "OpenCode observal plugin not installed. Run `observal doctor patch --harness opencode` to inject it."
        )
        return

    try:
        from observal_shared.opencode_plugin_source import OPENCODE_PLUGIN_SOURCE, OPENCODE_PLUGIN_VERSION

        current = plugin_path.read_text(errors="ignore")
        desired_hash = hashlib.sha256(OPENCODE_PLUGIN_SOURCE.encode()).hexdigest()
        current_hash = hashlib.sha256(current.encode()).hexdigest()
        if current_hash == desired_hash:
            return
        if "offline stub" in current or "event: async () => {}" in current:
            warnings.append(
                "OpenCode observal plugin is an offline stub. "
                "Run `observal doctor patch --harness opencode` to update it."
            )
            return
        if f'OBSERVAL_PLUGIN_VERSION = "{OPENCODE_PLUGIN_VERSION}"' not in current or current_hash != desired_hash:
            warnings.append(
                "OpenCode observal plugin is stale or modified. "
                "Run `observal doctor patch --harness opencode` to update it."
            )
    except OSError as e:
        issues.append(f"{plugin_path}: failed to read OpenCode plugin: {e}")


def _check_antigravity(issues: list, warnings: list):
    """Check if Observal hooks are installed for Antigravity CLI."""
    optic.debug("_check_antigravity")
    from observal_cli.shared.utils import resolve_antigravity_config_dir

    config_dir = resolve_antigravity_config_dir()
    if not config_dir:
        rprint("  [dim]Antigravity not detected[/dim]")
        return

    hooks_path = config_dir / "hooks.json"
    if not hooks_path.exists():
        warnings.append(
            "Antigravity session push hooks not installed. "
            "Run `observal doctor patch --harness antigravity` to inject them."
        )
        return

    data = _load_json(hooks_path)
    if data is None:
        issues.append(f"{hooks_path}: not valid JSON.")
        return

    group = data.get("observal-telemetry", {})
    if not isinstance(group, dict):
        warnings.append(
            "Antigravity session push hooks not installed. "
            "Run `observal doctor patch --harness antigravity` to inject them."
        )
        return

    has_hook = False
    for evt in ("PreInvocation", "Stop"):
        handlers = group.get(evt, [])
        if isinstance(handlers, list):
            for h in handlers:
                if "antigravity_session_push" in h.get("command", ""):
                    has_hook = True
                    break

    if not has_hook:
        warnings.append(
            "Antigravity session push hooks not installed. "
            "Run `observal doctor patch --harness antigravity` to inject them."
        )


def _goose_event_current(installed_rules: object, desired_rules: list) -> bool:
    """Return True when *installed_rules* already carries every desired Observal handler.

    ``_patch_goose`` keeps foreign rules alongside ours, so comparing the whole
    rule list would report a permanently stale hook.
    """
    if not isinstance(installed_rules, list):
        return False
    present = [
        handler
        for rule in installed_rules
        if isinstance(rule, dict)
        for handler in rule.get("hooks", [])
        if isinstance(handler, dict)
    ]
    wanted = [handler for rule in desired_rules for handler in rule.get("hooks", [])]
    return all(handler in present for handler in wanted)


def _check_goose(issues: list, warnings: list):
    """Check that the Observal hook plugin is installed for Goose."""
    optic.debug("_check_goose")
    from observal_cli.harness_specs.goose_hooks_spec import GOOSE_HOOK_EVENTS, build_hooks, hooks_file
    from observal_cli.shared.utils import resolve_goose_config_dir, resolve_goose_data_dir

    if not resolve_goose_config_dir().is_dir() and not resolve_goose_data_dir().is_dir():
        rprint("  [dim]Goose not detected[/dim]")
        return

    hooks_path = hooks_file()
    missing = "Goose session push hooks not installed. Run `observal doctor patch --harness goose` to inject them."
    if not hooks_path.exists():
        warnings.append(missing)
        return

    data = _load_json(hooks_path)
    if not isinstance(data, dict):
        issues.append(f"{hooks_path}: not valid JSON object.")
        return

    installed = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    desired = build_hooks()["hooks"]
    stale = [event for event in GOOSE_HOOK_EVENTS if not _goose_event_current(installed.get(event), desired[event])]
    if stale:
        warnings.append(
            f"Goose session push hooks are missing or stale for: {', '.join(stale)}. "
            "Run `observal doctor patch --harness goose` to update them."
        )


# ── Cleanup command ──────────────────────────────────────────


def _valid_targets(values: list[str], operation: str, valid_harnesses: list[str] | None = None) -> list[str]:
    valid = tuple(valid_harnesses or get_valid_harnesses())
    unknown = [value for value in values if value not in valid]
    if unknown:
        fail(
            ErrorCategory.VALIDATION,
            f"Unknown harness: {unknown[0]}.",
            operation=operation,
            resource="harness",
            remediation=f"Choose from: {', '.join(valid)}.",
        )
    return list(dict.fromkeys(values))


def _adapter_change(target: str, action: str, dry_run: bool, output: OutputMode | str) -> bool:
    from observal_cli.harness.protocol import NotSupportedError

    try:
        with _capture(output):
            adapter = get_adapter(target)
            method = adapter.patch_hooks if action == "patch" else adapter.cleanup_hooks
            return bool(method(dry_run))
    except NotSupportedError as error:
        fail(
            ErrorCategory.VALIDATION,
            str(error),
            operation=f"Doctor {action}",
            resource=target,
            remediation="Choose a harness that supports managed telemetry hooks.",
        )
    except (json.JSONDecodeError, ValueError) as error:
        fail(
            ErrorCategory.VALIDATION,
            f"Cannot {action} {target}: a harness configuration file is invalid JSON.",
            operation=f"Doctor {action}",
            resource=target,
            remediation="Repair the harness configuration and retry.",
            detail=repr(error),
        )
    except (OSError, RuntimeError) as error:
        fail(
            ErrorCategory.UNAVAILABLE,
            f"Cannot {action} {target} harness files.",
            operation=f"Doctor {action}",
            resource=target,
            remediation="Check filesystem paths and permissions, then retry.",
            detail=repr(error),
        )


def _patch_targets(targets: list[str], *, dry_run: bool, output: OutputMode | str) -> dict:
    ensure_loaded()
    rows = []
    for target in targets:
        rows.append({"harness": target, "changed": _adapter_change(target, "patch", dry_run, output)})
    changed = any(row["changed"] for row in rows)
    if changed and not dry_run:
        from observal_cli.audit import emit_cli_audit

        emit_cli_audit(
            "doctor.patch",
            resource_type="harness",
            detail=f"harnesses={','.join(targets)}, hooks=true",
            sensitivity="high",
        )
    return {"action": "patch", "dry_run": dry_run, "changed": changed, "targets": rows}


def _cleanup_targets(targets: list[str], *, dry_run: bool, output: OutputMode | str) -> dict:
    ensure_loaded()
    rows = []
    for target in targets:
        rows.append({"harness": target, "changed": _adapter_change(target, "cleanup", dry_run, output)})
    changed = any(row["changed"] for row in rows)
    if changed and not dry_run:
        from observal_cli.audit import emit_cli_audit

        emit_cli_audit(
            "doctor.cleanup",
            resource_type="harness",
            detail=f"harnesses={','.join(targets)}, hooks=true",
            sensitivity="high",
        )
    return {"action": "cleanup", "dry_run": dry_run, "changed": changed, "targets": rows}


@doctor_app.command(name="cleanup")
def doctor_cleanup(
    harness: str | None = typer.Option(None, "--harness", "-i", help="Target one harness. Default: all."),
    exclude: list[str] = typer.Option([], "--exclude", "-x", help="Exclude a harness (repeatable)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Remove Observal-managed telemetry artifacts while preserving user configuration.

    Examples:
      observal doctor cleanup --dry-run
      observal doctor cleanup --harness claude-code --yes
      observal doctor cleanup --yes --output json
    """
    output = _value(output)
    yes = _value(yes)
    dry_run = _value(dry_run)
    harness = _value(harness)
    exclude = _value(exclude)
    valid_harnesses = get_valid_harnesses()
    exclude = _valid_targets(list(exclude), "Clean up Doctor instrumentation", valid_harnesses)
    selected = (
        _valid_targets([harness], "Clean up Doctor instrumentation", valid_harnesses) if harness else valid_harnesses
    )
    targets = [target for target in selected if target not in exclude]
    if not targets:
        fail(
            ErrorCategory.VALIDATION,
            "Doctor cleanup has no target harnesses.",
            operation="Clean up Doctor instrumentation",
            resource="harness selection",
            remediation="Remove the conflicting exclusion or select another harness.",
        )
    if not dry_run and output == "json" and not yes:
        fail(
            ErrorCategory.VALIDATION,
            "JSON mode cannot prompt before removing telemetry instrumentation.",
            operation="Clean up Doctor instrumentation",
            resource="harness instrumentation",
            remediation="Add --yes to confirm cleanup.",
        )
    if not dry_run and output != "json" and not yes:
        typer.confirm("Remove Observal-managed telemetry instrumentation?", abort=True)

    with _capture(output):
        rprint("[bold]Observal Doctor: Cleanup[/bold]\n")
        result = _cleanup_targets(targets, dry_run=dry_run, output=output)
        if dry_run:
            rprint("\n[yellow]Dry run, no changes made.[/yellow]")
        elif result["changed"]:
            rprint("\n[green]✓ Cleanup complete.[/green] Restart your harness sessions to take effect.")
        else:
            rprint("\n[dim]Nothing to clean up, no Observal artifacts found.[/dim]")
    if output == "json":
        output_json(result)


def _cleanup_claude_code(dry_run: bool) -> bool:
    optic.trace("dry_run={}", dry_run)
    rprint("[cyan]Claude Code[/cyan]")
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        rprint("  [dim]No settings.json found - skipping[/dim]")
        return False

    data = _read_json_object(settings_path)

    changed = False

    # Remove Observal-managed env vars (OBSERVAL_*)
    env = data.get("env", {})
    removed_env = []
    for key in list(env):
        if key in MANAGED_ENV_KEYS:
            removed_env.append(key)
            if not dry_run:
                del env[key]
            changed = True
    if removed_env:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} env vars: {', '.join(removed_env)}")

    # Remove Observal hooks from each event
    hooks = data.get("hooks", {})
    removed_events = []
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        cleaned = [g for g in groups if not _is_observal_matcher_group(g)]
        if len(cleaned) < len(groups):
            removed_events.append(f"{event} ({len(groups) - len(cleaned)} removed)")
            if not dry_run:
                if cleaned:
                    hooks[event] = cleaned
                else:
                    del hooks[event]
            changed = True
    if removed_events:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} hooks: {esc(', '.join(removed_events))}")

    if changed and not dry_run:
        # Clean up empty sections
        if not data.get("env"):
            data.pop("env", None)
        if not data.get("hooks"):
            data.pop("hooks", None)
        atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
        rprint(f"  [green]Written {esc(settings_path)}[/green]")

    if not changed:
        rprint("  [dim]No Observal artifacts found[/dim]")

    return changed


def _cleanup_kiro(dry_run: bool) -> bool:
    optic.trace("dry_run={}", dry_run)
    rprint("[cyan]Kiro[/cyan]")
    agents_dir = Path.home() / ".kiro" / "agents"
    if not agents_dir.is_dir():
        rprint("  [dim]No ~/.kiro/agents/ found - skipping[/dim]")
        return False

    changed = False
    for agent_profile in sorted(agents_dir.glob("*.json")):
        agent_data = _read_json_object(agent_profile)

        agent_changed = False

        # Remove hooks that reference Observal
        hooks = agent_data.get("hooks", {})
        if isinstance(hooks, dict):
            for event, entries in list(hooks.items()):
                if not isinstance(entries, list):
                    continue
                cleaned = [e for e in entries if not _is_observal_hook_entry(e)]
                if len(cleaned) < len(entries):
                    agent_changed = True
                    if not dry_run:
                        if cleaned:
                            hooks[event] = cleaned
                        else:
                            del hooks[event]

        if agent_changed:
            changed = True
            verb = "Would clean" if dry_run else "Cleaned"
            rprint(f"  {verb} {esc(agent_profile.name)}")
            if not dry_run:
                atomic_write(agent_profile, json.dumps(agent_data, indent=2) + "\n")

    # Standalone v1 hooks files (~/.kiro/hooks/observal.json and project copies)
    if _cleanup_kiro_hook_files(dry_run):
        changed = True

    if not changed:
        rprint("  [dim]No Observal artifacts found in Kiro agents[/dim]")

    return changed


def _kiro_hook_dirs() -> set[Path]:
    """Return every directory ``_patch_kiro`` may write a v1 hooks file into.

    Cleanup has to cover exactly the same set. Patch writes one file per scope -
    the user scope plus each locked agent's project - so cleaning only the user
    scope leaves project hooks live and Kiro keeps running Observal commands
    after an uninstall.
    """
    from observal_cli.lockfile import read_registry_lockfile

    dirs: set[Path] = set()
    user_config_dir = Path.home() / ".kiro"
    if user_config_dir.is_dir():
        dirs.add(user_config_dir)
    try:
        _, registry = read_registry_lockfile()
        for agent in registry.get("harnesses", {}).get("kiro", {}).get("agents", []):
            directory = agent.get("directory")
            if directory:
                dirs.add(Path(directory) / ".kiro")
    except Exception as exc:
        optic.debug("could not read lockfile for Kiro hook cleanup: {}", exc)
    return dirs


def _cleanup_kiro_hook_files(dry_run: bool) -> bool:
    """Strip Observal entries from every standalone v1 Kiro hooks file."""
    changed = False
    for config_dir in sorted(_kiro_hook_dirs()):
        if _cleanup_kiro_hook_file(config_dir / "hooks", dry_run):
            changed = True
    return changed


def _cleanup_kiro_hook_file(hooks_dir: Path, dry_run: bool) -> bool:
    """Strip Observal entries from one v1 hooks file."""
    from observal_cli.harness_specs.kiro_hooks_spec import (
        KIRO_V1_HOOK_FILENAME,
        is_observal_v1_hook,
    )

    hooks_file = hooks_dir / KIRO_V1_HOOK_FILENAME
    try:
        data = json.loads(hooks_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False

    entries = data.get("hooks")
    if not isinstance(entries, list):
        return False
    kept = [h for h in entries if not is_observal_v1_hook(h)]
    if len(kept) == len(entries):
        return False

    verb = "Would clean" if dry_run else "Cleaned"
    rprint(f"  {verb} {esc(hooks_file)}")
    if not dry_run:
        if kept:
            data["hooks"] = kept
            atomic_write(hooks_file, json.dumps(data, indent=2) + "\n")
        else:
            hooks_file.unlink(missing_ok=True)
    return True


def _cleanup_pi(dry_run: bool) -> bool:
    """Remove an Observal-managed local Pi extension install.

    Never touches an npm:observal-pi registration (that's the user's own
    choice, not something Observal installed) and never touches a local
    file that isn't Observal-managed.
    """
    rprint("[cyan]Pi[/cyan]")
    changed = pi_extension.remove(dry_run=dry_run)
    if changed:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} {esc(pi_extension.extension_path())}")
    else:
        rprint("  [dim]No Observal-managed Pi extension found[/dim]")
    return changed


def _cleanup_cursor(dry_run: bool) -> bool:
    """Remove Observal hooks from ~/.cursor/hooks.json."""
    rprint("[cyan]Cursor[/cyan]")
    hooks_path = Path.home() / ".cursor" / "hooks.json"
    if not hooks_path.exists():
        rprint("  [dim]No ~/.cursor/hooks.json found - skipping[/dim]")
        return False

    data = _read_json_object(hooks_path)

    hooks = data.get("hooks", {})
    changed = False
    removed_events = []

    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        cleaned = [
            h
            for h in entries
            if "cursor_session_push" not in h.get("command", "") and "session_push" not in h.get("command", "")
        ]
        if len(cleaned) < len(entries):
            removed_events.append(f"{event} ({len(entries) - len(cleaned)} removed)")
            if not dry_run:
                if cleaned:
                    hooks[event] = cleaned
                else:
                    del hooks[event]
            changed = True

    if changed:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} hooks: {esc(', '.join(removed_events))}")
        if not dry_run:
            if not data.get("hooks"):
                data.pop("hooks", None)
            atomic_write(hooks_path, json.dumps(data, indent=2) + "\n")
            rprint(f"  [green]Written {esc(hooks_path)}[/green]")
    else:
        rprint("  [dim]No Observal artifacts found[/dim]")

    return changed


def _cleanup_codex(dry_run: bool) -> bool:
    """Remove Observal hooks from ~/.codex/hooks.json."""
    rprint("[cyan]Codex[/cyan]")
    codex_dir = Path.home() / ".codex"
    hooks_path = codex_dir / "hooks.json"
    if not hooks_path.exists():
        rprint("  [dim]No ~/.codex/hooks.json found - skipping[/dim]")
        return False

    data = _read_json_object(hooks_path)

    hooks = data.get("hooks", {})
    changed = False
    removed_events = []

    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        cleaned = [
            g
            for g in groups
            if not isinstance(g, dict)
            or not any("session_push" in h.get("command", "") for h in g.get("hooks", []) if isinstance(h, dict))
        ]
        if len(cleaned) < len(groups):
            removed_events.append(f"{event} ({len(groups) - len(cleaned)} removed)")
            if not dry_run:
                if cleaned:
                    hooks[event] = cleaned
                else:
                    del hooks[event]
            changed = True

    if changed:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} hooks: {esc(', '.join(removed_events))}")
        if not dry_run:
            if not data.get("hooks"):
                data.pop("hooks", None)
            atomic_write(hooks_path, json.dumps(data, indent=2) + "\n")
            rprint(f"  [green]Written {esc(hooks_path)}[/green]")
    else:
        rprint("  [dim]No Observal artifacts found[/dim]")

    return changed


def _cleanup_copilot(dry_run: bool) -> bool:
    """Remove Observal hooks from .github/hooks/observal.json and ~/.copilot/hooks/."""
    rprint("[cyan]Copilot (VS Code)[/cyan]")
    changed = False

    targets = [
        Path.cwd() / ".github" / "hooks" / "observal.json",
        Path.home() / ".copilot" / "hooks" / "observal.json",
    ]
    ps1_path = Path.cwd() / ".github" / "hooks" / "run_hook.ps1"

    for hooks_path in targets:
        if hooks_path.exists():
            verb = "Would remove" if dry_run else "Removed"
            rprint(f"  {verb} {esc(hooks_path)}")
            if not dry_run:
                hooks_path.unlink()
            changed = True

    if ps1_path.exists():
        existing = ps1_path.read_text()
        if "copilot_vscode_session_push" in existing or "hooks.session_push --harness copilot" in existing:
            verb = "Would remove" if dry_run else "Removed"
            rprint(f"  {verb} {esc(ps1_path)}")
            if not dry_run:
                ps1_path.unlink()
            changed = True

    if not changed:
        rprint("  [dim]No Observal artifacts found[/dim]")

    return changed


def _cleanup_copilot_cli(dry_run: bool) -> bool:
    """Remove Observal hooks from ~/.copilot/hooks/observal.json."""
    rprint("[cyan]Copilot CLI[/cyan]")
    hooks_path = Path.home() / ".copilot" / "hooks" / "observal.json"

    if not hooks_path.exists():
        rprint("  [dim]No ~/.copilot/hooks/observal.json found - skipping[/dim]")
        return False

    verb = "Would remove" if dry_run else "Removed"
    rprint(f"  {verb} {esc(hooks_path)}")
    if not dry_run:
        hooks_path.unlink()

    return True


def _cleanup_opencode(dry_run: bool) -> bool:
    """Remove Observal plugin from ~/.config/opencode/plugins/."""
    rprint("[cyan]OpenCode[/cyan]")
    plugin_path = Path.home() / ".config" / "opencode" / "plugins" / "observal-plugin.ts"

    if not plugin_path.exists():
        rprint("  [dim]No observal plugin found - skipping[/dim]")
        return False

    verb = "Would remove" if dry_run else "Removed"
    rprint(f"  {verb} {esc(plugin_path)}")
    if not dry_run:
        plugin_path.unlink()

    return True


def _cleanup_goose(dry_run: bool) -> bool:
    """Remove Observal hook rules from ~/.agents/plugins/observal/, keeping foreign ones."""
    import shutil

    from observal_cli.harness_specs.goose_hooks_spec import hooks_file, plugin_dir
    from observal_cli.shared.utils import is_observal_hook_entry

    rprint("[cyan]Goose[/cyan]")
    path = plugin_dir()

    if not path.is_dir():
        rprint("  [dim]No observal plugin found - skipping[/dim]")
        return False

    hooks_path = hooks_file()
    data = _read_json_object(hooks_path) if hooks_path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{hooks_path} must contain a JSON object")
    existing_hooks = data.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        raise ValueError(f"{hooks_path} hooks must be a JSON object")

    # _patch_goose preserves user rules in this plugin, so cleanup must not delete them.
    foreign = {}
    for event, rules in existing_hooks.items():
        if not isinstance(rules, list):
            continue
        kept = [
            rule
            for rule in rules
            if isinstance(rule, dict)
            and not any(is_observal_hook_entry(h) for h in rule.get("hooks", []) if isinstance(h, dict))
        ]
        if kept:
            foreign[event] = kept

    if foreign:
        verb = "Would remove" if dry_run else "Removed"
        rprint(f"  {verb} Observal hooks from {esc(hooks_path)} (kept {len(foreign)} foreign event(s))")
        if not dry_run:
            atomic_write(hooks_path, json.dumps({**data, "hooks": foreign}, indent=2) + "\n")
        return True

    verb = "Would remove" if dry_run else "Removed"
    rprint(f"  {verb} {esc(path)}")
    if not dry_run:
        shutil.rmtree(path)

    return True


_VALID_HARNESSES = get_valid_harnesses()


# ── Patch command ────────────────────────────────────────────


@doctor_app.command(name="patch")
def doctor_patch(
    all_harnesses: bool = typer.Option(False, "--all-harnesses", help="Target every registered harness"),
    harness: list[str] = typer.Option([], "--harness", "-i", help="Target a harness (repeatable)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing"),
    output: OutputMode = typer.Option("table", "--output", "-o"),
):
    """Install Observal-managed session telemetry for selected harnesses.

    Examples:
      observal doctor patch --all-harnesses --dry-run
      observal doctor patch --harness claude-code
      observal doctor patch --all-harnesses --output json
    """
    output = _value(output)
    all_harnesses = _value(all_harnesses)
    harness = _value(harness)
    dry_run = _value(dry_run)
    if all_harnesses and harness:
        fail(
            ErrorCategory.VALIDATION,
            "Choose either --all-harnesses or --harness, not both.",
            operation="Patch Doctor instrumentation",
            resource="harness selection",
            remediation="Remove one of the conflicting target options.",
        )
    if not all_harnesses and not harness:
        fail(
            ErrorCategory.VALIDATION,
            "Doctor patch requires a target harness.",
            operation="Patch Doctor instrumentation",
            resource="harness selection",
            remediation="Add --all-harnesses or at least one --harness.",
        )
    targets = list(_VALID_HARNESSES) if all_harnesses else _valid_targets(list(harness), "Patch Doctor instrumentation")

    cfg = config.load()
    if not cfg.get("server_url"):
        fail(
            ErrorCategory.AUTH,
            "Observal authentication is not configured.",
            operation="Patch Doctor instrumentation",
            resource="CLI configuration",
            remediation="Run `observal auth login` and retry.",
        )

    with _capture(output):
        rprint("[bold]Observal Doctor: Patch[/bold]\n")
        result = _patch_targets(targets, dry_run=dry_run, output=output)
        if dry_run:
            rprint("\n[yellow]Dry run, no changes made.[/yellow]")
        elif result["changed"]:
            rprint("\n[green]✓ Patch complete.[/green] Restart your harness sessions to pick up changes.")
        else:
            rprint("\n[dim]Everything already up to date.[/dim]")
    if output == "json":
        output_json(result)


def _patch_claude_code(dry_run: bool) -> bool:
    """Install session push hooks into ~/.claude/settings.json."""
    optic.trace("dry_run={}", dry_run)
    from observal_cli import settings_reconciler

    rprint("[cyan]Claude Code - session push hooks[/cyan]")

    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        settings_path.parent.mkdir(parents=True, exist_ok=True)

    desired_hooks = get_desired_hooks()

    # No env vars needed for session push - config lives in ~/.observal/config.json
    changes = settings_reconciler.reconcile(desired_hooks, {}, dry_run=dry_run)

    if changes:
        for c in changes:
            rprint(f"  {esc(c)}")
        return True
    else:
        rprint("  [dim]Already up to date[/dim]")
        return False


def _patch_kiro(dry_run: bool) -> bool:
    """Repair UUID-attributed hooks for Kiro agents in the current registry lockfile."""
    from observal_cli.lockfile import read_registry_lockfile

    optic.trace("dry_run={}", dry_run)
    rprint("[cyan]Kiro - session push hooks[/cyan]")
    _, registry = read_registry_lockfile()
    agents = registry.get("harnesses", {}).get("kiro", {}).get("agents", [])
    changed = False
    hook_dirs: set[Path] = set()

    if not agents:
        rprint("  [dim]No locked Kiro agents[/dim]")
    else:
        ensure_loaded()
        adapter = get_adapter("kiro")
        for entry in agents:
            local_name = entry.get("local_name") or entry.get("slug") or entry.get("name")
            agent_id = str(entry.get("id") or "")
            if not local_name or not agent_id:
                continue
            if entry.get("scope") == "user":
                profile = Path.home() / ".kiro" / "agents" / f"{local_name}.json"
            else:
                directory = entry.get("directory")
                if not directory:
                    continue
                profile = Path(directory) / ".kiro" / "agents" / f"{local_name}.json"
            if not profile.exists():
                rprint(f"  [yellow]Missing locked profile: {esc(profile)}[/yellow]")
                continue
            current = _read_json_object(profile)
            desired = adapter.rewrite_agent_profile(json.loads(json.dumps(current)), agent_id=agent_id)
            if desired != current:
                changed = True
                verb = "Would repair" if dry_run else "Repaired"
                rprint(f"  {verb} {esc(profile)}")
                if not dry_run:
                    atomic_write(profile, json.dumps(desired, indent=2) + "\n")

            # The standalone v1 hooks file is what Kiro IDE 1.0 and CLI 3.0
            # actually read. One file per scope, shared by every agent there.
            hook_dirs.add(profile.parent.parent)

    # Kiro reads ~/.kiro/hooks/observal.json for *every* session, whichever agent
    # is active, so the user-scope file has to exist even when the lockfile holds
    # no usable Kiro agents - otherwise the harness emits no telemetry at all.
    user_config_dir = Path.home() / ".kiro"
    if user_config_dir.is_dir():
        hook_dirs.add(user_config_dir)

    for hook_dir in sorted(hook_dirs):
        if _patch_kiro_hook_file(hook_dir, dry_run):
            changed = True

    if not changed:
        rprint("  [dim]Already up to date[/dim]")
    return changed


def _patch_kiro_hook_file(config_dir: Path, dry_run: bool) -> bool:
    """Write/refresh <config_dir>/hooks/observal.json in the v1 schema."""
    from observal_cli.harness_specs.kiro_hooks_spec import (
        KIRO_V1_HOOK_FILENAME,
        merge_kiro_hooks_file,
    )

    hooks_file = config_dir / "hooks" / KIRO_V1_HOOK_FILENAME
    try:
        current = json.loads(hooks_file.read_text())
    except (json.JSONDecodeError, OSError):
        current = None
    if not isinstance(current, dict):
        current = None
    desired = merge_kiro_hooks_file(current)
    if desired == current:
        return False
    verb = "Would write" if dry_run else "Wrote"
    rprint(f"  {verb} {esc(hooks_file)}")
    if not dry_run:
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hooks_file, json.dumps(desired, indent=2) + "\n")
    return True


def _patch_cursor(dry_run: bool) -> bool:
    """Install session push hooks into ~/.cursor/hooks.json."""
    optic.trace("dry_run={}", dry_run)
    import sys

    rprint("[cyan]Cursor - session push hooks[/cyan]")

    hooks_path = Path.home() / ".cursor" / "hooks.json"
    if not hooks_path.parent.is_dir():
        rprint("  [dim]No ~/.cursor/ directory - skipping[/dim]")
        return False

    # Use the current interpreter (from the observal CLI's venv) so that
    # httpx and other dependencies are available when Cursor fires the hook.
    cmd = f"{sys.executable} -m observal_cli.hooks.session_push --harness cursor"

    desired = {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [{"command": cmd, "type": "command"}],
            "stop": [{"command": cmd, "type": "command"}],
        },
    }

    # Load existing hooks.json if present
    existing = {}
    if hooks_path.exists():
        existing = _read_json_object(hooks_path)

    # Check if already patched
    existing_hooks = existing.get("hooks", {})
    needs_update = False

    for event in ("beforeSubmitPrompt", "stop"):
        entries = existing_hooks.get(event, [])
        has_observal = any("hooks.session_push --harness cursor" in e.get("command", "") for e in entries)
        if not has_observal:
            needs_update = True
            break

    if not needs_update:
        rprint("  [dim]Already up to date[/dim]")
        return False

    # Merge: keep existing non-Observal hooks, add ours
    merged_hooks = existing_hooks.copy()
    for event, desired_entries in desired["hooks"].items():
        current = merged_hooks.get(event, [])
        # Remove old Observal hooks
        cleaned = [
            h
            for h in current
            if "cursor_session_push" not in h.get("command", "") and "session_push" not in h.get("command", "")
        ]
        merged_hooks[event] = cleaned + desired_entries

    result = {"version": 1, "hooks": merged_hooks}

    if not dry_run:
        atomic_write(hooks_path, json.dumps(result, indent=2) + "\n")

    verb = "Would install" if dry_run else "Installed"
    rprint(f"  {verb} hooks in {esc(hooks_path)}")
    return True


def _patch_antigravity(dry_run: bool) -> bool:
    """Install session push hooks into ~/.gemini/config/hooks.json."""
    from observal_cli.harness_specs.antigravity_hooks_spec import (
        _OBSERVAL_HOOK_NAME,
        build_antigravity_hooks,
    )
    from observal_cli.shared.utils import resolve_antigravity_config_dir

    rprint("[cyan]Antigravity - session push hooks[/cyan]")

    config_dir = resolve_antigravity_config_dir()
    if config_dir is None:
        rprint("  [dim]No ~/.gemini/config/ directory - skipping[/dim]")
        return False

    hooks_path = config_dir / "hooks.json"
    desired = build_antigravity_hooks()

    existing: dict = {}
    if hooks_path.exists():
        existing = _read_json_object(hooks_path)

    if _OBSERVAL_HOOK_NAME in existing:
        rprint("  [dim]Already up to date[/dim]")
        return False

    existing.update(desired)
    if not dry_run:
        atomic_write(hooks_path, json.dumps(existing, indent=2) + "\n")

    verb = "Would install" if dry_run else "Installed"
    rprint(f"  {verb} hooks in {esc(hooks_path)}")
    return True


_PI_ACTION_VERBS = {
    "install": ("Would install", "Installed"),
    "refresh": ("Would update", "Updated"),
    "adopt": ("Would record install metadata for", "Recorded install metadata for"),
    "migrate": ("Would migrate", "Migrated"),
    "restore": ("Would restore", "Restored"),
    "dedupe": ("Would remove the duplicate", "Removed the duplicate"),
}


def _patch_pi(dry_run: bool) -> bool:
    """Install or refresh the bundled telemetry extension, when npm isn't configured."""
    optic.trace("dry_run={}", dry_run)
    rprint("[cyan]Pi - session telemetry extension[/cyan]")

    status = pi_extension.check_status()
    result = pi_extension.install_or_refresh(dry_run=dry_run, status=status)
    action, backup = result.action, result.backup

    if not result.changed:
        if status.state == pi_extension.NOT_DETECTED:
            rprint("  [dim]No ~/.pi/agent/ directory - skipping[/dim]")
        elif status.state in (pi_extension.NPM_CURRENT, pi_extension.NPM_UNPINNED, pi_extension.NPM_STALE):
            rprint("  [dim]npm:observal-pi is configured - Observal does not install a local extension[/dim]")
            if status.message:
                rprint(f"  [yellow]{esc(status.message)}[/yellow]")
        elif status.state == pi_extension.UNMANAGED:
            rprint(f"  [yellow]{esc(status.message)}[/yellow]")
        else:
            rprint("  [dim]Already up to date[/dim]")
        return False

    would_verb, done_verb = _PI_ACTION_VERBS[action]
    verb = would_verb if dry_run else done_verb
    rprint(f"  {verb} {esc(pi_extension.extension_path())}")
    if backup is not None:
        kept = "Would keep" if dry_run else "Kept"
        rprint(f"  [dim]{kept} the previous file at {esc(backup)}[/dim]")
    if not dry_run and action != "adopt":
        rprint("  [dim]Restart pi or run /reload to activate[/dim]")
    if action == "dedupe" and not dry_run:
        # The duplicate warning absorbed any stale-npm reminder. Now that the
        # local copy is gone, re-read the state so that reminder still lands.
        remaining = pi_extension.check_status()
        if remaining.message:
            rprint(f"  [yellow]{esc(remaining.message)}[/yellow]")
    return True


def _patch_codex(dry_run: bool) -> bool:
    """Install session push hooks into ~/.codex/hooks.json and enable codex_hooks flag."""
    optic.debug("_patch_codex: dry_run={}", dry_run)
    from observal_cli.harness_specs.codex_hooks_spec import build_codex_hooks

    rprint("[cyan]Codex - session push hooks[/cyan]")

    codex_dir = Path.home() / ".codex"
    hooks_path = codex_dir / "hooks.json"
    config_path = codex_dir / "config.toml"

    desired = build_codex_hooks()

    # Load existing hooks.json if present
    existing: dict = {}
    if hooks_path.exists():
        existing = _read_json_object(hooks_path)

    # Check if already patched
    existing_hooks = existing.get("hooks", {})
    needs_update = False

    for event in ("UserPromptSubmit", "Stop"):
        groups = existing_hooks.get(event, [])
        has_observal = any(
            "hooks.session_push --harness codex" in h.get("command", "")
            for g in groups
            if isinstance(g, dict)
            for h in g.get("hooks", [])
            if isinstance(h, dict)
        )
        if not has_observal:
            needs_update = True
            break

    # Also check if codex_hooks flag needs enabling
    needs_flag = False
    if config_path.exists():
        content = config_path.read_text()
        if "codex_hooks" not in content or "codex_hooks = false" in content:
            needs_flag = True
    else:
        needs_flag = True

    if not needs_update and not needs_flag:
        rprint("  [dim]Already up to date[/dim]")
        return False

    # Merge hooks: preserve non-Observal hooks, add ours
    if needs_update:
        merged_hooks = existing_hooks.copy()
        for event, desired_groups in desired["hooks"].items():
            current = merged_hooks.get(event, [])
            # Remove old Observal hook groups
            cleaned = [
                g
                for g in current
                if not isinstance(g, dict)
                or not any("session_push" in h.get("command", "") for h in g.get("hooks", []) if isinstance(h, dict))
            ]
            merged_hooks[event] = cleaned + desired_groups

        result = {"hooks": merged_hooks}

        if not dry_run:
            codex_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(hooks_path, json.dumps(result, indent=2) + "\n")

        verb = "Would install" if dry_run else "Installed"
        rprint(f"  {verb} hooks in {esc(hooks_path)}")

    # Enable codex_hooks flag in config.toml
    if needs_flag:
        if not dry_run:
            codex_dir.mkdir(parents=True, exist_ok=True)
            if config_path.exists():
                content = config_path.read_text()
                if "codex_hooks = false" in content:
                    content = content.replace("codex_hooks = false", "codex_hooks = true")
                elif "codex_hooks" not in content:
                    content = f"codex_hooks = true\n{content}"
                atomic_write(config_path, content)
            else:
                atomic_write(config_path, "codex_hooks = true\n")

        verb = "Would enable" if dry_run else "Enabled"
        rprint(f"  {verb} codex_hooks flag in {esc(config_path)}")

    return True


def _patch_copilot(dry_run: bool) -> bool:
    """Install session push hooks for Copilot (VS Code).

    1. Installs hooks at .github/hooks/observal.json (project-level)
    2. Installs run_hook.ps1 wrapper script
    """
    optic.debug("_patch_copilot: dry_run={}", dry_run)
    from observal_cli.harness_specs.copilot_hooks_spec import build_copilot_hooks, build_copilot_run_hook_ps1

    rprint("[cyan]Copilot (VS Code) - session push hooks[/cyan]")

    any_changes = False

    # ── Part 1: Install hooks at .github/hooks/observal.json ──
    hooks_dir = Path.cwd() / ".github" / "hooks"
    hooks_path = hooks_dir / "observal.json"

    desired = build_copilot_hooks()

    existing: dict = {}
    if hooks_path.exists():
        existing = _read_json_object(hooks_path)

    existing_hooks = existing.get("hooks", {})
    needs_hook_update = False

    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        entries = existing_hooks.get(event, [])
        has_observal = any(
            "run_hook.ps1" in e.get("command", "") or "copilot_cli_session_push" in e.get("command", e.get("bash", ""))
            for e in entries
        )
        if not has_observal:
            needs_hook_update = True
            break

    if needs_hook_update:
        merged_hooks = existing_hooks.copy()
        for event, desired_entries in desired["hooks"].items():
            current = merged_hooks.get(event, [])
            cleaned = [
                h
                for h in current
                if "run_hook.ps1" not in h.get("command", "")
                and "copilot_cli_session_push" not in h.get("command", h.get("bash", ""))
                and "session_push" not in h.get("command", h.get("bash", ""))
            ]
            merged_hooks[event] = cleaned + desired_entries

        result = {"version": 1, "hooks": merged_hooks}

        if not dry_run:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(hooks_path, json.dumps(result, indent=2) + "\n")

        verb = "Would install" if dry_run else "Installed"
        rprint(f"  {verb} hooks in {esc(hooks_path)}")
        any_changes = True
    else:
        rprint("  [dim]Hooks already up to date[/dim]")

    # ── Part 1b: Install run_hook.ps1 wrapper ──
    ps1_path = hooks_dir / "run_hook.ps1"
    # Resolve the Windows Python path for the PS1 wrapper.
    # On Windows: sys.executable is already correct.
    # On WSL/Linux: the PS1 runs on Windows, so we need the Windows-side
    # uv tools Python path. Detect WSL and resolve accordingly.
    import sys as _sys

    if _sys.platform == "win32":
        python_path = _sys.executable
    else:
        # Running from WSL/Linux: resolve Windows uv tools path
        # Standard location: %APPDATA%/uv/tools/observal-cli/Scripts/python.exe
        # In WSL this maps to /mnt/c/Users/<user>/AppData/Roaming/uv/tools/...
        import os

        # Try to find the Windows user profile via environment or /mnt/c/Users
        win_appdata = os.environ.get("APPDATA_WIN", "")
        if not win_appdata:
            # Infer from WSL mount: find the Windows username
            # Check if running under /mnt/c/Users/<name>/...
            cwd_str = str(Path.cwd())
            if "/mnt/c/Users/" in cwd_str:
                win_user = cwd_str.split("/mnt/c/Users/")[1].split("/")[0]
                python_path = f"C:\\Users\\{win_user}\\AppData\\Roaming\\uv\\tools\\observal-cli\\Scripts\\python.exe"
            else:
                # Fallback: use bare 'python' and hope it's on Windows PATH
                python_path = "python"
        else:
            python_path = f"{win_appdata}\\uv\\tools\\observal-cli\\Scripts\\python.exe"

    ps1_content = build_copilot_run_hook_ps1(python_path)

    needs_ps1_update = True
    if ps1_path.exists():
        existing_ps1 = ps1_path.read_text()
        if "hooks.session_push --harness copilot" in existing_ps1:
            needs_ps1_update = False

    if needs_ps1_update:
        if not dry_run:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(ps1_path, ps1_content)
        verb = "Would install" if dry_run else "Installed"
        rprint(f"  {verb} PowerShell wrapper at {esc(ps1_path)}")
        any_changes = True

    return any_changes


def _patch_copilot_cli(dry_run: bool) -> bool:
    """Install session push hooks into ~/.copilot/hooks/observal.json."""
    optic.debug("_patch_copilot_cli: dry_run={}", dry_run)
    from observal_cli.harness_specs.copilot_cli_hooks_spec import build_copilot_cli_hooks

    rprint("[cyan]Copilot CLI - session push hooks[/cyan]")

    hooks_dir = Path.home() / ".copilot" / "hooks"
    hooks_path = hooks_dir / "observal.json"

    desired = build_copilot_cli_hooks()

    # Load existing hook file if present
    existing: dict = {}
    if hooks_path.exists():
        existing = _read_json_object(hooks_path)

    # Check if already patched
    existing_hooks = existing.get("hooks", {})
    needs_update = False

    for event in ("sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse"):
        entries = existing_hooks.get(event, [])
        has_observal = any("hooks.session_push --harness copilot-cli" in e.get("bash", "") for e in entries)
        if not has_observal:
            needs_update = True
            break

    if not needs_update:
        rprint("  [dim]Already up to date[/dim]")
        return False

    # Merge: keep existing non-Observal hooks, add ours
    merged_hooks = existing_hooks.copy()
    for event, desired_entries in desired["hooks"].items():
        current = merged_hooks.get(event, [])
        # Remove old Observal hooks
        cleaned = [
            h
            for h in current
            if "copilot_cli_session_push" not in h.get("bash", "") and "session_push" not in h.get("bash", "")
        ]
        merged_hooks[event] = cleaned + desired_entries

    result = {"version": 1, "hooks": merged_hooks}

    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(hooks_path, json.dumps(result, indent=2) + "\n")

    verb = "Would install" if dry_run else "Installed"
    rprint(f"  {verb} hooks in {esc(hooks_path)}")
    return True


def _patch_opencode(dry_run: bool) -> bool:
    """Install observal telemetry plugin into ~/.config/opencode/plugins/."""
    optic.debug("_patch_opencode: dry_run={}", dry_run)
    from observal_cli.harness_specs.opencode_hooks_spec import get_plugin_source

    rprint("[cyan]OpenCode - telemetry plugin[/cyan]")

    plugins_dir = Path.home() / ".config" / "opencode" / "plugins"
    plugin_path = plugins_dir / "observal-plugin.ts"

    plugin_source = get_plugin_source()
    desired_hash = hashlib.sha256(plugin_source.encode()).hexdigest()
    existing_hash = None
    if plugin_path.exists():
        existing_hash = hashlib.sha256(plugin_path.read_bytes()).hexdigest()
        if existing_hash == desired_hash:
            rprint("  [dim]Already installed[/dim]")
            return False

    if not dry_run:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(plugin_path, plugin_source)

    verb = (
        "Would update"
        if dry_run and existing_hash
        else "Would install"
        if dry_run
        else "Updated"
        if existing_hash
        else "Installed"
    )
    rprint(f"  {verb} plugin at {esc(plugin_path)}")
    return True


def _patch_goose(dry_run: bool) -> bool:
    """Install the Observal hook plugin into ~/.agents/plugins/observal/."""
    optic.debug("_patch_goose: dry_run={}", dry_run)
    from observal_cli.harness_specs.goose_hooks_spec import (
        build_hooks,
        build_plugin_manifest,
        hooks_file,
        manifest_file,
    )
    from observal_cli.shared.utils import (
        is_observal_hook_entry,
        resolve_goose_config_dir,
        resolve_goose_data_dir,
    )

    rprint("[cyan]Goose - session push hooks[/cyan]")

    if not resolve_goose_config_dir().is_dir() and not resolve_goose_data_dir().is_dir():
        rprint("  [dim]Goose not detected - skipping[/dim]")
        return False

    hooks_path = hooks_file()
    manifest_path = manifest_file()
    desired = build_hooks()["hooks"]

    existing = _read_json_object(hooks_path) if hooks_path.exists() else {}
    if not isinstance(existing, dict):
        raise ValueError(f"{hooks_path} must contain a JSON object")
    existing_hooks = existing.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        raise ValueError(f"{hooks_path} hooks must be a JSON object")

    # Keep any hook rules the user added to this plugin, replace only ours.
    merged = dict(existing_hooks)
    for event, rules in desired.items():
        kept = [
            rule
            for rule in merged.get(event, [])
            if isinstance(rule, dict)
            and not any(is_observal_hook_entry(h) for h in rule.get("hooks", []) if isinstance(h, dict))
        ]
        merged[event] = kept + rules

    manifest = build_plugin_manifest()
    manifest_current = _load_json(manifest_path) if manifest_path.exists() else None
    if merged == existing_hooks and manifest_current == manifest:
        rprint("  [dim]Already up to date[/dim]")
        return False

    if not dry_run:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
        atomic_write(hooks_path, json.dumps({**existing, "hooks": merged}, indent=2) + "\n")

    verb = "Would install" if dry_run else "Installed"
    rprint(f"  {verb} hook plugin at {esc(hooks_path.parent.parent)}")
    return True
