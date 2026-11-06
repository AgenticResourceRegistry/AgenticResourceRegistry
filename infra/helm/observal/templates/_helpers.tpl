{{/*
SPDX-FileCopyrightText: 2026 Ravi Chopra <shivamchopra1234567890@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
SPDX-License-Identifier: Apache-2.0
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "observal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "observal.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "observal.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "observal.labels" -}}
helm.sh/chart: {{ include "observal.chart" . }}
{{ include "observal.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "observal.selectorLabels" -}}
app.kubernetes.io/name: {{ include "observal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name for the init Job.
*/}}
{{- define "observal.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (printf "%s-init" (include "observal.fullname" .)) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret holding all sensitive env vars.
*/}}
{{- define "observal.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- printf "%s-secret" (include "observal.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Name of the Secret holding JWT signing keys (created by init Job).
*/}}
{{- define "observal.jwtSecretName" -}}
{{- printf "%s-jwt-keys" (include "observal.fullname" .) }}
{{- end }}

{{/*
Name of the ConfigMap holding non-sensitive env vars.
*/}}
{{- define "observal.configMapName" -}}
{{- printf "%s-config" (include "observal.fullname" .) }}
{{- end }}

{{/*
Embedded workload toggles. Render "true" or an empty string.
*/}}
{{- define "observal.postgres.embedded" -}}
{{- if and .Values.postgresql.enabled (not .Values.externalPostgres.enabled) }}true{{ end }}
{{- end }}

{{- define "observal.clickhouse.embedded" -}}
{{- if and .Values.clickhouse.enabled (not .Values.externalClickhouse.enabled) }}true{{ end }}
{{- end }}

{{- define "observal.redis.embedded" -}}
{{- if and .Values.redis.enabled (not .Values.externalRedis.enabled) }}true{{ end }}
{{- end }}

{{/*
Name of the Secret holding URLs built from externalX host settings.
*/}}
{{- define "observal.externalSecretName" -}}
{{- printf "%s-external" (include "observal.fullname" .) }}
{{- end }}

{{/*
Percent-encoded userinfo (with trailing @), or empty when there is neither user nor password.
Takes a dict with username and password.
*/}}
{{- define "observal.urlUserinfo" -}}
{{- $user := (.username | default "") | toString | urlquery | replace "+" "%20" -}}
{{- $pass := (.password | default "") | toString | urlquery | replace "+" "%20" -}}
{{- if and $user $pass -}}
{{- printf "%s:%s@" $user $pass -}}
{{- else if $user -}}
{{- printf "%s@" $user -}}
{{- else if $pass -}}
{{- printf ":%s@" $pass -}}
{{- end -}}
{{- end }}

{{- define "observal.externalPostgresUrl" -}}
{{- $e := .Values.externalPostgres -}}
{{- printf "postgresql+asyncpg://%s%s:%d/%s" (include "observal.urlUserinfo" $e) $e.host (int $e.port) $e.database -}}
{{- if $e.sslMode }}?ssl={{ $e.sslMode }}{{ end -}}
{{- end }}

{{- define "observal.externalClickhouseUrl" -}}
{{- $e := .Values.externalClickhouse -}}
{{- $scheme := ternary "clickhouses" "clickhouse" (default false $e.secure) -}}
{{- printf "%s://%s%s:%d/%s" $scheme (include "observal.urlUserinfo" $e) $e.host (int $e.port) $e.database -}}
{{- end }}

{{- define "observal.externalRedisUrl" -}}
{{- $e := .Values.externalRedis -}}
{{- $scheme := ternary "rediss" "redis" (default false $e.tls) -}}
{{- printf "%s://%s%s:%d/%d" $scheme (include "observal.urlUserinfo" $e) $e.host (int $e.port) (int $e.database) -}}
{{- end }}

{{/*
Fail early on inconsistent embedded/external settings.
*/}}
{{- define "observal.validate" -}}
{{- range $svc := list
    (dict "name" "postgresql" "ext" "externalPostgres" "embedded" .Values.postgresql.enabled "cfg" .Values.externalPostgres)
    (dict "name" "clickhouse" "ext" "externalClickhouse" "embedded" .Values.clickhouse.enabled "cfg" .Values.externalClickhouse)
    (dict "name" "redis" "ext" "externalRedis" "embedded" .Values.redis.enabled "cfg" .Values.externalRedis) -}}
{{- if and (not $svc.embedded) (not $svc.cfg.enabled) -}}
{{- fail (printf "%s.enabled=false requires %s.enabled=true" $svc.name $svc.ext) -}}
{{- end -}}
{{- if and $svc.cfg.enabled (not $svc.cfg.existingSecret) (not $svc.cfg.host) -}}
{{- fail (printf "%s.enabled=true requires %s.existingSecret or %s.host" $svc.ext $svc.ext $svc.ext) -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
DATABASE_URL, CLICKHOUSE_URL and REDIS_URL env entries, each read from the Secret that owns it.
*/}}
{{- define "observal.connectionEnv" -}}
{{- $shared := include "observal.secretName" . -}}
{{- $generated := include "observal.externalSecretName" . -}}
{{- range $svc := list
    (dict "var" "DATABASE_URL" "embedded" (include "observal.postgres.embedded" .) "cfg" .Values.externalPostgres)
    (dict "var" "CLICKHOUSE_URL" "embedded" (include "observal.clickhouse.embedded" .) "cfg" .Values.externalClickhouse)
    (dict "var" "REDIS_URL" "embedded" (include "observal.redis.embedded" .) "cfg" .Values.externalRedis) -}}
{{- $name := $shared -}}
{{- $key := $svc.var -}}
{{- if not $svc.embedded -}}
{{- if $svc.cfg.existingSecret -}}
{{- $name = $svc.cfg.existingSecret -}}
{{- $key = default "connection-uri" $svc.cfg.existingSecretKey -}}
{{- else -}}
{{- $name = $generated -}}
{{- end -}}
{{- end }}
- name: {{ $svc.var }}
  valueFrom:
    secretKeyRef:
      name: {{ $name }}
      key: {{ $key }}
{{- end }}
{{- end }}

{{/*
Image pull policy helper. Takes a dict with repository, tag, pullPolicy,
and optional appVersion fallback.
*/}}
{{- define "observal.image" -}}
{{- $registry := .global.imageRegistry -}}
{{- $repo := .image.repository -}}
{{- $tag := .image.tag | default (.appVersion | default "latest") -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end }}

{{/*
Standard initContainer: wait for Postgres readiness.
*/}}
{{- define "observal.initWaitPostgres" -}}
{{- if include "observal.postgres.embedded" . }}
- name: wait-for-postgres
  image: postgres:16
  imagePullPolicy: IfNotPresent
  command:
    - sh
    - -c
    - |
      until pg_isready -h {{ include "observal.fullname" . }}-db -U postgres; do
        echo "Waiting for Postgres..."; sleep 2;
      done
  env:
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ include "observal.secretName" . }}
          key: POSTGRES_PASSWORD
{{- end }}
{{- end }}

{{/*
Standard initContainer: wait for ClickHouse readiness.
*/}}
{{- define "observal.initWaitClickhouse" -}}
{{- if include "observal.clickhouse.embedded" . }}
- name: wait-for-clickhouse
  image: curlimages/curl:8.8.0
  imagePullPolicy: IfNotPresent
  command:
    - sh
    - -c
    - |
      until curl -sf http://{{ include "observal.fullname" . }}-clickhouse:8123/ping; do
        echo "Waiting for ClickHouse..."; sleep 2;
      done
{{- end }}
{{- end }}

{{/*
Standard initContainer: wait for Redis readiness.
*/}}
{{- define "observal.initWaitRedis" -}}
{{- if include "observal.redis.embedded" . }}
- name: wait-for-redis
  image: redis:7-alpine
  imagePullPolicy: IfNotPresent
  command:
    - sh
    - -c
    - |
      until redis-cli -h {{ include "observal.fullname" . }}-redis ping | grep -q PONG; do
        echo "Waiting for Redis..."; sleep 2;
      done
{{- end }}
{{- end }}

{{/*
Standard initContainer: wait for init Job completion.
Polls the Job's succeeded count via the K8s API using the init ServiceAccount.
*/}}
{{- define "observal.initWaitInitJob" -}}
- name: wait-for-init
  image: bitnami/kubectl:latest
  imagePullPolicy: IfNotPresent
  command:
    - sh
    - -c
    - |
      JOB="{{ include "observal.fullname" . }}-init"
      NS="{{ .Release.Namespace }}"
      echo "Waiting for init Job $JOB to complete..."
      until [ "$(kubectl get job $JOB -n $NS -o jsonpath='{.status.succeeded}' 2>/dev/null)" = "1" ]; do
        echo "  init Job not yet succeeded, retrying in 5s..."; sleep 5;
      done
      echo "Init Job completed."
{{- end }}

{{/*
Standard container security context.
*/}}
{{- define "observal.securityContext" -}}
securityContext:
  allowPrivilegeEscalation: false
  runAsUser: 1001
  runAsGroup: 1001
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{/*
Standard pod security context.
*/}}
{{- define "observal.podSecurityContext" -}}
securityContext:
  seccompProfile:
    type: RuntimeDefault
{{- end }}
