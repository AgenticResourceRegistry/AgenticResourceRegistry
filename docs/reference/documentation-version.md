---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Documentation and software versions"
description: "Distinguish development guides from published releases and match your CLI to your registry."
---

This documentation revision follows the **1.17 development line**. Its workflow checks use `1.17.0-dev.0`; it is not a certification that every command or behavior is available in the latest published release.

## Joining an existing registry

Ask your administrator which server release your team runs. Check your CLI with:

```bash
observal --version
```

Login checks CLI/server version compatibility and prints a matching upgrade or downgrade command when needed. Follow that instruction instead of assuming the newest CLI is the right one. For exact options, use the help output of that installed version.

The standalone installers and `uv tool install observal-cli` default to published artifacts. They do not promise to install this development revision. Installing a release and then following development-only instructions can leave a version or feature mismatch.

## Evaluating the development version

The [local source-evaluation guide](/getting-started/local-evaluation) builds the server and installs the CLI from the same checkout. Record that checkout's commit when testing, so a later change to `main` does not silently change the software you evaluated.

A source install is for development evaluation, not a reason to upgrade a team server without its administrator's approval.

## Reading examples

An **agent version** such as `1.0.0` belongs to a resource, not the CLI or server. Select and inspect the resource release independently of the platform version. Hook-specification metadata is another distinct version: it records the managed hook layout, not a model or coding-tool release.

These guides distinguish code-checked behavior from completed live verification. Support for a harness does not certify every future version of that external tool.

## Publication boundary

Before this draft documentation becomes a stable-release reference, maintainers must choose the target release, verify the core workflows against its artifacts, and confirm the Mintlify publishing configuration. Until that decision is made, do not label development verification as stable-release acceptance.
