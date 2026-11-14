# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared token detection for raw preflight values and redacted review displays.

A generic sk- suffix inside an English word is not a standalone key. Strong
provider-scoped prefixes remain detected anywhere, including inside slugs.
Credential-named structured fields get the stricter generic scan as well.
"""

import re

CREDENTIAL = re.compile(
    r"(?:sk-(?:proj-|ant-)[A-Za-z0-9_-]{20,}"
    r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_GENERIC = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
_SECRET_FIELD = re.compile(r"(?:^|[_\W])(?:token|secret|password|authorization|api_?key|private_?key)(?:$|[_\W])", re.I)


def _credential_key(name: str) -> bool:
    """Recognize credential keys without treating unrelated identifiers as secrets."""
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", normalized).lower()
    return normalized in {"headers", "env", "environment", "environment_variables"} or bool(
        _SECRET_FIELD.search(normalized)
    )


def secret_paths(value: object, path: str, *, credential_context: bool = False) -> list[str]:
    """Return paths only; never put credential values or token-shaped keys in diagnostics."""
    credential_context = credential_context or _credential_key(path.rsplit(".", 1)[-1])
    if isinstance(value, str):
        return [path] if CREDENTIAL.search(value) or (credential_context and _GENERIC.search(value)) else []
    if isinstance(value, dict):
        found = []
        for key, child in value.items():
            name = str(key)
            key_path = path + ".[redacted-key]" if CREDENTIAL.search(name) else path + "." + name
            if CREDENTIAL.search(name):
                found.append(key_path)
            found.extend(secret_paths(child, key_path, credential_context=credential_context or _credential_key(name)))
        return found
    if isinstance(value, list):
        return [
            p
            for i, child in enumerate(value)
            for p in secret_paths(child, f"{path}[{i}]", credential_context=credential_context)
        ]
    return []
