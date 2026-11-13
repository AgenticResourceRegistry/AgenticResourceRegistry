---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Version and reuse components"
description: "Publish a reviewed successor without silently changing existing agent installations."
---

Version a component when its instructions, command, template, artifact, or declared metadata changes. An approved release is not a mutable shared configuration file.

## Before changing a release

Read the existing release, its identity, content, and consumers. Record the base version, decide what changes, and inspect the new candidate before review. Fork when you want an independently owned resource; publish a successor when maintaining the same listing.

```bash
observal registry version list prompt YOUR_NAMESPACE/summary-template --output json
```

Use the actual type (`mcp`, `skill`, `hook`, `prompt`, or `sandbox`). For full content of an exact release, use the corresponding [component-version inspection endpoint](/guides/inspect-an-agent#inspect-pinned-components), not only a version-list summary.

## Publish a candidate version

For a prompt example:

```bash
observal registry version publish prompt YOUR_NAMESPACE/summary-template --version 1.1.0 --description 'Ask for a concise checklist' --changelog 'Template output changed' --extra '{"template":"Summarize {{topic}} as a concise checklist."}' --output json
```

Replace the identity/version and check type-specific allowed fields. This command **creates a version for review**; the word `publish` does not mean approval and public availability have completed.

Existing metadata can be inherited. Inspect the persisted candidate's full fields instead of assuming every omitted value came from the base you intended or every field was replaced. Review changed instructions/files, declarations, and prerequisites, then [approve and publish the exact revision](/guides/review-and-publish).

For complete skill folders, use the exact-base successor and version-UUID workflow in [skill folders](/skill-folder-workflow). Do not use a generic version operation to bypass folder review, resource limits, or rollout gates.

## Known install-response limitation

The current source verification found an older-prompt case where an agent lock retained prompt `1.0.0`, but the server's `component_pins` response reported the listing's newer `1.1.0`. In that saved response, the generated configuration still contained the old template. This narrows the observation to a metadata/lock disagreement for that response; it does not certify every selected-version field or component type.

The CLI refused the disagreement before writing generated files. Its response-consistency check runs outside the `--strict` condition: removing `--strict` or passing `--no-strict` does not bypass it. This is an unresolved server consistency issue, not a strict-only refusal.

If you encounter “Server component pin disagrees with the install lock,” stop and have the selected-version response investigated. A deliberately reviewed new agent release can adopt a newer component, but doing that does not repair the old release's install response or justify silently moving its pin.

## Existing agents keep their pins

A new component release does not update an already published agent. To adopt it, change the consuming agent definition explicitly:

```bash
observal agent add prompt COMPONENT_UUID --version COMPONENT_VERSION --dir ./new-agent
```

This `add` example is for a component not already in that definition. Duplicate references are not an update mechanism: edit the existing component's version in the YAML when it is already present, then build and release the agent through its supported lifecycle.

Review component changes alongside the agent's version. Use `agent release` for an approved agent rather than editing it in place. An explicit pin wins over a generic “latest” assumption; [agent version and pin reference](/cli/agent#release-and-versions) explains refresh behavior.

## Consumers also have a lock

A project's `observal.lock` pins its chosen agent release. Publishing new components or agents does not silently move a plain pull past that lock. Upgrade only after inspecting the selected release and use explicit version/upgrade options, with strict integrity checks where applicable.

Standalone component commands have their own version/preview/update contracts; not every command has the same flags or pin behavior. Preserve managed receipts and local edits when updating skills. Do not erase lockfiles to make an incompatible upgrade appear successful.

## Verify the result

Check the version history/publication state, exact candidate content, consuming agent lock/pins, generated install plan, and a controlled runtime task. Version publication and file installation are not evidence of observed use or usefulness. [Insights interpretation](/guides/interpret-insights) keeps those distinctions explicit.

## Next

[Component authoring](/guides/components), [agent authoring](/guides/create-an-agent), and the [version-command reference](/cli/component).
