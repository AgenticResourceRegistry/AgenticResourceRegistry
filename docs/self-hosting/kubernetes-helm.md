---
# SPDX-FileCopyrightText: 2026 Ravi Chopra <shivamchopra1234567890@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Kubernetes Deployment with Helm"
---

Deploy Observal onto a Kubernetes cluster using the official Helm chart.

> [!WARNING]
> **Production Notice**: The in-cluster PostgreSQL, ClickHouse, and Redis StatefulSets deployed by this chart are intended for evaluation, development, and small-scale testing. For production workloads, point the chart at managed services with `externalPostgres`, `externalClickhouse`, and `externalRedis`. See [External databases](#external-databases).

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
   helm install observal oci://ghcr.io/observal/charts/observal \
     --version <version> \
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
   git clone https://github.com/Observal/Observal.git
   cd Observal
   ```

2. Install the chart into a dedicated namespace:
   ```bash
   kubectl create namespace observal
   helm install observal ./infra/helm/observal --namespace observal
   ```

3. Verify all workloads are running and completed:
   ```bash
   kubectl get pods -n observal
   ```

## Configuration

You can customize the deployment by passing a custom values file (`-f values.yaml`) or setting flags via `--set`.

```bash
helm install observal oci://ghcr.io/observal/charts/observal \
  --version <version> \
  --namespace observal \
  -f custom-values.yaml
```

### Parameters Reference Table

| Parameter | Description | Default |
| --- | --- | --- |
| `global.imageRegistry` | Global image registry prefix | `""` |
| `global.imagePullSecrets` | Global image pull secrets list | `[]` |
| `api.image.repository` | API container image repository | `ghcr.io/observal/observal-api` |
| `api.image.tag` | API container image tag. Defaults to the chart app version when empty. | `""` |
| `api.replicas` | Replicas for API deployment | `1` |
| `api.workers` | Uvicorn worker count per API pod | `2` |
| `worker.image.repository` | Worker container image repository | `ghcr.io/observal/observal-api` |
| `worker.image.tag` | Worker container image tag. Defaults to the chart app version when empty. | `""` |
| `worker.replicas` | Replicas for background job worker | `1` |
| `web.image.repository` | Web UI container image repository | `ghcr.io/observal/observal-web` |
| `web.image.tag` | Web UI container image tag. Defaults to the chart app version when empty. | `""` |
| `web.replicas` | Replicas for Web UI deployment | `1` |
| `postgresql.enabled` | Deploy embedded PostgreSQL StatefulSet | `true` |
| `postgresql.storage.size` | PVC size for PostgreSQL | `10Gi` |
| `clickhouse.enabled` | Deploy embedded ClickHouse StatefulSet | `true` |
| `clickhouse.storage.size` | PVC size for ClickHouse | `50Gi` |
| `redis.enabled` | Deploy embedded Redis StatefulSet | `true` |
| `redis.storage.size` | PVC size for Redis | `2Gi` |
| `externalPostgres.enabled` | Use an external PostgreSQL and skip the embedded StatefulSet | `false` |
| `externalPostgres.existingSecret` | Secret holding the full connection URL | `""` |
| `externalPostgres.existingSecretKey` | Key of that Secret holding the URL | `connection-uri` |
| `externalPostgres.host` / `port` / `database` | Endpoint, used when `existingSecret` is empty | `""` / `5432` / `observal` |
| `externalPostgres.username` / `password` | Credentials, used when `existingSecret` is empty | `postgres` / `""` |
| `externalPostgres.sslMode` | Appended as `?ssl=<mode>` (for example `require`) | `""` |
| `externalClickhouse.enabled` | Use an external ClickHouse and skip the embedded StatefulSet | `false` |
| `externalClickhouse.existingSecret` / `existingSecretKey` | Secret and key holding the full connection URL | `""` / `connection-uri` |
| `externalClickhouse.host` / `port` / `database` | Endpoint, used when `existingSecret` is empty | `""` / `8123` / `observal` |
| `externalClickhouse.username` / `password` | Credentials, used when `existingSecret` is empty | `default` / `""` |
| `externalClickhouse.secure` | Use HTTPS (`clickhouses://`) | `false` |
| `externalRedis.enabled` | Use an external Redis and skip the embedded StatefulSet | `false` |
| `externalRedis.existingSecret` / `existingSecretKey` | Secret and key holding the full connection URL | `""` / `connection-uri` |
| `externalRedis.host` / `port` / `database` | Endpoint, used when `existingSecret` is empty | `""` / `6379` / `0` |
| `externalRedis.username` / `password` | Credentials, used when `existingSecret` is empty | `""` / `""` |
| `externalRedis.tls` | Use TLS (`rediss://`) | `false` |
| `ingress.enabled` | Enable Kubernetes Ingress resource | `true` |
| `ingress.host` | Hostname for Ingress rule | `observal.example.com` |
| `ingress.tls.enabled` | Enable TLS termination on Ingress | `false` |
| `ingress.tls.certManager.enabled` | Automatically request cert via cert-manager | `false` |
| `secrets.existingSecret` | Use pre-existing K8s Secret for `SECRET_KEY` and embedded-service credentials | `""` |
| `secrets.secretKey` | Override generated application secret | `""` |
| `config.logLevel` | Application log level (`DEBUG`, `INFO`, `WARN`, `ERROR`) | `INFO` |
| `config.seedDemoAccounts` | Seed demo accounts on startup | `false` |

## External databases

Set `externalPostgres.enabled`, `externalClickhouse.enabled`, or `externalRedis.enabled` to skip the matching in-cluster StatefulSet and point the API, worker, and init Job at your own service. Each service is independent, so you can mix embedded and external. Setting `postgresql.enabled=false` (or the ClickHouse/Redis equivalent) without enabling the external block fails the install.

Each external service takes its connection URL from one of two sources:

1. **`existingSecret` (recommended).** A Secret you manage, holding the full URL under `existingSecretKey` (default `connection-uri`).
2. **`host`, `port`, `database`, `username`, `password`.** The chart builds the URL, percent-encodes the credentials, and stores it in a chart-managed Secret. The password then lives in your values and Helm release history.

`existingSecret` wins when both are set.

URL formats expected in an `existingSecret`:

| Service | Format |
| --- | --- |
| PostgreSQL | `postgresql+asyncpg://user:pass@host:5432/observal?ssl=require` |
| ClickHouse | `clickhouse://user:pass@host:8123/observal` (HTTP) or `clickhouses://user:pass@host:8443/observal` (HTTPS) |
| Redis | `redis://:pass@host:6379/0` or `rediss://:pass@host:6379/0` (TLS) |

Percent-encode special characters in credentials (`@` as `%40`, `/` as `%2F`, `:` as `%3A`).

### Example: all managed services, secrets by reference

```bash
kubectl create secret generic observal-pg -n observal \
  --from-literal=connection-uri='postgresql+asyncpg://observal:PASSWORD@mydb.abc.us-east-1.rds.amazonaws.com:5432/observal?ssl=require'
kubectl create secret generic observal-clickhouse -n observal \
  --from-literal=connection-uri='clickhouses://default:PASSWORD@abc.us-east-1.aws.clickhouse.cloud:8443/observal'
kubectl create secret generic observal-redis -n observal \
  --from-literal=connection-uri='rediss://:AUTH_TOKEN@master.mycache.use1.cache.amazonaws.com:6379/0'
```

```yaml
externalPostgres:
  enabled: true
  existingSecret: observal-pg
externalClickhouse:
  enabled: true
  existingSecret: observal-clickhouse
externalRedis:
  enabled: true
  existingSecret: observal-redis
```

### Example: host settings in values

```yaml
externalPostgres:
  enabled: true
  host: mydb.abc.us-east-1.rds.amazonaws.com
  username: observal
  password: ""   # pass with --set externalPostgres.password=...
  sslMode: require
externalClickhouse:
  enabled: true
  host: abc.us-east-1.aws.clickhouse.cloud
  port: 8443
  secure: true
  password: ""   # pass with --set externalClickhouse.password=...
externalRedis:
  enabled: true
  host: master.mycache.use1.cache.amazonaws.com
  tls: true
  password: ""   # pass with --set externalRedis.password=...
```

### Notes

- `sslMode: require` encrypts the PostgreSQL connection but does not verify the server certificate.
- The database must exist and the user must be able to create tables (PostgreSQL) and run `CREATE DATABASE IF NOT EXISTS` (ClickHouse). The init Job runs the migrations and retries a limited number of times (Job `backoffLimit`); unlike the embedded services there is no wait step, so an unreachable endpoint shows up as a failing `observal-init` Job.
- With `secrets.existingSecret`, the Secret only needs `SECRET_KEY` plus the keys for services that remain embedded (`POSTGRES_PASSWORD` and `DATABASE_URL`, `CLICKHOUSE_PASSWORD` and `CLICKHOUSE_URL`, `REDIS_URL`).
- **Upgrading from `postgresql.externalUrl`, `clickhouse.externalUrl`, or `redis.externalUrl`:** these values were removed. Move each URL into a Secret and reference it with `externalX.existingSecret`, or split it into the `externalX` host fields.

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
helm upgrade --install observal oci://ghcr.io/observal/charts/observal \
  --version <version> \
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

To apply configuration changes or update to a newer chart version:

```bash
helm upgrade observal oci://ghcr.io/observal/charts/observal \
  --version <version> \
  --namespace observal \
  -f custom-values.yaml
```

### Rollback

If an upgrade encounters issues, rollback to a previous release revision:

```bash
# View release history
helm history observal --namespace observal

# Rollback to revision 1
helm rollback observal 1 --namespace observal
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
oci://ghcr.io/observal/charts/observal
```

Helm OCI registries do not use `helm repo add`; install and upgrade commands reference the `oci://` chart URL directly.

After the first release publishes the package, make the GHCR chart package public in the repository package settings if it is not already public.

ArtifactHub should be registered against the OCI chart URL. OCI repositories require one ArtifactHub repository per chart. The release workflow pushes `infra/helm/artifacthub-repo.yml` to GHCR with the special `artifacthub.io` tag. After ArtifactHub creates the repository record, copy the repository ID from the ArtifactHub control panel into `infra/helm/artifacthub-repo.yml` as `repositoryID` to enable Verified Publisher status on the next chart release.
