-- SPDX-FileCopyrightText: 2026 Observal Contributors
-- SPDX-License-Identifier: Apache-2.0
-- Where an agent hook (binding_agent <> '') is placed decides when it can run:
-- 'frontmatter' in the agent file, which Claude Code runs only in interactive
-- sessions, or 'gated_settings' in settings.json behind the agent gate
-- (agent pull --hooks=settings), which runs whenever the agent is active,
-- headless included. Every extraction made before gated placement existed
-- is a frontmatter placement. Ignored for standalone hooks (binding_agent = '').
ALTER TABLE layer_components ADD COLUMN IF NOT EXISTS binding_placement LowCardinality(String) DEFAULT 'frontmatter';
