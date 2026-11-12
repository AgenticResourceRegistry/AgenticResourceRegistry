---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Adding a New Harness to Agentic Resource Registry"
---

This guide covers everything needed to add full harness support. Agentic Resource Registry manages
five component types: **MCP servers**, **skills**, **hooks**, **prompts**, and
**sandboxes**. The registry selects installable types through each harness's capabilities;
sandboxes use the MCP delivery capability. Each harness needs
scanning (discovery), config generation (install), hook instrumentation (telemetry),
and session parsing (reconciliation).

## Overview: What "Supporting a harness" Means

When a user runs `observal agent pull <agent>`, Agentic Resource Registry writes harness-specific files:

| Component | What gets written | Example |
|-----------|------------------|---------|
| MCP servers | Native JSON/TOML config with direct commands or URLs | `.cursor/mcp.json` |
| Skills | Markdown skill files in harness's skill directory | `.claude/skills/my-skill/SKILL.md` |
| Hooks | Native lifecycle hook config or extension-managed registry hooks | `settings.json` hooks section |
| Prompts | Native prompt files where supported | `.github/prompts/my-prompt.prompt.md` |
| Sandboxes | MCP gateway entry dispatching through `observal-sandbox-run` | `observal-sandbox` in MCP config |

Installation capabilities and scan coverage describe separate operations.
The legacy `scan_home()` and `scan_project()` methods report inventories by
adapter and scope: Kiro's home scan covers MCPs, skills, hooks and agents, while
its project scan covers MCPs. Codex, Cursor and Copilot legacy scans cover MCPs;
Pi covers MCPs and skills. For read-only inventory discovery, use
`observal scan --inventory`, powered by `discover_home()` / `discover_project()`.
Plain `observal scan` also applies legacy migrations. Use the capability matrix
below to determine install support independently of scan results.

## Current registered capabilities

The source of truth is `capabilities` in
`packages/observal-shared/observal_shared/harness_registry.py`. Capability flags
define supported operations; paths and adapter methods implement them. Keep this
table and the
[supported coding tools overview](/integrations) in sync when capabilities change.

| Harness | Registry ID | MCP servers | Skills | Hooks | Native prompts |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `claude-code` | Yes | Yes | Yes | No |
| Kiro | `kiro` | Yes | Yes | Yes | No |
| Cursor | `cursor` | Yes | No | Yes | No |
| Codex CLI | `codex` | Yes | Yes | Yes | No |
| Copilot | `copilot` | Yes | Yes | Yes | Yes |
| Copilot CLI | `copilot-cli` | Yes | Yes | Yes | Yes |
| OpenCode | `opencode` | Yes | Yes | Yes | No |
| Antigravity | `antigravity` | Registered only | Registered only | Registered only | No |
| Goose | `goose` | Yes | Yes | Yes | No |
| Pi | `pi` | Yes | Yes | Yes | No |

**Kiro supports skills** at `.kiro/skills/{name}/SKILL.md` and
`~/.kiro/skills/{name}/SKILL.md`. Its generated agent profile includes both skill
resource globs; see the [Kiro integration guide](/integrations/kiro).
Cursor currently enables MCP servers and hooks; skill installation is outside
its declared capabilities, even though the registry includes skill paths.

The table describes the current registry. Individual integration guides document
native activation, supported formats and tool-version requirements.
[Antigravity's compatibility hold](/integrations/antigravity) tracks verification
of its generated formats and native activation. Validate sandbox execution,
headless delegation, session delivery and component evidence as separate parts
of an integration. All ten entries resolve a server session parser; Copilot
shares the Copilot CLI parser. Component insights distinguish **unsupported**
evidence coverage from measured zero usage.

## File Checklist

| # | File | What it does |
|---|------|-------------|
| 1 | `packages/observal-shared/observal_shared/harness_registry.py` | Shared harness metadata: paths, keys, event maps, formats |
| 2 | `observal_cli/harness/<harness_name>.py` | CLI adapter: scanning, hook detection, session source resolution/discovery, managed file attribution |
| 3 | `observal_cli/harness/load_all.py` | Add import line for auto-registration |
| 4 | `observal_cli/harness/__init__.py` | Adapter registry and protocol validation |
| 5 | `observal-server/services/harness/<harness_name>.py` | Server adapter: config generation for install |
| 6 | `observal-server/services/harness/load_all.py` | Add import line for server adapter |
| 7 | `observal_cli/harness_specs/<harness_name>_hooks_spec.py` | Hook spec where needed; Cursor uses its adapter and Pi uses `packages/pi-extension/` |
| 8 | `observal-server/services/session_parsers/<harness_name>.py` | Server session parser for the native source format (may reuse an existing parser) |
| 9 | `observal_cli/hooks/<harness_name>_session_push.py` | Optional bridge for host-specific responses; otherwise use the shared session push hook |
| 10 | `observal_cli/cmd_doctor.py` | Doctor diagnose, patch, and cleanup implementations for the new harness |
| 11 | `observal_cli/layer.py` | Layer scanning globs (`HARNESS_LAYER_CONFIGS`) and active harness detection |
| 12 | `tests/test_cli_harness_adapters.py` | Adapter unit tests |
| 13 | `/api/v1/config/harnesses` consumers | Frontend uses server harness metadata through `useHarnesses()` |

## Step 1: Research the harness

Before writing code, document these for the target harness:

**MCP configuration:**
- Where does the harness look for MCP server config? (path, format: JSON/TOML/YAML)
- What's the top-level key? (`mcpServers`, `servers`, `mcp`, etc.)
- Which transports does it support: stdio, SSE, Streamable HTTP?
- Home-level config path vs project-level config path?

**Skills:**
- Does the harness have a skill/rules/instruction file concept?
- What format? (Markdown with YAML frontmatter, plain markdown, MDC, JSON)
- Where do skill files live? (project path, user/global path)

**Prompts:**
- Does the harness load native prompt files? Where, and in what format?
- Distinguish standalone prompts from instructions embedded in an agent profile.
- Declare `prompts` only when the adapter implements the native delivery path.

**Hooks:**
- Does the harness fire lifecycle events? (tool use, session start/stop, errors)
- How are hooks registered? (JSON config, settings file, plugin system)
- What events are available? Map them to Agentic Resource Registry's canonical events:
  - `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `UserPromptSubmit`, `SubagentStop`
- Does the harness support command hooks, HTTP hooks, or plugin hooks?

**Sessions:**
- Does the harness write session logs? (JSONL, SQLite, custom format)
- Where are session files stored?
- What's the schema? (messages, tool calls, thinking blocks)

**Sandboxes:**
- Sandboxes use MCP configuration delivery. Verify the gateway, runner, runtime,
  project access and isolation using the [sandbox contracts](/sandboxes).

## Step 2: Add Harness Registry Entry

Add one entry to `packages/observal-shared/observal_shared/harness_registry.py`.
Both the CLI and server import this shared registry.

```python
"my-harness": {
    "display_name": "My harness",
    "capabilities": {"hooks", "mcp_servers", "skills"},
    "session_parser": "my-harness",       # must resolve to a registered server parser
    "scopes": ["project", "user"],
    "default_scope": "project",
    "scope_labels": ("project (.my-harness/)", "user (~/.my-harness/)"),

    # MCP config
    "mcp_config": {
        "project": ".my-harness/mcp.json",
        "user": "~/.my-harness/mcp.json",
    },
    "mcp_servers_key": "mcpServers",
    "home_mcp_config": "~/.my-harness/mcp.json",

    # Skills
    "skills": {
        "project": ".my-harness/skills/{name}/SKILL.md",
        "user": "~/.my-harness/skills/{name}/SKILL.md",
    },
    "skill_format": "yaml_frontmatter",

    # Rules/Agent files
    "agent_profile": {
        "project": ".my-harness/agents/{name}.md",
        "user": "~/.my-harness/agents/{name}.md",
    },
    "agent_profile_format": "yaml_frontmatter",

    # Hooks
    "hook_type": "command",           # "command", "http", or "plugin"
    "hooks": {
        "project": ".my-harness/settings.json",
        "user": "~/.my-harness/settings.json",
    },
    "hook_scripts_dir": ".my-harness/hooks",
    "hook_events_map": {
        "PreToolUse": "preToolUse",
        "PostToolUse": "postToolUse",
        "Stop": "sessionEnd",
        "SessionStart": "sessionStart",
        "UserPromptSubmit": "beforeSubmitPrompt",
    },

    "config_dir": ".my-harness",

    # Verified runtime facts (discovery and activation gate on these).
    # Only set a non-default value when you have checked the harness's own
    # docs or code, and record the evidence in a comment.
    "mcp_install_mode": "file",          # file | setup_command | adapter | user_only
    "dynamic_tools": False,              # refreshes MCP tools mid-session (verify at runtime)
    "prompt_context_injection": False,   # a hook/extension can add context before the model answers
    "guidance_file_write": False,        # always False: the bundled skill is the instruction channel
    "headless_run": False,               # enable only after proving non-interactive CLI execution
}
```

The runtime facts are checked by `tests/test_harness_registry.py`, which holds an
explicit table of verified values. Add your harness to that table in the same
change, and make sure `mcp_install_mode` agrees with the `mcp_config` paths
(`setup_command` means no paths, `user_only` means only a user path).
Enable `headless_run` alongside tested `headless_command()` and
`parse_headless_output()` implementations in the CLI adapter. Delegation selects
harnesses with this verified execution path; Copilot (VS Code) and Goose currently
support agent installation without headless delegation.

## Step 2.5: Update Doctor and Layer Scan (required)

Before moving on, always wire the new harness into these shared paths:

- `observal_cli/cmd_doctor.py`:
  - Add `_check_<harness>()`, `_patch_<harness>()`, and `_cleanup_<harness>()` implementations
- `observal_cli/harness/<harness_name>.py`:
  - Delegate `patch_hooks()` and `cleanup_hooks()` to those implementations
- `observal_cli/layer.py`:
  - Add user/project file globs under `HARNESS_LAYER_CONFIGS`
- `observal_cli/harness/<harness_name>.py`:
  - Add `home_markers` for active harness detection when the harness has a reliable home config marker. Glob patterns are supported.
  - Add `managed_agent_profiles`, `managed_skills`, and `managed_mcp_files` patterns for layer source attribution
  - Override `get_observal_managed_files()` only if simple `{name}` patterns are not enough

These integrations complete the harness workflow: pull and scan, doctor diagnostics,
hook installation and cleanup, and layer observability.

## Step 3: Create CLI Adapter (Scanning)

Create `observal_cli/harness/my_harness.py`. The example below covers legacy
scanning and hook detection; implement its hook/agent helpers for the native
format. Also implement `discover_home()` and `discover_project()` for bounded,
read-only inventory. Use `RichAdapterScanner` in
`observal_cli/discovery/adapter_support.py` and existing adapters as references;
the base discovery methods return empty results until the adapter implements
them. Test both scan interfaces and their scope-specific coverage.

```python
# SPDX-FileCopyrightText: 2026 Your Name <your@email.com>
# SPDX-License-Identifier: Apache-2.0

"""My harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from observal_cli.harness import (
    DiscoveredAgent,
    DiscoveredHook,
    DiscoveredMcp,
    DiscoveredSkill,
    HookSpec,
    ScanResult,
    register_adapter,
)
from observal_cli.harness.base import BaseAdapter
from observal_cli.shared.utils import (
    _OBSERVAL_HOOK_MARKERS,
    extract_mcp_servers,
    first_content_line,
    parse_frontmatter_field,
)


class MyHarnessAdapter(BaseAdapter):
    home_markers = (".my-harness",)
    managed_agent_profiles = ("user:agents/{name}.md", "project:.my-harness/agents/{name}.md")
    managed_skills = ("user:skills/{name}/SKILL.md", "project:.my-harness/skills/{name}/SKILL.md")
    managed_mcp_files = ("user:mcp.json", "project:.my-harness/mcp.json")

    @property
    def harness_name(self) -> str:
        return "my-harness"

    # ── Scanning ──────────────────────────────────────────────

    def scan_home(self, home: Path | None = None) -> ScanResult:
        """Discover MCPs, skills, hooks, agents from ~/.my-harness/"""
        home = home or Path.home()
        harness_dir = home / ".my-harness"
        if not harness_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps(harness_dir / "mcp.json", "my-harness:global")
        skills = self._scan_skills(harness_dir / "skills")
        hooks = self._scan_hooks(harness_dir / "settings.json")
        agents = self._scan_agents(harness_dir / "agents")

        return ScanResult(mcps=mcps, skills=skills, hooks=hooks, agents=agents)

    def scan_project(self, project_dir: Path) -> ScanResult:
        """Discover MCPs, skills from .my-harness/ in a project."""
        harness_dir = project_dir / ".my-harness"
        if not harness_dir.exists():
            return ScanResult()

        mcps = self._scan_mcps(harness_dir / "mcp.json", "my-harness:project")
        skills = self._scan_skills(harness_dir / "skills")
        return ScanResult(mcps=mcps, skills=skills)

    # ── Hook detection ────────────────────────────────────────

    def get_hook_spec(self) -> HookSpec:
        return HookSpec(
            events=["PreToolUse", "PostToolUse", "Stop"],
            format="command",
            markers=["observal", "OBSERVAL"],
        )

    def generate_hook_config(
        self,
        observal_url: str,
        api_key: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate the hooks config dict to write into settings."""
        # Import from your hook spec module
        from observal_cli.harness_specs.my_harness_hooks_spec import build_hooks
        return build_hooks()

    def detect_hooks(self, config_dir: Path) -> str:
        """Check if Observal hooks are installed. Return 'installed'|'partial'|'missing'."""
        settings = config_dir / "settings.json"
        if not settings.exists():
            return "missing"
        try:
            data = json.loads(settings.read_text())
        except (json.JSONDecodeError, OSError):
            return "missing"
        hooks = data.get("hooks", {})
        if not hooks:
            return "missing"
        found = 0
        for _evt, entries in hooks.items():
            if isinstance(entries, list):
                for h in entries:
                    if isinstance(h, dict) and any(
                        m in h.get("command", "") for m in _OBSERVAL_HOOK_MARKERS
                    ):
                        found += 1
                        break
        return "installed" if found >= 3 else ("partial" if found > 0 else "missing")

    # ── Private helpers ───────────────────────────────────────

    def _scan_mcps(self, mcp_file: Path, source: str) -> list[DiscoveredMcp]:
        if not mcp_file.exists():
            return []
        try:
            data = json.loads(mcp_file.read_text())
            servers = extract_mcp_servers(data)
            return [
                DiscoveredMcp(
                    name=name,
                    command=cfg.get("command"),
                    args=cfg.get("args", []),
                    url=cfg.get("url"),
                    description=f"My harness MCP: {name}",
                    source=source,
                )
                for name, cfg in servers.items()
            ]
        except (json.JSONDecodeError, OSError):
            return []

    def _scan_skills(self, skills_dir: Path) -> list[DiscoveredSkill]:
        if not skills_dir.is_dir():
            return []
        skills = []
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            name = skill_md.parent.name
            desc = ""
            try:
                content = skill_md.read_text()
                desc = parse_frontmatter_field(content, "description") or ""
                if not desc:
                    desc = first_content_line(content)
            except OSError:
                pass
            skills.append(
                DiscoveredSkill(
                    name=name,
                    description=desc or f"Skill: {name}",
                    source="my-harness:skills",
                )
            )
        return skills

    def _scan_hooks(self, settings_file: Path) -> list[DiscoveredHook]:
        """Discover installed hooks from settings."""
        # Implement based on harness's hook format
        return []

    def _scan_agents(self, agents_dir: Path) -> list[DiscoveredAgent]:
        """Discover agent definitions."""
        # Implement based on harness's agent file format
        return []


register_adapter(MyHarnessAdapter())
```

## Step 4: Create Server-Side Config Generator (Install)

Create `observal-server/services/harness/my_harness.py`. This generates files when
users run `observal agent pull` or install an agent. The minimal example below
shows profile and MCP output. Extend it with skill and hook files/configuration
and verify native loading when enabling those capabilities. Use `ctx.options`
to select scope-aware paths in the complete adapter.
For native prompts, implement `emits_prompt_files()` and prompt-file generation;
Copilot and Copilot CLI are the current reference adapters.

```python
# SPDX-FileCopyrightText: 2026 Your Name <your@email.com>
# SPDX-License-Identifier: Apache-2.0

"""My harness server-side config generator."""

from __future__ import annotations

from services.harness import BaseHarnessAdapter, ConfigContext, register_adapter


class MyHarnessAdapter(BaseHarnessAdapter):
    @property
    def harness_name(self) -> str:
        return "my-harness"

    def format_config(self, ctx: ConfigContext) -> dict:
        """Generate profile and MCP entries for this example."""
        return {
            "agent_profile": {
                "path": f".my-harness/agents/{ctx.safe_name}.md",
                "content": ctx.rules_content,
            },
            "mcp_config": {
                "path": ".my-harness/mcp.json",
                "content": {"mcpServers": ctx.mcp_configs},
            },
        }


register_adapter(MyHarnessAdapter())
```

## Step 5: Create Hook Spec

Use the harness's native mechanism. Eight harnesses have modules under
`observal_cli/harness_specs/`; Cursor installs hooks through its adapter/doctor
implementation, and Pi runs registry hooks and telemetry through the bundled
`packages/pi-extension/`. Both integrate directly with doctor without a separate
hook spec module.

For a command-hook harness that needs a spec, create
`observal_cli/harness_specs/my_harness_hooks_spec.py`. This defines what
hooks `observal doctor patch` installs:

```python
# SPDX-FileCopyrightText: 2026 Your Name <your@email.com>
# SPDX-License-Identifier: Apache-2.0

"""Hook specification for My harness."""


def build_hooks() -> dict:
    """Return the hooks config to merge into the harness's settings.

    This is called by doctor patch and by the adapter's generate_hook_config().
    The format must match what the harness expects in its hook config file.
    """
    return {
        "hooks": {
            "preToolUse": [
                {
                    "type": "command",
                    "command": "python -m observal_cli.hooks.session_push --harness my-harness",
                }
            ],
            "postToolUse": [
                {
                    "type": "command",
                    "command": "python -m observal_cli.hooks.session_push --harness my-harness",
                }
            ],
            "sessionEnd": [
                {
                    "type": "command",
                    "command": "python -m observal_cli.hooks.session_push --harness my-harness",
                }
            ],
        }
    }
```

## Step 6: Add the Session Source Adapter and Parser (required)

Transport and parsing are intentionally separate. The CLI adapter locates raw source records; it must not normalize them. The server parser remains harness-specific and converts stored raw rows into frontend events.

For a JSONL harness, implement the session methods on the existing `HarnessAdapter` in `observal_cli/harness/my_harness.py`:

```python
from observal_cli.harness import SessionSource


def resolve_session_source(self, event: dict, home: Path | None = None) -> SessionSource | None:
    # Resolve the exact session ID, source path, cwd, and optional parent ID.
    ...

def discover_session_sources(self, home: Path | None = None, since_hours: int = 168) -> list[SessionSource]:
    # Used by background recovery and `observal reconcile`.
    ...

def is_session_final(self, event: dict) -> bool:
    ...
```

Use `related_session_sources()` for separately stored subagents, `session_extra_fields()` for durable metadata such as credits, and `session_extra_records()` only for actual synthetic source records. Reuse `observal_cli.hooks.session_push --harness my-harness`; create a bridge module only when the host requires special stdout or runtime behavior. Do not add another cursor, direct POST path, or harness-specific reconcile scanner.

The shared engine reads complete records, spools them before network delivery, retries stable source indexes, advances only after a contiguous acknowledgement, recovers from the server checkpoint, and hashes full history only during final audit.

Implement the independent server parser in `observal-server/services/session_parsers/my_harness.py`. It should handle user and assistant messages, tool calls/results, reasoning blocks, errors, and boundaries. Register it in `observal-server/services/session_parsers/__init__.py`; the harness registry's `session_parser` value must match its ID.

For a non-JSONL host, add a native source/exporter only when the supported harness actually requires one. It must persist pending indexed records before delivery and obey the same acknowledgement/checkpoint/final-hash protocol. OpenCode and Pi are the reference native implementations.

## Step 7: Configure the Shared Session Hook

Point generated hook commands at:

```text
python -m observal_cli.hooks.session_push --harness my-harness
```

Command-hook bridges route recovery and delivery through the shared Python
engine. Native extensions, such as Pi, implement the same durable outbox,
acknowledgement, checkpoint and final-audit protocol in their host runtime.

## Step 8: Register Everything

1. `observal_cli/harness/load_all.py`:
   ```python
   from observal_cli.harness import my_harness as _my_harness  # noqa: F401
   ```

2. `observal_cli/harness/<harness_name>.py`: set `home_markers` and managed file attribution patterns used by layer snapshots.

3. `observal-server/services/harness/load_all.py`:
   ```python
   from services.harness import my_harness as _my_harness  # noqa: F401
   ```

4. `observal_cli/cmd_scan.py`: add to `_HARNESS_HOME_DIRS`:
   ```python
   "my-harness": "~/.my-harness",
   ```

## Step 9: Tests

The examples below cover legacy scan, hook detection and managed files.
Complete coverage also includes bounded `discover_home()` / `discover_project()`
inventory, capability gating, scope-aware server output, hook patch/cleanup,
session source discovery, parsing and recovery. Add activation fixtures and
headless/evidence tests for the capabilities you enable.

```python
class TestMyHarnessAdapter:
    def test_scan_home_empty(self, tmp_path):
        adapter = MyHarnessAdapter()
        result = adapter.scan_home(tmp_path)
        assert result.mcps == []
        assert result.skills == []

    def test_is_installed_uses_home_marker(self, tmp_path):
        adapter = MyHarnessAdapter()
        assert adapter.is_installed(tmp_path) is False
        (tmp_path / ".my-harness").mkdir()
        assert adapter.is_installed(tmp_path) is True

    def test_scan_home_discovers_mcps(self, tmp_path):
        harness_dir = tmp_path / ".my-harness"
        harness_dir.mkdir()
        (harness_dir / "mcp.json").write_text('{"mcpServers": {"srv": {"command": "npx"}}}')
        adapter = MyHarnessAdapter()
        result = adapter.scan_home(tmp_path)
        assert len(result.mcps) == 1
        assert result.mcps[0].name == "srv"

    def test_scan_home_discovers_skills(self, tmp_path):
        skill_dir = tmp_path / ".my-harness" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\ndescription: Does stuff\n---\n")
        adapter = MyHarnessAdapter()
        result = adapter.scan_home(tmp_path)
        assert len(result.skills) == 1

    def test_scan_project_discovers_mcps(self, tmp_path):
        (tmp_path / ".my-harness").mkdir()
        (tmp_path / ".my-harness" / "mcp.json").write_text('{"mcpServers": {"p": {"command": "node"}}}')
        adapter = MyHarnessAdapter()
        result = adapter.scan_project(tmp_path)
        assert len(result.mcps) == 1

    def test_detect_hooks_missing(self, tmp_path):
        adapter = MyHarnessAdapter()
        assert adapter.detect_hooks(tmp_path) == "missing"

    def test_detect_hooks_installed(self, tmp_path):
        (tmp_path / "settings.json").write_text(json.dumps({
            "hooks": {
                "preToolUse": [{"command": "python -m observal_cli.hooks.session_push --harness my-harness"}],
                "postToolUse": [{"command": "python -m observal_cli.hooks.session_push --harness my-harness"}],
                "sessionEnd": [{"command": "python -m observal_cli.hooks.session_push --harness my-harness"}],
            }
        }))
        adapter = MyHarnessAdapter()
        assert adapter.detect_hooks(tmp_path) == "installed"

    def test_managed_files_for_layer_source_attribution(self):
        lockfile = {
            "harnesses": {
                "my-harness": {
                    "agents": [{"name": "agent-one", "components": [{"type": "skill", "name": "helper"}]}],
                }
            }
        }
        adapter = MyHarnessAdapter()
        assert adapter.get_observal_managed_files(lockfile) == {
            "user:agents/agent-one.md",
            "project:.my-harness/agents/agent-one.md",
            "user:skills/helper/SKILL.md",
            "project:.my-harness/skills/helper/SKILL.md",
        }
```

## Step 10: Verify

Run from the repository root after installing the development dependencies:

```bash
# Registry, constants, adapters and config generation
(cd observal-server && uv run pytest ../tests/test_harness_registry.py ../tests/test_constants_sync.py ../tests/test_cli_harness_adapters.py ../tests/test_agent_config_generator.py -q)

# Read-only discovery for your registered harness
observal scan --inventory --harness my-harness

# Preview installation and hook configuration
observal agent pull YOUR_NAMESPACE/YOUR_AGENT --harness my-harness --dry-run
observal doctor patch --harness my-harness --dry-run

# Preview recovery of an unfinished session fixture
observal reconcile --harness my-harness --dry-run
```

Replace the harness ID and agent reference with your registered integration and
a test agent. Then apply the install and hook configuration in a disposable test
workspace, activate the agent in its native harness, and run a small task.
Reconcile the fixture without `--dry-run` against a test server and verify delivery
and checkpoint advancement. Preview commands validate the plan; the live task
and recovery checks validate execution.

## Component evidence (MCPs, skills and hooks)

MCP, skill and hook projectors all publish through `EvidenceSpec` in `services/component_activity/evidence_projector.py`. An activity job shares one bounded initial source snapshot across supported extractors; each attempted complete publication still reloads the source and mapping to reject changes during processing. Identity-conflict zero-row publications also perform a fresh source and conflict-state check. Read-side generation and projection-state SQL builders live in `services/component_activity/queries.py`.

Component Insights count skill and hook evidence only where it is proven for the harness. Connect `skill_evidence_extractor` or `hook_evidence_extractor` in the registry to its implementation in `services/session_parsers/`. Validate each (type × harness) cell using the following requirements and recorded, sanitized sessions in `tests/fixtures/component_insights/<harness>/`:

1. **A verified binding.** The CLI adapter verifies the installed component against the file the harness actually loads (`skill_manifest_path`, `skill_shadow_paths`, `skill_location` for skills; `verify_hook_binding` for hooks). Anything it cannot rule out, such as an unreadable config, a duplicate entry or a same-named copy elsewhere, must leave the component unverified. Add the harness to `SKILL_VERIFICATION_HARNESSES` or `HOOK_VERIFICATION_HARNESSES` in `observal_cli/layer.py` so the result is bound into the layer identity.
2. **An observable identity.** The transcript must name the same identity the verifier recorded: the exact `SKILL.md` location for skills, the exact event and command for hooks. Typed or pasted text must not be able to produce it; rely on harness-written fields.
3. **Declared limits.** The extractor declares which kinds it can observe (`observed_kinds`). For hooks it also declares whether silent successes are recorded (`records_silent_success`) and whether agent-scoped hooks run headless (`agent_hooks_run_headless`). Unknown is reported as not recorded or not eligible, never as zero.
4. **When it could run.** For hooks, prove from recordings when an installed hook can run at all (settings versus agent placement, interactive versus headless) before counting a session in the denominator. The verifier reports each agent hook's placement (`hook_placement`, stored as `layer_components.binding_placement`): `frontmatter` hooks follow `agent_hooks_run_headless`, while Claude Code's opt-in `gated_settings` hooks (`agent pull --hooks=settings`) can run whenever their agent is active. If a subagent's own transcript doesn't name its agent, set `HookSession.subagent` and, when every record carries the same harness-written parent session and subagent ids, `parent_session_id` and `subagent_id`. The hook projector then names the agent from the parent session's record of the spawn, but only if the extractor also implements `subagent_results`, `agent_tool_calls` and `resolve_agent` (see `claude_code_hook_evidence.py`). Otherwise those sessions stay `agent_unknown`. Pi has no native command hooks: its registry hooks run inside the Agentic Resource Registry extension, whose run receipts are the identity (`pi_hook_evidence.py`). They carry no `hook_agent`, because the verifier only reports them verified while the active profile's hooks file names that agent, so every session that verifies one is eligible.

Enable evidence coverage once all four requirements are verified; until then,
the cell reports `unsupported` rather than a usage count.

### Changing an evidence rule on existing data

Sessions are projected once per publication version. Changing an extractor or matcher does not touch sessions that were already projected unless you bump a version, and each version does something different:

| Constant | Where | What a bump does |
|---|---|---|
| `PROJECTION_VERSION`, `MATCHER_VERSION`, `SKILL_MATCHER_VERSION`, `HOOK_MATCHER_VERSION` | `services/component_activity/projector.py` and the per-type matcher modules | All four make up the UInt16 publication key. Bumping any one hides previous publications (reported as stale) until `replay_activity_revision` has republished historical sessions. Each value must fit 1-15; changing that range requires a schema/versioning change. |
| `CURRENT_EXTRACTOR_VERSION` | `services/layer_components/__init__.py` | Every layer snapshot is re-extracted by `backfill_layer_components` (daily, resumable). Presence is reported as stale until it is. |

Bump the relevant matcher version (or `PROJECTION_VERSION` for shared activity rules, `CURRENT_EXTRACTOR_VERSION` for layer rules) when a change would make **already published** numbers wrong in a way that matters, for example a rule that wrongly counted evidence (a false positive). The replay costs a scan of every stored session, and reports show stale coverage until it finishes, so do not bump for:

* changes that only add evidence kinds or recognise more of it: older sessions keep their smaller, still-true numbers;
* changes that only make older results more conservative than the new rule would.

Say which case applies in the constant's comment either way. Sessions recorded with a legacy layer hash can never be attributed, so a replay does not help them.

---

## Architecture Notes

**Skill file generation is shared; activation is harness-specific.** Supported
harnesses use `SKILL.md` files, with scope-specific paths from `skills` and format
from `skill_format` in the registry. The shared `generate_skill()` in
`services/config/skill_builder.py` calls the server adapter's
`skill_frontmatter_extra()` for native metadata. Each adapter connects these
files to native loading, such as Kiro's `skill://` resources. Enable the `skills`
capability alongside file generation and verified activation.

**Sandbox configuration is delivered through MCP.** The bundled
`observal-sandbox-mcp` gateway dispatches through `observal-sandbox-run`. Complete
sandbox validation covers gateway/runner installation, runtime connectivity,
required image tools, project mounts and isolation. Follow the
[sandbox contracts](/sandboxes) to verify execution alongside configuration delivery.

Other notes:

- Adapters are self-contained: one file handles scanning, hook detection, session source discovery, and managed file attribution
- Server parsers remain format-specific; transport, recovery, acknowledgement, and final audit behavior stay shared
- Shared utilities in `observal_cli/shared/utils.py`: `extract_mcp_servers`, `parse_frontmatter_field`, `_OBSERVAL_HOOK_MARKERS`
- `BaseAdapter` in `observal_cli/harness/base.py` provides feature-gating via `_check_feature()`
- `ensure_loaded()` guarantees all adapters are registered before cross-adapter operations
- MCP deduplication in scan uses first-discovered-wins
- MCP commands and remote URLs are emitted directly in each harness's native format
- Sandbox MCP delivery reuses adapter configuration support; runtime execution/project access still require separate verification
- Skills use the harness's native skill/rule file format, resolved from `skills` and `skill_format` in the registry
