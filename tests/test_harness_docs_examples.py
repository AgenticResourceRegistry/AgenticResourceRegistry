# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Execute the guide's teaching examples without touching installed harnesses."""

import ast
import inspect
import json
import re
import sys
import textwrap
import types
from pathlib import Path

import pytest

GUIDE = Path(__file__).resolve().parents[1] / "docs/adding-a-harness.md"
EXAMPLES = [
    textwrap.dedent(block).strip() for block in re.findall(r"```python\n(.*?)\n\s*```", GUIDE.read_text(), re.S)
]


def _example(marker: str) -> str:
    matches = [example for example in EXAMPLES if marker in example]
    assert len(matches) == 1, f"Expected one guide example containing {marker!r}"
    return matches[0]


def _sample_tests() -> list[str]:
    tree = ast.parse(_example("class TestMyHarnessAdapter:"))
    return [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]


@pytest.mark.parametrize("example", EXAMPLES, ids=[f"python-example-{index}" for index in range(len(EXAMPLES))])
def test_python_example_compiles(example):
    # The registry example is an entry to insert into an existing dictionary.
    source = "{" + example + "}" if example.startswith('"my-harness":') else example
    compile(source, str(GUIDE), "exec")


@pytest.fixture
def sample_adapter(monkeypatch):
    from observal_cli import harness
    from observal_shared.harness_registry import HARNESS_REGISTRY

    entry = ast.literal_eval("{" + _example('"display_name": "My harness"') + "}")
    monkeypatch.setitem(HARNESS_REGISTRY, "my-harness", entry["my-harness"])
    # Example registration must not replace real adapters for other tests.
    monkeypatch.setattr(harness, "_ADAPTER_REGISTRY", {})
    module = types.ModuleType("observal_cli.harness_specs.my_harness_hooks_spec")
    exec(_example('"""Hook specification for My harness.'), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    namespace = {}
    exec(_example("class MyHarnessAdapter(BaseAdapter):"), namespace)
    exec(_example("class TestMyHarnessAdapter:"), namespace)
    return namespace["MyHarnessAdapter"](), namespace["TestMyHarnessAdapter"]()


@pytest.mark.parametrize("name", _sample_tests())
def test_embedded_adapter_test_passes(name, sample_adapter, tmp_path):
    _, sample_tests = sample_adapter
    method = getattr(sample_tests, name)
    if inspect.signature(method).parameters:
        method(tmp_path)
    else:
        method()


def test_generated_example_hooks_match_declared_events(sample_adapter, tmp_path):
    from observal_shared.harness_registry import HARNESS_REGISTRY

    adapter, _ = sample_adapter
    config = adapter.generate_hook_config("http://localhost:8000", "")
    events = adapter.get_hook_spec().events
    event_map = HARNESS_REGISTRY["my-harness"]["hook_events_map"]
    assert set(config["hooks"]) == {event_map[event] for event in events}
    (tmp_path / "settings.json").write_text(json.dumps(config))
    assert adapter.detect_hooks(tmp_path) == "installed"


def test_server_example_generates_profile_and_mcp_entries(monkeypatch):
    from services import harness
    from services.harness import ConfigContext

    monkeypatch.setattr(harness, "_ADAPTER_REGISTRY", {})
    namespace = {}
    exec(_example("class MyHarnessAdapter(BaseHarnessAdapter):"), namespace)
    output = harness.get_adapter("my-harness").format_config(
        ConfigContext(
            agent=None,
            safe_name="demo",
            harness="my-harness",
            observal_url="",
            mcp_configs={"demo": {"command": "example-program"}},
            rules_content="Example instructions",
        )
    )
    assert output["agent_profile"] == {
        "path": ".my-harness/agents/demo.md",
        "content": "Example instructions",
    }
    assert output["mcp_config"] == {
        "path": ".my-harness/mcp.json",
        "content": {"mcpServers": {"demo": {"command": "example-program"}}},
    }
