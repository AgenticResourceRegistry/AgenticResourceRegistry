# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Server-side verifier for the CLI/Pi v2 layer fingerprint wire contract."""

import hashlib
import json
import re
import unicodedata


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def layer_hash_v2(harnesses: dict[str, list[dict]], pins: dict) -> str:
    """Recompute a v2 hash from manifest hashes and projected registry pins only.

    Keep this independent of the CLI package, which is not installed in the API
    image. File contents and drift are intentionally excluded from identity.
    """
    files: dict[str, str] = {}
    for harness, entries in harnesses.items():
        for entry in entries:
            path = _nfc(f"{harness}/{entry['path']}").replace("\\", "/")
            digest = _nfc(entry["hash"])
            if not re.fullmatch(r"sha256-[0-9a-f]{64}", digest):
                raise ValueError("Invalid layer manifest hash")
            if path in files and files[path] != digest:
                raise ValueError("Conflicting duplicate layer manifest path")
            files[path] = digest
    file_pairs = sorted(([key, value] for key, value in files.items()), key=lambda pair: pair[0].encode("utf-8"))

    tuples: list[list[str]] = []
    for agent in pins["agents"]:
        parent_id, parent_version = agent["id"], agent["version"]
        harness, scope = agent["harness"], agent["scope"]
        tuples.append(
            [
                "agent",
                "agent",
                parent_id,
                parent_version,
                harness,
                scope,
                agent.get("local_name", ""),
                "",
                "",
                agent.get("qualified_name", ""),
                agent["name"],
            ]
        )
        for component in agent["components"]:
            tuples.append(
                [
                    "agent_component",
                    component["type"],
                    component["id"],
                    component["version"],
                    harness,
                    component["scope"],
                    component["local_name"],
                    parent_id,
                    parent_version,
                    component.get("qualified_name", ""),
                    component["name"],
                ]
            )
    for item in pins["standalone"]:
        tuples.append(
            [
                "standalone",
                item["type"],
                item["id"],
                item["version"],
                item["harness"],
                item["scope"],
                item["local_name"],
                "",
                "",
                item.get("qualified_name", ""),
                item["name"],
            ]
        )
    tuples.sort(key=lambda row: [field.encode("utf-8") for field in row])
    serialized = json.dumps(
        ["observal-layer-v2", file_pairs, tuples], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return "v2_" + hashlib.sha256(serialized).hexdigest()[:60]
