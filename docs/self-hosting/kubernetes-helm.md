---
# SPDX-FileCopyrightText: 2026 Ravi Chopra <shivamchopra1234567890@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-FileCopyrightText: 2026 VishnuM049 <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Kubernetes Deployment with Helm"
---

Deploy Agentic Resource Registry onto a Kubernetes cluster using the official Helm chart.

> [!WARNING]
> **Production Notice**: The in-cluster PostgreSQL, ClickHouse, and Redis StatefulSets deployed by this chart are intended for evaluation, development, and small-scale testing. For production workloads, set `postgresql.enabled=false`, `clickhouse.enabled=false`, and `redis.enabled=false`, then provide `postgresql.externalUrl`, `clickhouse.externalUrl`, and `redis.externalUrl` for dedicated external services.

## Prerequisites

- Kubernetes cluster v1.27 or higher
- `helm` v3.8.0 or higher installed
- `kubectl` configured to access your cluster
- Ingress controller (e.g., `ingress-nginx`) installed on the cluster
- Default `StorageClass` supporting dynamic volume provisioning (PV/PVC)

## Quick Start

Use the hosted OCI chart after the first release that includes Helm chart publishing has completed:

1. Install the chart into a dedicated namespace:
   ```bash
   kubectl create namespace observal
   helm install observal oci://ghcr.io/agenticresourceregistry/charts/observal \
     --version 1.16.0 \
     --namespace observal
   ```

2. Verify all workloads are running and completed:
   ```bash
   kubectl get pods -n observal
   ```

## Local Chart Development

To test unreleased chart changes directly from a clone:

1. Clone the repository:
   ```bash
   git clone --branch v1.16.0 --depth 1 https://github.com/AgenticResourceRegistry/AgenticResourceRegistry.git
   cd AgenticResourceRegistry
   ```

2. Install the chart into a dedicated namespace:
   ```bash
   kubectl create namespace observal
   helm install observal ./infra/helm/observal --namespace observal \
     --set api.image.tag=1.16.0 --set worker.image.tag=1.16.0 --set web.image.tag=1.16.0
   ```

3. Verify all workloads are running and completed:
   ```bash
   kubectl get pods -n observal
   ```

## Configuration

You can customize the deployment by passing a custom values file (`-f values.yaml`) or setting flags via `--set`.

```bash
helm install observal oci://ghcr.io/agenticresourceregistry/charts/observal \
  --version 1.16.0 \
  --namespace observal \
  -f custom-values.yaml
```

### Parameters Reference Table

| Parameter | Description | Default |
| --- | --- | --- |
| `global.imageRegistry` | Global image registry prefix | `""` |
| `global.imagePullSecrets` | Global image pull secrets list | `[]` |
| `api.image.repository` | API container image repository | `ghcr.io/agenticresourceregistry/observal-api` |
| `api.image.tag` | API container image tag. Defaults to the chart app version when empty. | `""` |
| `api.replicas` | Replicas for API deployment | `1` |
| `api.workers` | Uvicorn worker count per API pod | `2` |
| `worker.image.repository` | Worker container image repository | `ghcr.io/agenticresourceregistry/observal-api` |
| `worker.image.tag` | Worker container image tag. Defaults to the chart app version when empty. | `""` |
| `worker.replicas` | Replicas for background job worker | `1` |
| `web.image.repository` | Web UI container image repository | `ghcr.io/agenticresourceregistry/observal-web` |
| `web.image.tag` | Web UI container image tag. Defaults to the chart app version when empty. | `""` |
| `web.replicas` | Replicas for Web UI deployment | `1` |
| `postgresql.enabled` | Deploy embedded PostgreSQL StatefulSet | `true` |
| `postgresql.externalUrl` | PostgreSQL URL used when embedded PostgreSQL is disabled | `""` |
| `postgresql.storage.size` | PVC size for PostgreSQL | `10Gi` |
| `clickhouse.enabled` | Deploy embedded ClickHouse StatefulSet | `true` |
| `clickhouse.externalUrl` | ClickHouse URL used when embedded ClickHouse is disabled | `""` |
| `clickhouse.storage.size` | PVC size for ClickHouse | `50Gi` |
| `redis.enabled` | Deploy embedded Redis StatefulSet | `true` |
| `redis.externalUrl` | Redis URL used when embedded Redis is disabled | `""` |
| `redis.storage.size` | PVC size for Redis | `2Gi` |
| `ingress.enabled` | Enable Kubernetes Ingress resource | `true` |
| `ingress.host` | Hostname for Ingress rule | `observal.example.com` |
| `ingress.tls.enabled` | Enable TLS termination on Ingress | `false` |
| `ingress.tls.certManager.enabled` | Automatically request cert via cert-manager | `false` |
| `secrets.existingSecret` | Use pre-existing K8s Secret for credentials | `""` |
| `secrets.secretKey` | Override generated application secret | `""` |
| `config.logLevel` | Application log level (`DEBUG`, `INFO`, `WARN`, `ERROR`) | `INFO` |
| `config.seedDemoAccounts` | Seed demo accounts on startup | `false` |

## Accessing the Application

### Via Port Forwarding (Development/Testing)

To access the Web UI locally without configuring Ingress DNS:

```bash
kubectl port-forward svc/observal-web 3000:3000 -n observal
```

Open `http://localhost:3000` in your browser.

### Via Ingress & TLS (Production)

Enable ingress and configure TLS termination using `cert-manager`:

```bash
helm upgrade --install observal oci://ghcr.io/agenticresourceregistry/charts/observal \
  --version 1.16.0 \
  --namespace observal \
  --set ingress.enabled=true \
  --set ingress.host=observal.mycompany.com \
  --set ingress.tls.enabled=true \
  --set ingress.tls.secretName=observal-tls \
  --set ingress.tls.certManager.enabled=true \
  --set ingress.tls.certManager.issuerName=letsencrypt-prod
```

## Maintenance & Operations

### Upgrading

Before an update, read [the canonical migration/recovery gates](/self-hosting/upgrades), inspect the chart's initializer hooks, and drain incompatible old writers before a migration requiring a homogeneous fleet. A Helm dependency/hook is not proof existing API/worker pods stopped. After selecting the reviewed target and procedure, configuration changes use:

```bash
helm upgrade observal oci://ghcr.io/agenticresourceregistry/charts/observal \
  --version 1.16.0 \
  --namespace observal \
  -f custom-values.yaml
```

### Rollback

A Helm rollback changes release resources; it does not undo database migrations or restore data. Inspect the completed migration state and prior application compatibility before selecting a previous revision. Use [the verified recovery procedure](/self-hosting/backup-and-restore) if needed. First inspect history:

```bash
# View release history
helm history observal --namespace observal

# Only after checking schema/application compatibility:
helm rollback observal PRIOR_REVISION --namespace observal
```

### Uninstalling

To delete the deployment and associated Kubernetes resources:

```bash
helm uninstall observal --namespace observal
```

> [!NOTE]
> Persistent Volume Claims (PVCs) for PostgreSQL, ClickHouse, Redis, and API data are retained by default to prevent accidental data loss. To delete them permanently, execute: `kubectl delete pvc -l app.kubernetes.io/instance=observal -n observal`.

## Chart Publishing

Official releases publish the Helm chart as an OCI artifact to GitHub Container Registry:

```text
oci://ghcr.io/agenticresourceregistry/charts/observal
```

Helm OCI registries do not use `helm repo add`; install and upgrade commands reference the `oci://` chart URL directly.

After the first release publishes the package, make the GHCR chart package public in the repository package settings if it is not already public.

ArtifactHub should be registered against the OCI chart URL. OCI repositories require one ArtifactHub repository per chart. The release workflow pushes `infra/helm/artifacthub-repo.yml` to GHCR with the special `artifacthub.io` tag. After ArtifactHub creates the repository record, copy the repository ID from the ArtifactHub control panel into `infra/helm/artifacthub-repo.yml` as `repositoryID` to enable Verified Publisher status on the next chart release.
