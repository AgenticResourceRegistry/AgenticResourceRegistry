-- SPDX-FileCopyrightText: 2026 Observal Contributors
-- SPDX-License-Identifier: Apache-2.0
-- Hook evidence: a verified hook's location_sha256 is the digest of the
-- (event, command) Claude Code records when it runs, and binding_agent names
-- the agent whose frontmatter installed it ('' for a settings-file hook).
-- Agent-scoped hooks run only while that agent is active.
ALTER TABLE layer_components ADD COLUMN IF NOT EXISTS binding_agent String DEFAULT '';
