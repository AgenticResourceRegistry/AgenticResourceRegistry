// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Loads curated documentation markdown files bundled at build time via Vite.
 * Do not glob all docs. Some repo docs are contributor-only or deprecated.
 */

const VISIBLE_DOC_PATHS = [
	"core-concepts/index.md",
	"core-concepts/session-tracking.md",
	"getting-started/installation.md",
	"getting-started/quickstart.md",
	"getting-started/observal-skill.md",
	"cli/prompt.md",
	"insights-config.md",
	"insights-setup.md",
	"self-observability.md",
	"hooks.md",
	"sandboxes.md",
	"registry-mcp-helper.md",
	"registry-skill-helper.md",
	"registry-hook-helper.md",
	"registry-sandbox-helper.md",
	"licensing.md",
	"self-hosting/index.md",
	"self-hosting/authentication.md",
	"self-hosting/configuration.md",
	"self-hosting/deployment-settings.md",
	"self-hosting/saml-settings.md",
	"self-hosting/okta-setup.md",
	"self-hosting/token-expiry.md",
	"self-hosting/trusted-proxies.md",
	"self-hosting/data-retention.md",
	"self-hosting/data-migration.md",
	"self-hosting/observability-settings.md",
	"self-hosting/resource-tuning.md",
	"self-hosting/miscellaneous.md",
	"self-hosting/databases.md",
	"self-hosting/docker-compose.md",
	"self-hosting/ports-and-volumes.md",
	"self-hosting/production-deploy.md",
	"self-hosting/backup-and-restore.md",
	"self-hosting/troubleshooting.md",
	"self-hosting/upgrades.md",
	"reference/api-endpoints.md",
	"reference/config-files.md",
	"reference/environment-variables.md",
	"reference/hooks-spec.md",
	"use-cases/debug-agent-failures.md",
	"use-cases/observe-mcp-traffic.md",
	"use-cases/share-agent-configs.md",
	"use-cases/team-registry.md",
];

const docModules = import.meta.glob<string>([
	"../../../docs/core-concepts/index.md",
	"../../../docs/core-concepts/session-tracking.md",
	"../../../docs/getting-started/installation.md",
	"../../../docs/getting-started/quickstart.md",
	"../../../docs/getting-started/observal-skill.md",
	"../../../docs/cli/prompt.md",
	"../../../docs/insights-config.md",
	"../../../docs/insights-setup.md",
	"../../../docs/self-observability.md",
	"../../../docs/hooks.md",
	"../../../docs/sandboxes.md",
	"../../../docs/registry-mcp-helper.md",
	"../../../docs/registry-skill-helper.md",
	"../../../docs/registry-hook-helper.md",
	"../../../docs/registry-sandbox-helper.md",
	"../../../docs/licensing.md",
	"../../../docs/self-hosting/index.md",
	"../../../docs/self-hosting/authentication.md",
	"../../../docs/self-hosting/configuration.md",
	"../../../docs/self-hosting/deployment-settings.md",
	"../../../docs/self-hosting/saml-settings.md",
	"../../../docs/self-hosting/okta-setup.md",
	"../../../docs/self-hosting/token-expiry.md",
	"../../../docs/self-hosting/trusted-proxies.md",
	"../../../docs/self-hosting/data-retention.md",
	"../../../docs/self-hosting/data-migration.md",
	"../../../docs/self-hosting/observability-settings.md",
	"../../../docs/self-hosting/resource-tuning.md",
	"../../../docs/self-hosting/miscellaneous.md",
	"../../../docs/self-hosting/databases.md",
	"../../../docs/self-hosting/docker-compose.md",
	"../../../docs/self-hosting/ports-and-volumes.md",
	"../../../docs/self-hosting/production-deploy.md",
	"../../../docs/self-hosting/backup-and-restore.md",
	"../../../docs/self-hosting/troubleshooting.md",
	"../../../docs/self-hosting/upgrades.md",
	"../../../docs/reference/api-endpoints.md",
	"../../../docs/reference/config-files.md",
	"../../../docs/reference/environment-variables.md",
	"../../../docs/reference/hooks-spec.md",
	"../../../docs/use-cases/debug-agent-failures.md",
	"../../../docs/use-cases/observe-mcp-traffic.md",
	"../../../docs/use-cases/share-agent-configs.md",
	"../../../docs/use-cases/team-registry.md",
], {
	query: "?raw",
	import: "default",
});

function toGlobKey(relativePath: string): string {
	return `../../../docs/${relativePath}`;
}

export async function loadDoc(relativePath: string): Promise<string | null> {
	const key = toGlobKey(relativePath);
	const loader = docModules[key];
	if (!loader) {
		console.warn(`[docs-loader] No doc found for path: ${relativePath} (key: ${key})`);
		return null;
	}
	return loader();
}

export function listDocPaths(): string[] {
	return VISIBLE_DOC_PATHS.filter((path) => docModules[toGlobKey(path)]);
}
