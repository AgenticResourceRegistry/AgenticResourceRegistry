// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Maps setting keys and sections to documentation file paths and anchors.
 * Paths are relative to the repo root `docs/` directory and resolved at build time.
 */

export interface DocRef {
	/** Path relative to docs/ (e.g. "self-hosting/token-expiry.md") */
	file: string;
	/** Optional anchor within the doc (e.g. "access-tokens") */
	anchor?: string;
	/** Human-readable label for the help link */
	label: string;
}

/**
 * Map from setting key to its doc reference.
 * When a setting card is clicked in help mode, we load the referenced doc + anchor.
 */
export const SETTING_DOCS: Record<string, DocRef> = {
	// Authentication
	"auth.self_registration_enabled": { file: "self-hosting/authentication.md", anchor: "self-registration", label: "Self Registration" },

	// Insights
	"insights.api_key": { file: "insights-config.md", anchor: "api-key", label: "API Key" },
	"insights.api_base": { file: "insights-config.md", anchor: "api-base-url", label: "API Base URL" },
	"insights.model_sections": { file: "insights-config.md", anchor: "sections-model", label: "Sections Model" },
	"insights.model_synthesis": { file: "insights-config.md", anchor: "synthesis-model", label: "Synthesis Model" },
	"insights.model_facets": { file: "insights-config.md", anchor: "facets-model", label: "Facets Model" },
	"insights.batch_enabled": { file: "insights-config.md", anchor: "batch-processing", label: "Batch Processing" },
	"insights.batch_period_days": { file: "insights-config.md", anchor: "batch-period", label: "Batch Period" },
	"insights.min_sessions": { file: "insights-config.md", anchor: "min-sessions", label: "Min Sessions" },
	"insights.facet_max_calls": { file: "insights-config.md", anchor: "max-facet-calls", label: "Max Facet Calls" },
	"insights.facet_concurrency": { file: "insights-config.md", anchor: "facet-concurrency", label: "Facet Concurrency" },

	// SSO
	"deployment.sso_only": { file: "self-hosting/authentication.md", anchor: "sso-only-mode", label: "SSO Only Mode" },
	"oauth.client_id": { file: "self-hosting/authentication.md", anchor: "oauth-client-id", label: "OIDC Client ID" },
	"oauth.client_secret": { file: "self-hosting/authentication.md", anchor: "oauth-client-secret", label: "OIDC Client Secret" },
	"oauth.server_metadata_url": { file: "self-hosting/authentication.md", anchor: "oauth-server-metadata-url", label: "OIDC Discovery URL" },
	"google.client_id": { file: "self-hosting/authentication.md", anchor: "google-oauth", label: "Google Client ID" },
	"google.client_secret": { file: "self-hosting/authentication.md", anchor: "google-oauth", label: "Google Client Secret" },
	"google.allowed_domains": { file: "self-hosting/authentication.md", anchor: "google-allowed-domains", label: "Google Allowed Domains" },
	"github.client_id": { file: "self-hosting/authentication.md", anchor: "github-oauth", label: "GitHub Client ID" },
	"github.client_secret": { file: "self-hosting/authentication.md", anchor: "github-oauth", label: "GitHub Client Secret" },
	"github.allowed_orgs": { file: "self-hosting/authentication.md", anchor: "github-allowed-orgs", label: "GitHub Allowed Organizations" },

	// Deployment
	"deployment.frontend_url": { file: "self-hosting/deployment-settings.md", anchor: "frontend-url", label: "Frontend URL" },
	"deployment.public_url": { file: "self-hosting/deployment-settings.md", anchor: "public-api-url", label: "Public API URL" },
	"deployment.otlp_http_url": { file: "self-hosting/deployment-settings.md", anchor: "otlp-endpoint-override", label: "OTLP Endpoint Override" },
	"deployment.cors_origins": { file: "self-hosting/deployment-settings.md", anchor: "cors-origins", label: "CORS Origins" },

	// Security
	"security.allow_internal_git_urls": { file: "self-hosting/trusted-proxies.md", anchor: "allow-internal-git-urls", label: "Allow Internal Git URLs" },
	"security.allow_draft_install": { file: "self-hosting/trusted-proxies.md", anchor: "ssrf-protection", label: "Draft Install" },
	"security.rate_limit_auth": { file: "self-hosting/trusted-proxies.md", anchor: "auth-rate-limit", label: "Auth Rate Limit" },
	"security.rate_limit_auth_strict": { file: "self-hosting/trusted-proxies.md", anchor: "strict-auth-rate-limit", label: "Strict Auth Rate Limit" },
	"security.trusted_proxy_ips": { file: "self-hosting/trusted-proxies.md", anchor: "trusted-proxy-ips", label: "Trusted Proxy IPs" },

	// Token Expiry
	"jwt.access_token_expire_minutes": { file: "self-hosting/token-expiry.md", anchor: "access-tokens", label: "Access Tokens" },
	"jwt.refresh_token_expire_days": { file: "self-hosting/token-expiry.md", anchor: "refresh-tokens", label: "Refresh Tokens" },
	"jwt.hooks_token_expire_minutes": { file: "self-hosting/token-expiry.md", anchor: "hooks-tokens", label: "Hooks Tokens" },

	// SAML
	"saml.idp_entity_id": { file: "self-hosting/saml-settings.md", anchor: "idp-entity-id", label: "IdP Entity ID" },
	"saml.idp_sso_url": { file: "self-hosting/saml-settings.md", anchor: "idp-sso-url", label: "IdP SSO URL" },
	"saml.idp_slo_url": { file: "self-hosting/saml-settings.md", anchor: "idp-slo-url", label: "IdP SLO URL" },
	"saml.idp_x509_cert": { file: "self-hosting/saml-settings.md", anchor: "idp-certificate", label: "IdP Certificate" },
	"saml.idp_metadata_url": { file: "self-hosting/saml-settings.md", anchor: "idp-metadata-url", label: "IdP Metadata URL" },
	"saml.sp_entity_id": { file: "self-hosting/saml-settings.md", anchor: "sp-entity-id", label: "SP Entity ID" },
	"saml.sp_acs_url": { file: "self-hosting/saml-settings.md", anchor: "sp-acs-url", label: "SP ACS URL" },
	"saml.jit_provisioning": { file: "self-hosting/saml-settings.md", anchor: "jit-provisioning", label: "JIT Provisioning" },
	"saml.default_role": { file: "self-hosting/saml-settings.md", anchor: "default-role", label: "Default Role" },
	"saml.sp_key_encryption_password": { file: "self-hosting/saml-settings.md", anchor: "sp-key-password", label: "SP Key Password" },

	// Resource Tuning
	"resource.db_pool_size": { file: "self-hosting/resource-tuning.md", anchor: "db-pool-size", label: "DB Pool Size" },
	"resource.db_max_overflow": { file: "self-hosting/resource-tuning.md", anchor: "db-max-overflow", label: "DB Max Overflow" },
	"resource.redis_max_connections": { file: "self-hosting/resource-tuning.md", anchor: "redis-max-connections", label: "Redis Max Connections" },
	"resource.redis_socket_timeout": { file: "self-hosting/resource-tuning.md", anchor: "redis-timeout", label: "Redis Timeout" },
	"resource.clickhouse_max_connections": { file: "self-hosting/resource-tuning.md", anchor: "clickhouse-max-connections", label: "ClickHouse Max Connections" },
	"resource.clickhouse_max_keepalive": { file: "self-hosting/resource-tuning.md", anchor: "clickhouse-keepalive", label: "ClickHouse Keepalive" },
	"resource.clickhouse_timeout": { file: "self-hosting/resource-tuning.md", anchor: "clickhouse-query-timeout", label: "ClickHouse Query Timeout" },
	"resource.skip_ddl_on_startup": { file: "self-hosting/resource-tuning.md", anchor: "skip-ddl-on-startup", label: "Skip DDL on Startup" },
	"resource.max_query_memory_mb": { file: "self-hosting/resource-tuning.md", anchor: "query-memory-limit", label: "Query Memory Limit" },
	"resource.group_by_spill_mb": { file: "self-hosting/resource-tuning.md", anchor: "group-by-spill-threshold", label: "GROUP BY Spill Threshold" },
	"resource.sort_spill_mb": { file: "self-hosting/resource-tuning.md", anchor: "order-by-spill-threshold", label: "ORDER BY Spill Threshold" },
	"resource.join_memory_mb": { file: "self-hosting/resource-tuning.md", anchor: "join-memory-limit", label: "JOIN Memory Limit" },

	// Data & Retention
	"danger.purge_traces_insights": { file: "self-hosting/data-retention.md", anchor: "purge-traces-and-insights", label: "Purge Traces & Insights" },
	"data.retention_days": { file: "self-hosting/data-retention.md", anchor: "data-retention", label: "Data Retention" },
	"data.cache_ttl_default": { file: "self-hosting/data-retention.md", anchor: "default-cache-ttl", label: "Default Cache TTL" },
	"data.cache_ttl_dashboard": { file: "self-hosting/data-retention.md", anchor: "dashboard-cache-ttl", label: "Dashboard Cache TTL" },
	"data.cache_ttl_otel": { file: "self-hosting/data-retention.md", anchor: "otel-cache-ttl", label: "OTEL Cache TTL" },

	// Observability
	"observability.log_level": { file: "self-hosting/observability-settings.md", anchor: "log-level", label: "Log Level" },
	"observability.log_format": { file: "self-hosting/observability-settings.md", anchor: "log-format", label: "Log Format" },
	"observability.enable_openapi": { file: "self-hosting/observability-settings.md", anchor: "enable-openapi", label: "Enable OpenAPI" },
	"observability.enable_metrics": { file: "self-hosting/observability-settings.md", anchor: "enable-metrics", label: "Enable Metrics" },

	// Miscellaneous
	"misc.harness_allowlist": { file: "self-hosting/miscellaneous.md", anchor: "harness-allowlist", label: "harness Allowlist" },
	"misc.default_harness": { file: "self-hosting/miscellaneous.md", anchor: "default-harness", label: "Default harness" },
	"misc.git_mirror_base_path": { file: "self-hosting/miscellaneous.md", anchor: "git-mirror-path", label: "Git Mirror Path" },
};

/**
 * Map from section title to its doc reference (for section-level help).
 */
export const SECTION_DOCS: Record<string, DocRef> = {
	"Authentication": { file: "self-hosting/authentication.md", label: "Authentication Settings" },
	"Agent Insights": { file: "insights-config.md", label: "Agent Insights Configuration" },
	"Deployment": { file: "self-hosting/deployment-settings.md", label: "Deployment Settings" },
	"Security": { file: "self-hosting/trusted-proxies.md", label: "Trusted Proxies & Network Security" },
	"SSO": { file: "self-hosting/authentication.md", label: "SSO Configuration" },
	"JWT Token Expiry": { file: "self-hosting/token-expiry.md", label: "Token Expiry Settings" },
	"Resource Tuning": { file: "self-hosting/resource-tuning.md", label: "Resource Tuning" },
	"Data & Retention": { file: "self-hosting/data-retention.md", label: "Data & Retention Settings" },
	"Data Retention": { file: "self-hosting/data-retention.md", anchor: "data-retention", label: "Data Retention" },
	"Data Migration": { file: "self-hosting/data-migration.md", label: "Data Migration" },
	"Appearance": { file: "self-hosting/miscellaneous.md", anchor: "appearance", label: "Appearance" },
	"Trace Privacy": { file: "self-hosting/miscellaneous.md", anchor: "trace-privacy", label: "Trace Privacy" },
	"Registered Agents Only": { file: "self-hosting/miscellaneous.md", anchor: "registered-agents-only", label: "Registered Agents Only" },
	"Telemetry Purge": { file: "self-hosting/data-retention.md", anchor: "purge-traces-and-insights", label: "Purge Traces & Insights" },
	"Observability": { file: "self-hosting/observability-settings.md", label: "Observability Settings" },
	"Miscellaneous": { file: "self-hosting/miscellaneous.md", label: "Miscellaneous Settings" },
};

/**
 * Map from page/feature context to doc references for non-settings pages.
 */
export const PAGE_DOCS: Record<string, DocRef> = {
	// Traces
	"traces": { file: "core-concepts/session-tracking.md", label: "Session Tracking" },
	"traces.detail": { file: "self-hosting/telemetry.md", label: "Telemetry Configuration" },

	// Registry
	"agents": { file: "core-concepts/README.md", label: "Core Concepts" },
	"agents.builder": { file: "core-concepts/README.md", label: "Core Concepts" },
	"agents.install": { file: "getting-started/quickstart.md", label: "Quickstart" },
	"components": { file: "core-concepts/README.md", label: "Core Concepts" },

	// Admin
	"audit-log": { file: "reference/api-endpoints.md", label: "API Reference" },
	"sso": { file: "self-hosting/authentication.md", label: "SSO" },
	"diagnostics": { file: "self-hosting/troubleshooting.md", label: "Troubleshooting" },
	"migration": { file: "self-hosting/data-migration.md", label: "Data Migration" },
};
