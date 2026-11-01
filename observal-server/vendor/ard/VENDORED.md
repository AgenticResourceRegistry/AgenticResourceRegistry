<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Vendored: Agentic Resource Discovery (ARD) specification assets

| | |
|---|---|
| Upstream | https://github.com/ards-project/ard-spec |
| Pinned commit | `b76f235a8f461876ad4f1e77abd0eb0eb302b48d` |
| Upstream date | 2026-09-12 |
| Spec version | v0.91 (Proposal) |
| License | Apache-2.0 (`LICENSE` in this directory, copyright Agentic Resource Discovery project) |

## What is vendored

Copied byte-for-byte from the pinned commit, preserving the upstream layout so
the conformance tool's relative schema path (`../../spec/schemas/`) resolves:

```
spec/schemas/ard-entry.schema.json     ArdEntry / ArdManifest JSON Schema (authoritative)
spec/schemas/ai-catalog.schema.json    predecessor catalog schema (compatibility input)
spec/schemas/ard.context.jsonld        JSON-LD base context served at agenticresourcediscovery.org/context/v1
spec/schemas/ard.cddl                  structural grammar
spec/schemas/ard.openapi.yaml          registry REST API (POST /search, POST /explore, GET /agents)
conformance/bin/conformance-test       zero-dependency Python conformance CLI
LICENSE                                upstream license
```

Nothing in this directory is modified. Do not edit these files; update them by
re-vendoring (below).

## How Observal uses them

- `services/discovery/` validates outgoing ARD entries and manifests against
  `ard-entry.schema.json` in tests.
- `tests/test_ard_conformance.py` runs `conformance-test manifest` against the
  generated `/.well-known/ard.json` and `conformance-test registry` against the
  in-process API.
- The pinned version, media-type normalization table and identifier scheme are
  recorded in `docs/adr/0001-agentic-resource-discovery.md`.

## Updating

```bash
git clone --depth 1 https://github.com/ards-project/ard-spec.git /tmp/ard-spec
cd /tmp/ard-spec && git log -1 --format='%H %ad' --date=short
cp spec/schemas/{ard-entry.schema.json,ai-catalog.schema.json,ard.context.jsonld,ard.cddl,ard.openapi.yaml} \
   <repo>/observal-server/vendor/ard/spec/schemas/
cp conformance/bin/conformance-test <repo>/observal-server/vendor/ard/conformance/bin/
cp LICENSE <repo>/observal-server/vendor/ard/LICENSE
```

Then update the pinned commit and date in this file and in the ADR, re-run the
conformance tests, and review the upstream diff for changes to required terms,
the URN pattern, or the search response shape.

## Running the conformance tool by hand

```bash
python3 observal-server/vendor/ard/conformance/bin/conformance-test manifest <file-or-url>
python3 observal-server/vendor/ard/conformance/bin/conformance-test publisher <domain>
python3 observal-server/vendor/ard/conformance/bin/conformance-test registry http://localhost:8000/api/v1/ard
```

Install `jsonschema` to enable strict schema validation in manifest mode; the
tool falls back to its built-in semantic checks without it.
