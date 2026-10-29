<!-- SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Observal

Observal is an agent-centric registry and observability platform for AI coding agents. This chart deploys the API, web UI, worker, PostgreSQL, ClickHouse, Redis, and supporting Kubernetes resources for self-hosted installations.

## Install

After the hosted OCI chart has been published:

```bash
kubectl create namespace observal
helm install observal oci://ghcr.io/agenticresourceregistry/charts/observal \
  --version <version> \
  --namespace observal
```

For production deployments, use managed PostgreSQL, ClickHouse, and Redis services by setting `postgresql.enabled=false`, `clickhouse.enabled=false`, and `redis.enabled=false`, then providing the matching external connection URLs.

See the Kubernetes deployment guide for the full values reference and operational notes:

https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/blob/main/docs/self-hosting/kubernetes-helm.md
