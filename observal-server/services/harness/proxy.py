# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""One immutable agent version presented as an agent, for config generation."""

from models.agent import Agent, AgentVersion


class AgentVersionConfigProxy:
    """Expose one immutable version with its parent agent identity."""

    def __init__(self, agent: Agent, version: AgentVersion, components: list) -> None:
        self._agent = agent
        self._version = version
        self.components = components
        self.id = agent.id
        self.name = agent.name
        self.namespace = agent.namespace
        self.slug = agent.slug
        self.team_id = agent.team_id
        self.is_private = agent.is_private

    def __getattr__(self, name: str):
        return getattr(self._version, name)
