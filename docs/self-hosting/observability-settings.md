---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Observability Settings"
---

Configure server logging, API documentation exposure, and Prometheus metrics.

## Log Level

Controls server log verbosity.

| Value | Effect |
|-------|--------|
| `TRACE` | Most verbose level, includes trace-level developer logs and very detailed execution flow |
| `DEBUG` | Verbose application diagnostics for active debugging |
| `INFO` (default) | Normal production logging |
| `WARNING` | Only warnings and errors |
| `ERROR` | Errors only, minimal operational visibility |

**When to use DEBUG:** Temporarily while investigating a bug. Do not leave it enabled in production because it increases log volume and can expose internal implementation details.

**When to use TRACE:** Only during short, targeted investigations where DEBUG is not enough. TRACE can include high-volume request, hook, parser, and internal execution details. Enable briefly, capture the needed logs, then return to INFO.

## Log Format

Controls whether logs are structured JSON or human-readable console output.

| Value | Effect |
|-------|--------|
| `json` | Best for Datadog, Loki, CloudWatch, and other log aggregators |
| `console` | Best for local development and manual log reading |

## Enable OpenAPI

Expose interactive API documentation endpoints.

| Value | Effect |
|-------|--------|
| `true` | Exposes `/docs`, `/redoc`, and `/openapi.json` |
| `false` | Hides generated API documentation |

**Production recommendation:** Disable unless your admins actively use these endpoints. It reduces public attack surface and avoids exposing schema details to unauthenticated users.

## Enable Metrics

Expose Prometheus-compatible metrics.

| Value | Effect |
|-------|--------|
| `true` | Exposes `/metrics` for Prometheus scraping |
| `false` | Metrics endpoint is unavailable |

**When to enable:** You run Prometheus, Grafana, or another scraper and want infrastructure metrics for API health, request latency, errors, and worker activity.
