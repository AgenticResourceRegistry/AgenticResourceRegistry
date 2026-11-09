---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Publish and render a prompt"
description: "Create a reviewed text template, supply variables, and check the rendered result."
---

A prompt component is a reusable text template. Rendering substitutes variables; it does not run a model or automatically activate the template in a live coding session.

## Before you start

Choose a specific purpose and input variables. Own or represent the content you submit, and keep credentials/private data out of the template. Registry rendering sends supplied variable values to the server; do not use sensitive examples without an appropriate data policy.

## Save a draft

```bash
observal registry prompt submit --name summary-template --version 1.0.0 --description 'A concise documentation summary' --category documentation --template 'Summarize {{topic}} in three bullets.' --draft --output json
```

JSON or plain template files are also supported through `--from-file`; a plain template file needs explicit metadata. Inspect the full returned template, category, variables, identity, and version.

```bash
observal registry prompt show YOUR_NAMESPACE/summary-template --output json
observal registry prompt submit --submit LISTING_UUID --output json
```

Follow [review/publication](/guides/review-and-publish). Saving/submitting is not proof that the release is published or the prompt is effective.

## Render an approved template

```bash
observal registry prompt render YOUR_NAMESPACE/summary-template --var topic=README.md --output json
```

Expect rendered text corresponding to `Summarize README.md in three bullets.` Check every substitution before using it. Unknown/missing variable names can leave literal placeholders; a successful response is not proof all intended inputs were supplied.

The dedicated render command does not accept a version selector in this revision. Check the listing's selected template/release before claiming reproducible standalone rendering. For reproducible agent composition, pin the exact component version instead.

## Use it deliberately

There is no standalone `registry prompt install` command. You can provide the inspected rendered text to your assistant, use the supported discovery/context workflow, or [add the prompt's exact version to an agent](/guides/components#add-an-exact-component-version-to-an-agent).

Check how that harness represents prompts. Native prompt-file support is distinct from instructions/text in an agent; do not promise an identical slash command in every tool.

## Maintain it

Use [component versioning](/guides/version-components) for changes to an approved release. A new template version does not rewrite existing agent pins, and rendered text alone is not feedback or measured effectiveness. Test the revised template with an appropriate model task before presenting it as an improvement.

Exact categories/options are in the [prompt reference](/cli/prompt).
