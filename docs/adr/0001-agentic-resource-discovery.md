<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ADR 0001: Adopt Agentic Resource Discovery (ARD) as the discovery envelope

**Status:** Accepted
**Date:** 2026-09-20
**Deciders:** Observal maintainers

## Context

Observal is install-first: a person builds an Agent from registry components,
publishes it, and pulls it into a harness before a session starts. That model
is right for repeatable, governed workflows and wrong for the moment a
developer is mid-task and needs a capability that already exists in the
registry. It also means the assistant only knows about resources someone
installed ahead of time, so it rebuilds things that exist and tells users a
capability is unavailable when it is one search away.

The Agentic Resource Discovery specification (ARD) defines a common way to
describe a resource and a common search API so any client can ask "what
exists for this task?" across registries. It deliberately does **not** define
installation, invocation, authentication, review, or telemetry. Observal
already has all of those. The fit is: ARD says what exists, Observal says how
to use it safely.

This record fixes the decisions the discovery work depends on so later phases
can be built against a stable target. The full plan lives outside the repo;
this ADR is the part that must not drift.

## Decision 1: Pin the specification

Observal implements **ARD v0.91 (Proposal)** as published at commit
`b76f235a8f461876ad4f1e77abd0eb0eb302b48d` of
[`ards-project/ard-spec`](https://github.com/ards-project/ard-spec)
(2026-09-12). The JSON Schema, JSON-LD context, CDDL grammar, OpenAPI file and
the official conformance CLI from that commit are vendored unmodified under
`observal-server/vendor/ard/` (see `VENDORED.md` there for provenance and the
update procedure).

The spec is a proposal and will change. Observal tracks it by re-vendoring at
a new commit, updating this ADR, and re-running the conformance tests, never
by hand-editing vendored files or silently accepting whatever the live site
says.

## Decision 2: Native models stay authoritative; ARD is a projection

The existing `Agent`/`AgentVersion`, `McpListing`/`McpVersion`,
`SkillListing`/`SkillVersion`, hook, prompt and sandbox tables remain the
source of truth for installation, lifecycle, review and ownership. Discovery
adds a read model, `discovery_entries`, populated from approved versions and
refreshed on lifecycle events. Nothing that exists today is replaced or
repurposed. The `/api/v1/agents` and component contracts are unchanged.

## Decision 3: Identifier scheme

Every native resource gets exactly one permanent ARD identifier:

```
urn:air:<deployment-domain>:<kind>:<uuid>
```

- `<deployment-domain>` is the host of the deployment's public URL
  (`deployment.public_url` dynamic setting), which satisfies ARD's requirement
  that the publisher segment be a fully qualified domain name.
- `<kind>` is one of `agent`, `mcp`, `skill`, `hook`, `prompt`, `sandbox` and
  occupies ARD's namespace segment.
- `<uuid>` is the native listing's primary key and occupies ARD's name segment.

Example: `urn:air:observal.acme.com:skill:5f2c1a9e-8b1d-4d0c-9c4e-1f2a3b4c5d6e`

**Why the UUID and not `namespace/slug`.** ARD requires the identifier to be
a stable handle that survives the resource moving. Observal's `namespace/slug`
changes on rename and on `transfer-owner`. The UUID never changes. The
human-readable identity is still carried, as `obs:nativeRef`
(`namespace/slug@version`), so clients can pivot to existing commands.

**The publisher domain is pinned.** It is derived from `deployment.public_url`
the first time a real fully qualified domain is seen and stored in the
`discovery.publisher_domain` setting; after that, identifiers do not follow the
public URL if the deployment moves hosts. Until a real domain is configured,
identifiers use the placeholder `observal.local` and are expected to change
once. Changing the pinned setting deliberately renames every identifier and is
an administrator action. The artifact URL, by contrast, is a location and
always follows the current public URL. Single-label hosts and IP addresses are
never accepted as publishers.

Externally ingested entries keep the identifier their publisher assigned.
Observal never rewrites another publisher's URN.

## Decision 4: Endpoint base and well-known paths

```
GET  /.well-known/ard.json               manifest of public, approved entries
GET  /.well-known/ai-catalog.json        predecessor path, same content
POST /api/v1/ard/search                  ARD Search
GET  /api/v1/ard/agents                  ARD List (deterministic browse)
POST /api/v1/ard/explore                 ARD Explore (facets), later phase
GET  /api/v1/ard/entries/{identifier}    fetch one full entry (Observal-specific)
GET  /api/v1/artifacts/{kind}/{uuid}/{version}
                                         permanent versioned artifact with digest
```

The registry advertises `https://<deployment>/api/v1/ard` as its operational
base through an entry of type `application/ai-registry+json` in the manifest.

ARD defines no operation for fetching a complete entry by identifier; clients
are expected to fetch the artifact `url`. Observal adds `/entries/{identifier}`
for its own CLI and UI. It is not part of the conformance surface.

## Decision 5: `federation` when omitted

The spec's default for the `federation` field is `auto`. Observal honours that
literally: an omitted field is treated as `auto`, **bounded by the
administrator's upstream allowlist**. With the default empty allowlist this is
functionally local-only. When an admin allows upstreams, `auto` and
`referrals` only ever reach those.

Rejected alternative: default to `none`. That would make a conformant client
receive different behaviour than the spec promises, and the privacy goal is
achieved just as well by an empty allowlist.

Search text may contain sensitive information (incident details, unreleased
names, internal service names). It never leaves the deployment unless an admin
has allowed a specific upstream, and results from upstreams always carry their
`source`.

## Decision 6: Visibility

Discovery narrows the signed-in user's registry view to their own namespace
and teamspaces they currently belong to. This is intentionally stricter than
registry browsing/install for public resources in other namespaces: a public
resource can be installed by reference without appearing in a user's ARD
search. The same scope applies to search, list, inspect, and versioned artifact
reads, including imported A2A agents. Membership is checked at query time, so
removing a member revokes their ARD access to that team's public and private
entries. Submitter status alone does not grant access to an old teamspace.

Two filters apply, in order:

1. **Who can see the listing** — ordinary signed-in users see personal listings
   they own (public or private) and listings in *all* teamspaces where they are
   current members (public or private). Global reviewers retain their public
   review scope; admins and super-admins still see everything. Anonymous calls
   retain the public-catalog behavior described below.
2. **Which lifecycle states are returned** — approved versions to everyone
   who passes filter 1; pending and rejected versions only to users whose
   effective component permission is `owner` (the same rule install uses for
   owner fallback); archived entries excluded unless the query filters on
   `obs:lifecycle` explicitly.

Unauthenticated requests receive only public, approved entries, and only when
the registry-wide `deployment.public_registry_enabled` setting is on (default
off), the same switch that governs anonymous browsing elsewhere. When it is
off, unauthenticated search returns an empty result set and the well-known
manifest carries only the registry's own `application/ai-registry+json` entry,
both of which are conformant. The well-known manifest is always computed as an
unauthenticated request.

## Decision 7: Media types and compatibility inputs

Emitted (canonical) types:

| Observal resource | ARD `type` |
|---|---|
| Skill | `application/ai-skill+md` |
| MCP server | `application/mcp-server-card+json` (a descriptor, not the live stream) |
| Prompt | `application/vnd.observal.prompt+json` |
| Sandbox | `application/vnd.observal.sandbox+json` |
| Hook | `application/vnd.observal.hook+json` |
| Agent | `application/vnd.observal.agent+json` |
| External, unknown | preserved exactly as published |

An Observal Agent is an installable configuration, not a running service, so it
is never emitted as `application/a2a-agent-card+json`. (ADR 0002 adds remote A2A
agents as imported entries of that type, and delegation to both kinds.)

Accepted and normalised on input, with the original preserved in `raw_entry`:

| Input | Normalised to |
|---|---|
| `urn:ai:…` | `urn:air:…` |
| `application/mcp-server`, `application/mcp-server+json` | `application/mcp-server-card+json` |
| `application/ai-skill` | `application/ai-skill+md` |
| `/.well-known/ai-catalog.json`, `rel="ai-catalog"` | treated as `/.well-known/ard.json`, `rel="ard"` |

## Decision 8: Relevance is the only thing in `score`

ARD's `score` (0–100) is semantic relevance and nothing else. Approval,
publisher verification, liveness, harness compatibility and adoption are
returned as separate, named fields (`obs:approval`, `obs:trust`,
`obs:availability`, `obs:supportedHarnesses`, …) and are never blended into
`score`. Policy eligibility is applied as a filter before ranking, not as a
weight inside it.

## Decision 9: What is deliberately deferred

- **Web ingestion.** ARD §5.2 says every registry MUST crawl entry sources.
  Observal defers this to the external-sources phase. The vendored conformance
  tool passes without it; until that phase ships, Observal is an ARD
  *publisher* and *search endpoint*, not a complete ARD *registry*, and says
  so here rather than claiming otherwise.
- **Explore.** Optional in the spec; added with search-quality work.
- **Trust manifest verification.** Entries Observal publishes carry a
  `trustManifest.identity` of `https://<publisher-domain>` (an HTTPS FQDN URI
  string, `identityType: "https"`) bound to the URN's publisher segment.
  Verification of third-party manifests arrives with external ingestion.

## Decision 10: Search baseline needs no external service

The first search implementation is PostgreSQL full-text search plus `pg_trgm`
(already enabled). It must remain a complete, supported configuration. Dense
retrieval is an optional later addition behind a provider interface and an
explicit infrastructure change (pgvector); its absence never disables
discovery.

## Consequences

- Discovery reaches all ten harnesses through the bundled skill and the CLI,
  which already ship to every harness on login. No new runtime is required
  for the first milestone.
- Per-harness behaviour (how MCP is installed, whether tools refresh
  mid-session, whether a hook can inject context) is recorded as verified
  facts in the shared harness registry and tested, so later phases gate on
  data rather than assumptions.
- Anything that permanently changes a developer's setup, or that can write or
  delete, keeps asking first. Approved skills and prompts, being text, may be
  loaded automatically.
- Conformance is tested in CI against the vendored tool at the pinned
  commit. A spec change is a deliberate re-vendor, reviewed like any other
  dependency update.
