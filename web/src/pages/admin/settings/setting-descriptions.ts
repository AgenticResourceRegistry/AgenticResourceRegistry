// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * One-line descriptions for schema settings that have no server subtitle.
 * Each is the opening sentence of the setting's entry in docs/self-hosting/*.md,
 * the same page the section's Docs button opens. Keep them in sync with the docs.
 */
export const SETTING_DESCRIPTIONS: Record<string, string> = {
	"auth.self_registration_enabled": "Controls whether visitors can create their own Observal account from the login page.",
	"deployment.frontend_url": "The base URL where users access the Observal web UI in their browser.",
	"deployment.public_url": "The externally-reachable URL of the Observal API server.",
	"deployment.cors_origins": "Origins allowed to make cross-origin browser requests to the API.",
	"security.trusted_proxy_ips": "A list of IP addresses or CIDR ranges that Observal trusts to set forwarding headers (X-Forwarded-For, X-Real-IP, X-Forwarded-Proto).",
	"security.allow_internal_git_urls": "Whether agent and component definitions can reference Git repositories on private/internal networks.",
	"resource.db_pool_size": "Number of persistent database connections maintained in the connection pool.",
	"resource.db_max_overflow": "Temporary connections created when the pool is full. These are closed after use.",
	"resource.redis_max_connections": "Maximum concurrent connections to Redis.",
	"resource.redis_socket_timeout": "Socket timeout in seconds for Redis operations.",
	"resource.clickhouse_max_connections": "Maximum HTTP connections to ClickHouse for analytics queries.",
	"resource.clickhouse_max_keepalive": "Persistent connections kept alive between requests.",
	"resource.clickhouse_timeout": "Maximum seconds a single ClickHouse query can run before cancellation.",
	"data.retention_days": "ClickHouse TTL for raw session content. Application retention values cannot exceed this ceiling when it is enabled.",
	"data.cache_ttl_default": "Default cache duration, in seconds, for ordinary API responses.",
	"data.cache_ttl_dashboard": "Cache duration, in seconds, for expensive dashboard aggregation queries.",
	"observability.log_level": "Controls server log verbosity.",
	"observability.log_format": "Controls whether logs are structured JSON or human-readable console output.",
	"observability.enable_openapi": "Expose interactive API documentation endpoints.",
	"observability.enable_metrics": "Expose Prometheus-compatible metrics.",
	"misc.harness_allowlist": "Restrict which harnesses are available in the platform.",
	"misc.default_harness": "The harness pre-selected in web installation controls.",
	"misc.git_mirror_base_path": "Directory used for cloned repository mirrors during component analysis and source discovery.",
};
