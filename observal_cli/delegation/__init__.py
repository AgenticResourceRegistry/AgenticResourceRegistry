# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Agent-to-agent delegation (ADR 0002).

A running agent finds another approved agent through ARD search and hands it
one task. Every delegation is an A2A Task, whatever runs it:

- a registry Agent runs as a child process of a harness that has a verified
  headless mode, inside a throwaway git worktree; its file changes come back as
  a patch artifact and are never applied for the caller;
- a remote A2A agent is called directly over A2A JSON-RPC with the Agent Card
  a reviewer approved.

Modules:

- ``tasks``      A2A task shapes and the local task store
- ``workspace``  worktree isolation and the patch of what the child changed
- ``local``      materialize a registry Agent and run it headless
- ``a2a_client`` call a remote A2A agent
- ``runner``     the detached worker that drives one task to completion
- ``service``    find, start, wait, cancel, with depth and cycle guards
- ``mcp_server`` the observal-agents MCP server pulled agents get
"""
