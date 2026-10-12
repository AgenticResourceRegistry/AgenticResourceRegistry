<!-- SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal CLI Command Reference

Auto-generated from the Typer app by `scripts/sync_observal_skill.py`. Do not edit manually.

<!-- BEGIN AUTO-GENERATED COMMAND REFERENCE -->
Every command available in the installed CLI. This block is generated from the Typer app by `scripts/sync_observal_skill.py`. If a flag you need is missing here, run `<command> --help` for full options.

**Root commands**

- `observal api`: Call an authenticated Observal JSON API endpoint.
- `observal outdated`: Show installed agents and standalone components with their registry status.
- `observal reconcile`: Backfill local session records missed by automatic hook delivery
- `observal scan`: Show a read-only inventory of your local harness setup.

**`observal admin`**: Core administration and submission review commands

- `observal admin review`: Submission review commands
  - `observal admin review approve`: Approve a component, Agent, or bundle submission.
  - `observal admin review list`: List pending submissions awaiting review.
  - `observal admin review reject`: Reject a component, Agent, or bundle submission.
  - `observal admin review show`: Show review details for a component or Agent.
- `observal admin audit-log`: Query the compliance audit log.
- `observal admin audit-log-export`: Export the compliance audit log as CSV or JSON.
- `observal admin cache-clear`: Clear all server caches.
- `observal admin create-user`: Create a new user account. Requires admin privileges.
- `observal admin delete-user`: Delete a user account. Requires admin privileges.
- `observal admin diagnostics`: Show system diagnostics and health status.
- `observal admin recommend`: Mark or unmark an agent or component as recommended.
- `observal admin reset-password`: Reset a user's password. Requires admin privileges.
- `observal admin saml-config`: View current SAML SSO configuration.
- `observal admin saml-config-delete`: Delete SAML SSO configuration. Disables SAML SSO.
- `observal admin saml-config-set`: Create or update SAML SSO configuration.
- `observal admin scim-token-create`: Create a new SCIM provisioning token.
- `observal admin scim-token-revoke`: Revoke a SCIM provisioning token.
- `observal admin scim-tokens`: List SCIM provisioning tokens.
- `observal admin security-events`: View security events log.
- `observal admin set`: Set a server setting.
- `observal admin set-role`: Change a user's role.
- `observal admin settings`: List server settings.
- `observal admin trace-privacy`: View trace privacy setting.
- `observal admin trace-privacy-set`: Enable or disable trace privacy (redacts sensitive trace data).
- `observal admin users`: List all users.

**`observal agent`**: Agent registry commands

- `observal agent co-authors`: Manage co-authors for agents
  - `observal agent co-authors add`: Add a co-author.
  - `observal agent co-authors list`: List co-authors.
  - `observal agent co-authors remove`: Remove a co-author.
- `observal agent add`: Add a component reference to observal-agent.yaml.
- `observal agent archive`: Archive an agent.
- `observal agent build`: Validate agent definition against the server (dry-run).
- `observal agent bulk-create`: Bulk-create agents from a JSON file.
- `observal agent create`: Create a new agent (interactive wizard, from file, or via flags).
- `observal agent delete`: Archive an agent. Prefer the archive command.
- `observal agent fork`: Fork an approved agent release into your own editable draft.
- `observal agent init`: Scaffold an observal-agent.yaml definition file.
- `observal agent install`: Get install config for an agent.
- `observal agent list`: List active agents (paginated).
- `observal agent my`: List your own agents (all statuses).
- `observal agent outdated`: Show which components an agent version pins behind their latest release.
- `observal agent publish`: Publish the agent definition to the server.
- `observal agent pull`: Fetch agent config and write harness files to disk.
- `observal agent release`: Bump version and push a versioned release to the registry.
- `observal agent show`: Show full agent details.
- `observal agent transfer-owner`: Transfer ownership to another username.
- `observal agent unarchive`: Restore an archived agent back to active status.
- `observal agent versions`: List all versions for an agent.

**`observal auth`**: Authentication and account commands

- `observal auth login`: Connect to Observal.
- `observal auth logout`: Clear saved credentials.
- `observal auth whoami`: Show current authenticated user.
- `observal auth status`: Check authenticated server connectivity and local outbox health.
- `observal auth change-password`: Change your password.
- `observal auth set-username`: Set or update your username.

**`observal config`**: CLI configuration

- `observal config alias`: Set or remove a local registry reference alias.
- `observal config aliases`: List all local aliases.
- `observal config path`: Show the config file path.
- `observal config set`: Set a validated user-managed CLI setting.
- `observal config show`: Show effective CLI configuration without exposing credentials.

**`observal delegate`**: Hand a task to another approved agent (registry or remote A2A)

- `observal delegate cancel`: Stop a delegated task.
- `observal delegate find`: Find approved agents (registry and remote A2A) that can take a task.
- `observal delegate list`: List recent delegated tasks on this machine.
- `observal delegate mcp`: Run the observal-agents MCP server on stdio.
- `observal delegate reply`: Answer a remote agent that asked for more input.
- `observal delegate run`: Delegate a task and wait for the result.
- `observal delegate status`: Show a delegated task, optionally waiting for it to finish.

**`observal discover`**: Find approved resources for a task and use them in this session

- `observal discover inspect`: Show the complete entry for one resource.
- `observal discover search`: Search approved resources for a task.
- `observal discover use`: Activate a resource for the current task.

**`observal doctor`**: Diagnose and patch harness settings for Observal telemetry

- `observal doctor support`: Generate and inspect diagnostic support bundles. Bundles contain no customer data or row contents.
  - `observal doctor support bundle`: Generate a diagnostic support bundle. No customer data or row contents included.
  - `observal doctor support inspect`: Inspect a support bundle without extracting it.
- `observal doctor cleanup`: Remove Observal-managed telemetry artifacts while preserving user configuration.
- `observal doctor patch`: Install Observal-managed session telemetry for selected harnesses.

**`observal inbox`**: Your work and event feed: reviews, decisions, and update notices

- `observal inbox count`: Show unread and needs-action counts.
- `observal inbox dismiss`: Dismiss an item without acting on it.
- `observal inbox done`: Resolve an item.
- `observal inbox list`: List your inbox items.
- `observal inbox read`: Mark an item read without resolving it.
- `observal inbox read-all`: Mark everything matching the filter as read.
- `observal inbox reopen`: Reopen a resolved or dismissed item.
- `observal inbox show`: Show one item with its full action history.
- `observal inbox unread`: Mark an item unread again.

**`observal ops`**: Observability and operational commands (sessions, telemetry, rankings, feedback, insights)

- `observal ops insights`: Agent insight reports
  - `observal ops insights generate`: Trigger generation of a new insight report.
  - `observal ops insights list`: List insight reports for an agent.
  - `observal ops insights show`: Show an insight report with pretty-printed narrative.
- `observal ops logs`: Live log viewer (open in a separate tab)
- `observal ops telemetry`: Telemetry health commands
  - `observal ops telemetry status`: Check telemetry data flow status.
- `observal ops feedback`: Show feedback for an MCP server or agent.
- `observal ops rate`: Rate an MCP server, agent, or component.
- `observal ops rate-delete`: Delete your review for an item.
- `observal ops rate-update`: Update your existing review for an item.
- `observal ops top`: Show top MCP servers or agents by usage.
- `observal ops traces`: List recent traces (sessions).

**`observal registry`**: Component registry (MCPs, skills, hooks, prompts, sandboxes, remote A2A agents)

- `observal registry a2a`: Remote A2A agents (register an Agent Card, review, remove)
  - `observal registry a2a list`: List remote A2A agents you can see, including pending ones you own or review.
  - `observal registry a2a remove`: Remove a registered agent from discovery (owner or admin).
  - `observal registry a2a review`: Approve or reject a registered agent (reviewers and admins).
  - `observal registry a2a submit`: Register (or refresh) a remote A2A agent for review.
- `observal registry bulk`: Submit mixed Registry components from one JSON file.
  - `observal registry bulk submit`: Submit mixed MCP, skill, hook, prompt, and sandbox entries.
- `observal registry hook`: Hook registry commands
  - `observal registry hook co-authors`: Manage co-authors for hooks
    - `observal registry hook co-authors add`: Add a co-author.
    - `observal registry hook co-authors list`: List co-authors.
    - `observal registry hook co-authors remove`: Remove a co-author.
  - `observal registry hook archive`: Archive this component.
  - `observal registry hook edit`: Edit a draft, rejected, or pending hook submission.
  - `observal registry hook fork`: Fork an approved hook release into your own editable draft.
  - `observal registry hook install`: Install a hook for a specific harness.
  - `observal registry hook list`: List approved hooks from the registry.
  - `observal registry hook show`: Show detailed information for a single hook.
  - `observal registry hook submit`: Submit a new hook for review.
  - `observal registry hook transfer-owner`: Transfer ownership to another username.
  - `observal registry hook unarchive`: Restore an archived component.
- `observal registry mcp`: MCP server registry commands
  - `observal registry mcp co-authors`: Manage co-authors for mcps
    - `observal registry mcp co-authors add`: Add a co-author.
    - `observal registry mcp co-authors list`: List co-authors.
    - `observal registry mcp co-authors remove`: Remove a co-author.
  - `observal registry mcp sync`: Publish a new MCP version automatically when its GitHub or GitLab repository changes
    - `observal registry mcp sync disable`: Turn off webhook sync. Published versions are kept.
    - `observal registry mcp sync enable`: Turn on GitHub or GitLab webhook sync, or change which events trigger it.
    - `observal registry mcp sync rotate-secret`: Issue a new webhook secret. Update it in GitHub or GitLab, or deliveries will be rejected.
    - `observal registry mcp sync run`: Sync the tracked branch now, without waiting for a push.
    - `observal registry mcp sync status`: Show webhook sync settings and the result of the last sync.
  - `observal registry mcp submit`: Submit an MCP server to the registry.
  - `observal registry mcp show`: Show full details of an MCP server.
  - `observal registry mcp install`: Generate an install config snippet for an MCP server.
  - `observal registry mcp archive`: Archive this component.
  - `observal registry mcp edit`: Edit an MCP server submission.
  - `observal registry mcp fork`: Fork an approved mcp release into your own editable draft.
  - `observal registry mcp list`: List approved MCP servers in the registry.
  - `observal registry mcp my`: List your own MCP servers across all statuses.
  - `observal registry mcp transfer-owner`: Transfer ownership to another username.
  - `observal registry mcp unarchive`: Restore an archived component.
- `observal registry models`: Inspect registry-backed harness model data.
  - `observal registry models list`: List registry-backed harness models.
- `observal registry prompt`: Prompt registry commands
  - `observal registry prompt co-authors`: Manage co-authors for prompts
    - `observal registry prompt co-authors add`: Add a co-author.
    - `observal registry prompt co-authors list`: List co-authors.
    - `observal registry prompt co-authors remove`: Remove a co-author.
  - `observal registry prompt archive`: Archive this component.
  - `observal registry prompt edit`: Edit a draft, rejected, or pending prompt submission.
  - `observal registry prompt fork`: Fork an approved prompt release into your own editable draft.
  - `observal registry prompt list`: List approved prompts in the registry.
  - `observal registry prompt my`: List your own prompts across all statuses.
  - `observal registry prompt render`: Render a prompt template with variable substitution.
  - `observal registry prompt show`: Show detailed information about a prompt.
  - `observal registry prompt submit`: Submit a new prompt template for review.
  - `observal registry prompt transfer-owner`: Transfer ownership to another username.
  - `observal registry prompt unarchive`: Restore an archived component.
- `observal registry recommend`: Components recommended for you, based on your own sessions
  - `observal registry recommend dismiss`: Stop recommending a component to you.
  - `observal registry recommend list`: Show components recommended for you.
- `observal registry sandbox`: Sandbox registry commands
  - `observal registry sandbox co-authors`: Manage co-authors for sandboxes
    - `observal registry sandbox co-authors add`: Add a co-author.
    - `observal registry sandbox co-authors list`: List co-authors.
    - `observal registry sandbox co-authors remove`: Remove a co-author.
  - `observal registry sandbox archive`: Archive this component.
  - `observal registry sandbox edit`: Edit a draft, rejected, or pending sandbox submission.
  - `observal registry sandbox fork`: Fork an approved sandbox release into your own editable draft.
  - `observal registry sandbox list`: List approved sandboxes in the registry.
  - `observal registry sandbox show`: Show detailed information about a sandbox.
  - `observal registry sandbox submit`: Submit a new sandbox environment for review.
  - `observal registry sandbox transfer-owner`: Transfer ownership to another username.
  - `observal registry sandbox unarchive`: Restore an archived component.
- `observal registry skill`: Skill registry commands
  - `observal registry skill backups`: List, restore or explicitly prune verified skill folder backups.
    - `observal registry skill backups list`: List retained verified backups and their ages.
    - `observal registry skill backups prune`: Explicitly remove one old backup after verifying its active successor.
    - `observal registry skill backups restore`: Restore only a byte-verified unmodified active installation.
  - `observal registry skill co-authors`: Manage co-authors for skills
    - `observal registry skill co-authors add`: Add a co-author.
    - `observal registry skill co-authors list`: List co-authors.
    - `observal registry skill co-authors remove`: Remove a co-author.
  - `observal registry skill archive`: Archive this component.
  - `observal registry skill edit`: Edit a draft, rejected, or pending skill submission.
  - `observal registry skill export`: Export a skill version to a local directory.
  - `observal registry skill fork`: Create an editable successor to the currently reviewed direct release.
  - `observal registry skill fork-listing`: Fork an approved skill release into your own editable draft.
  - `observal registry skill import-folder`: Create one complete direct-folder draft under a reviewed Git or legacy listing.
  - `observal registry skill install`: Install an approved skill from Git or a complete reviewed registry folder.
  - `observal registry skill list`: List approved skills in the registry.
  - `observal registry skill my`: List your own skills across all statuses.
  - `observal registry skill rebase`: Rebase a draft skill version on the current approved version.
  - `observal registry skill replace-files`: Replace all files in a skill version with contents from a directory.
  - `observal registry skill show`: Show detailed information about a skill.
  - `observal registry skill submit`: Submit a new skill for review.
  - `observal registry skill transfer-owner`: Transfer ownership to another username.
  - `observal registry skill unarchive`: Restore an archived component.
  - `observal registry skill withdraw`: Withdraw a pending skill version from review.
- `observal registry version`: Manage component versions
  - `observal registry version list`: List version history for a registry component.
  - `observal registry version publish`: Publish a new version for a registry component.

**`observal self`**: CLI self-management commands (upgrade, downgrade, rollback, status)

- `observal self upgrade`: Upgrade the Observal CLI to the latest or specified version.
- `observal self downgrade`: Downgrade the Observal CLI to a previous version.
- `observal self rollback`: Restore the CLI binary saved before the last version change.
- `observal self status`: Show the CLI version, install method, and update availability.

**`observal server`**: Manage the embedded Observal server (PostgreSQL + ClickHouse + Redis + API).

- `observal server migrate`: Portable PostgreSQL and ClickHouse migration tools
  - `observal server migrate export`: Export all PostgreSQL registry data to a portable archive.
  - `observal server migrate export-telemetry`: Export ClickHouse telemetry data to Parquet files.
  - `observal server migrate import`: Import a migration archive into the target database.
  - `observal server migrate import-telemetry`: Import Parquet telemetry files into target ClickHouse.
  - `observal server migrate validate`: Validate archive integrity and optionally compare against a database.
  - `observal server migrate validate-telemetry`: Validate telemetry Parquet files and optionally check FK references.
- `observal server start`: Start the embedded services and API.
- `observal server stop`: Stop all embedded services.
- `observal server restart`: Restart all embedded services.
- `observal server status`: Show embedded service status.
- `observal server logs`: Show embedded service logs.
- `observal server install`: Download verified embedded database binaries.
- `observal server reset`: Stop embedded services and wipe database data and generated secrets.
- `observal server config`: Show embedded server paths and ports.
- `observal server rollback`: Restore PostgreSQL and the Docker image version from backup.
- `observal server upgrade`: Upgrade a local Docker deployment.
- `observal server versions`: List Docker image versions and managed PostgreSQL backups.

**`observal share`**: Share version-pinned Agents installed in the current repository.

- `observal share candidates`: List shareable Agent versions tracked in the current repository.
- `observal share create`: Select repository Agents and create an opaque, expiring share link.
- `observal share open`: Open a share and, after confirmation, pull its accessible Agents.
- `observal share revoke`: Revoke a share link immediately.

**`observal team`**: Manage teamspaces: creation, membership, access, and visibility.

- `observal team invite`: Manage private-team invitation links.
  - `observal team invite create`: Create a private-team invitation link. Owner or global admin only.
  - `observal team invite delete`: Delete an unused invitation. Owner or global admin only.
  - `observal team invite list`: List invitation links for a private teamspace.
  - `observal team invite preview`: Preview an invitation without requesting access.
  - `observal team invite request`: Use an invitation to request access. An owner must still approve.
  - `observal team invite requests`: List access requests associated with an invitation.
  - `observal team invite revoke`: Revoke a private-team invitation link. Owner or global admin only.
- `observal team members`: Manage team membership.
  - `observal team members add`: Add or update a team member. Owner or admin only.
  - `observal team members list`: List members of a teamspace.
  - `observal team members remove`: Remove a team member. Owner or admin only. The last owner cannot be removed.
- `observal team request`: Manage teamspace join requests.
  - `observal team request approve`: Approve a pending join request. Owner or admin only. Grants member role.
  - `observal team request join`: Request member access to a teamspace. An owner must approve.
  - `observal team request list`: List a teamspace's join requests and decisions. Owner or admin only.
  - `observal team request mine`: Show your join-request status for a teamspace.
  - `observal team request reject`: Reject a pending join request. Owner or admin only.
  - `observal team request withdraw`: Withdraw your pending join request for a teamspace.
- `observal team visibility`: Manage and review teamspace visibility.
  - `observal team visibility approve`: Approve pending public visibility. Reviewer or admin only.
  - `observal team visibility list-requests`: List pending public visibility requests. Reviewer or admin only.
  - `observal team visibility reject`: Reject pending public visibility. Reviewer or admin only.
  - `observal team visibility set`: Change visibility or request public review. Owners and admins only.
- `observal team claim-personal`: Claim or return your private personal teamspace.
- `observal team create`: Create a teamspace. Any signed-in user can; you become the owner.
- `observal team delete`: Delete a teamspace. Owner or admin only. This cannot be undone.
- `observal team leave`: Leave a teamspace. The last owner cannot leave; transfer ownership first.
- `observal team list`: List teamspaces you belong to (or all with --all).
- `observal team show`: Show teamspace detail and members.
<!-- END AUTO-GENERATED COMMAND REFERENCE -->
