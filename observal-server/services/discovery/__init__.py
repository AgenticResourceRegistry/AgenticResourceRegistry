# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Discovery: project native registry resources into ARD entries and search them.

Modules:

- ``identity``    identifiers, media types, compatibility normalisation
- ``adapters``    per-kind projection of native rows into neutral fields + artifacts
- ``projection``  build/refresh/tombstone ``discovery_entries`` rows
- ``visibility``  who may see which entries (mirrors the registry's rules)
- ``search``      SQL prefilter + deterministic Python ranking
- ``serialize``   ARD response shapes
- ``hooks``       session listener that reprojects on lifecycle changes

See ``docs/adr/0001-agentic-resource-discovery.md`` for the decisions.
"""
