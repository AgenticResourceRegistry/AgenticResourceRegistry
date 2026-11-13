<!-- SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Nithin-Bhargav-07 <gaddamnithinbhargav@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Table of contents

## Guides

### Start here

* [Agentic Resource Registry](index.md)
* [Join your team's registry](getting-started/onboarding.md)
* [Install the CLI](getting-started/installation.md)
* [Pull your first agent](getting-started/quickstart.md)
* [Try a registry locally](getting-started/local-evaluation.md)
* [Core concepts](core-concepts/index.md)

### Agents and team workflows

* [Registry workflows](use-cases/index.md)
* [Inspect an agent before installing](guides/inspect-an-agent.md)
* [Create and publish an agent](guides/create-an-agent.md)
* [Share agent configurations](use-cases/share-agent-configs.md)
* [Fork and customize an agent or component](use-cases/fork-and-customize.md)
* [Work in a teamspace](use-cases/teamspaces.md)
* [Work through Inbox](guides/manage-inbox.md)
* [Sync an MCP server from GitHub or GitLab](use-cases/mcp-repo-sync.md)

### Components

* [Create and reuse components](guides/components.md)
* [Publish an MCP configuration](registry-mcp-helper.md)
* [Publish a skill](registry-skill-helper.md)
* [Publish a hook](registry-hook-helper.md)
* [Publish and render a prompt](guides/publish-a-prompt.md)
* [Publish a sandbox configuration](registry-sandbox-helper.md)
* [Version and reuse components](guides/version-components.md)

### Discovery, delegation, and insights

* [Discover resources with ARD](guides/discover-resources.md)
* [Delegate work with A2A](guides/delegate-work.md)
* [Interpret agent and component insights](guides/interpret-insights.md)

### Session evidence

* [Debug agent failures from session evidence](use-cases/debug-agent-failures.md)
* [Observe MCP activity in sessions](use-cases/observe-mcp-traffic.md)
* [Session tracking and reconciliation](core-concepts/session-tracking.md)

## Integrations

### Coding tools

* [Supported coding tools](integrations/index.md)
* [Claude Code](integrations/claude-code.md)
* [Copilot](integrations/copilot.md)
* [Use a registry agent in Copilot CLI](integrations/copilot-cli.md)
* [Cursor](integrations/cursor.md)
* [Use a registry agent in Codex](integrations/codex.md)
* [Goose](integrations/goose.md)
* [Kiro](integrations/kiro.md)
* [OpenCode](integrations/opencode.md)
* [Pi](integrations/pi.md)
* [Antigravity: check compatibility before activation](integrations/antigravity.md)

## Self-hosting & Admin

### Registry administration

* [Review and publish a submission](guides/review-and-publish.md)
* [Run a team-wide agent registry](use-cases/team-registry.md)

### Deploy a registry

* [Self-host a registry](self-hosting/index.md)
* [Requirements](self-hosting/requirements.md)
* [Run the source Compose stack](self-hosting/docker-compose.md)
* [Single-node deployment](self-hosting/single-node-deploy.md)
* [Production deployment](self-hosting/production-deploy.md)
* [AWS deployment with Terraform](self-hosting/aws-terraform.md)
* [GCP deployment with Terraform](self-hosting/gcp-terraform.md)

### Configure access and behavior

* [Configure your registry](self-hosting/configuration.md)
* [Set up authentication and SSO](self-hosting/authentication.md)
* [Usage reporting](self-hosting/usage-reporting.md)

### Operate and recover

* [Understand ports and persistent storage](self-hosting/ports-and-volumes.md)
* [Understand registry and evidence storage](self-hosting/databases.md)
* [Back up and rehearse recovery](self-hosting/backup-and-restore.md)
* [Plan and verify an upgrade](self-hosting/upgrades.md)
* [Troubleshooting](self-hosting/troubleshooting.md)

### Security

* [Security assurance case](security/assurance-case.md)
* [Verify an Agentic Resource Registry release](security/release-verification.md)

## Reference

### CLI Reference

* [Overview](cli/index.md)
* [observal api](cli/api.md)
* [Mutation retries and idempotency](cli/idempotency.md)
* [observal auth](cli/auth.md)
* [observal config](cli/config.md)
* [observal discover](cli/discover.md)
* [observal scan](cli/scan.md)
* [observal share](cli/share.md)
* [observal outdated](cli/outdated.md)
* [observal reconcile](cli/reconcile.md)
* [observal inbox](cli/inbox.md)
* [observal agent pull](cli/pull.md)
* [observal registry](cli/registry.md)
* [observal registry recommend](cli/recommend.md)
* [observal registry version](cli/component.md)
* [observal registry models](cli/models.md)
* [observal agent](cli/agent.md)
* [observal team](cli/team.md)
* [observal ops](cli/ops.md)
* [observal admin](cli/admin.md)
* observal doctor
  * [observal doctor](cli/doctor.md)
  * [observal doctor support](cli/support.md)
* [observal self](cli/self.md)
* [observal registry prompt](cli/prompt.md)
* observal server
  * [observal server](cli/server.md)
  * [observal server migrate](cli/migrate.md)
* observal registry skill
  * [observal registry skill](cli/skill.md)
  * [Complete skill folders: developer workflow](skill-folder-workflow.md)
  * [Complete-folder skill rollout (operators)](skill-folder-rollout.md)

### Reference

* [Documentation and software versions](reference/documentation-version.md)
* [Environment variables](reference/environment-variables.md)
* [API endpoints](reference/api-endpoints.md)
* [Hooks specification](reference/hooks-spec.md)
* [Config files](reference/config-files.md)

### Architecture Decisions

* [ADR 0001: Adopt Agentic Resource Discovery (ARD) as the discovery envelope](adr/0001-agentic-resource-discovery.md)
* [ADR 0002: Agent-to-agent delegation over ARD and A2A](adr/0002-a2a-delegation.md)

### Contributing

* [Code Review Standard](code-review.md)
* [Development Guide](DEVELOPMENT_GUIDE.md)
* [Testing Guide](testing/Testing_Guide.md)

### Repository resources

* [Contributing Guide](../CONTRIBUTING.md)
* [Database integration tests](testing/database-integration-tests.md)
* [Fuzzing Guide](../fuzz/README.md)
* [AI Policy](../AI_POLICY.md)
