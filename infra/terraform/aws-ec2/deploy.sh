#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
#
# Deploy Observal onto the EC2 instance provisioned by Terraform.
# Uses pre-built images from GHCR (no source builds required).
# Run this AFTER `terraform apply` completes.
#
# Usage: ./deploy.sh

set -euo pipefail

# ── Read Terraform outputs ───────────────────────────────────────────────────

INSTANCE_ID=$(terraform output -raw instance_id)
PUBLIC_IP=$(terraform output -raw public_ip)
REGION=$(terraform output -raw region)
DOMAIN=$(terraform output -raw domain)
IMAGE_TAG=$(terraform output -raw image_tag)
OBSERVAL_REF=$(terraform output -raw observal_ref)
OBSERVAL_REPO=$(terraform output -raw observal_repo)
ENV_OVERRIDES=$(terraform output -json env_overrides 2>/dev/null || echo "{}")
OBSERVABILITY_STACK=$(terraform output -raw observability_stack 2>/dev/null || echo "none")

echo "=== Observal EC2 Deploy ==="
echo "  Instance:  $INSTANCE_ID"
echo "  IP:        $PUBLIC_IP"
echo "  Region:    $REGION"
echo "  Domain:    ${DOMAIN:-"(none — HTTP only)"}"
echo "  Image:     ghcr.io/agenticresourceregistry/observal-api:$IMAGE_TAG"
echo "  Observability: $OBSERVABILITY_STACK"
echo ""

# ── Helper: run command on instance via SSM ──────────────────────────────────

run_remote() {
  local cmd="$1"
  local timeout="${2:-600}"

  local cmd_id
  cmd_id=$(aws ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "{\"commands\":[\"$cmd\"]}" \
    --timeout-seconds "$timeout" \
    --region "$REGION" \
    --query "Command.CommandId" \
    --output text)

  # Poll for completion
  local status="InProgress"
  while [ "$status" = "InProgress" ] || [ "$status" = "Pending" ]; do
    sleep 5
    status=$(aws ssm get-command-invocation \
      --command-id "$cmd_id" \
      --instance-id "$INSTANCE_ID" \
      --region "$REGION" \
      --query "Status" \
      --output text 2>/dev/null || echo "InProgress")
  done

  if [ "$status" != "Success" ]; then
    echo "ERROR: Command failed with status: $status"
    aws ssm get-command-invocation \
      --command-id "$cmd_id" \
      --instance-id "$INSTANCE_ID" \
      --region "$REGION" \
      --query "StandardErrorContent" \
      --output text 2>/dev/null || true
    return 1
  fi

  # Print output
  aws ssm get-command-invocation \
    --command-id "$cmd_id" \
    --instance-id "$INSTANCE_ID" \
    --region "$REGION" \
    --query "StandardOutputContent" \
    --output text 2>/dev/null || true
}

# ── Wait for SSM agent to come online ────────────────────────────────────────

echo "Waiting for instance to be reachable via SSM..."
for i in $(seq 1 60); do
  online=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --region "$REGION" \
    --query "InstanceInformationList[0].PingStatus" \
    --output text 2>/dev/null || echo "None")
  if [ "$online" = "Online" ]; then
    echo "  SSM agent online."
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: Instance not reachable via SSM after 5 minutes."
    exit 1
  fi
  sleep 5
done

# ── Wait for startup script to finish ────────────────────────────────────────

echo "Waiting for instance startup script to complete..."
for i in $(seq 1 60); do
  result=$(run_remote "test -f /var/run/observal-startup-complete && echo done || echo waiting" 30 2>/dev/null || echo "waiting")
  if echo "$result" | grep -q "done"; then
    echo "  Startup complete."
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: Startup script did not complete after 5 minutes."
    exit 1
  fi
  sleep 5
done

# ── Deploy server package (pre-built images from GHCR) ───────────────────────

echo "Setting up Observal server package..."

# Clone only the server-package config files (nginx, grafana, clickhouse configs)
run_remote "rm -rf /opt/observal && git clone --depth 1 --branch $OBSERVAL_REF $OBSERVAL_REPO /opt/observal-src && mkdir -p /opt/observal && cp /opt/observal-src/docker/server-package/* /opt/observal/ && cp -r /opt/observal-src/docker/clickhouse /opt/observal/ 2>/dev/null || true && cp -r /opt/observal-src/docker/server-package/grafana /opt/observal/ 2>/dev/null || true && cp -r /opt/observal-src/docker/server-package/prometheus* /opt/observal/ 2>/dev/null || true && rm -rf /opt/observal-src"

# ── Configure .env ───────────────────────────────────────────────────────────

echo "Configuring environment..."
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
POSTGRES_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -base64 18)
CLICKHOUSE_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -base64 18)
GRAFANA_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -base64 18)

FRONTEND_URL="${DOMAIN:+https://$DOMAIN}"
FRONTEND_URL="${FRONTEND_URL:-http://$PUBLIC_IP}"

# Generate .env from template
run_remote "cd /opt/observal && cp env.template .env && sed -i 's|__SECRET_KEY__|$SECRET_KEY|g' .env && sed -i 's|__POSTGRES_PASSWORD__|$POSTGRES_PW|g' .env && sed -i 's|__CLICKHOUSE_PASSWORD__|$CLICKHOUSE_PW|g' .env && sed -i 's|__FRONTEND_URL__|$FRONTEND_URL|g' .env && echo 'OBSERVAL_VERSION=$IMAGE_TAG' >> .env && chmod 600 .env"

if [ "$OBSERVABILITY_STACK" = "grafana" ]; then
  run_remote "cd /opt/observal && echo 'GRAFANA_ADMIN_USER=admin' >> .env && echo 'GRAFANA_ADMIN_PASSWORD=$GRAFANA_PW' >> .env"
fi

# Apply env overrides (skip empty values)
while IFS='=' read -r key value; do
  [ -z "$key" ] && continue
  [ -z "$value" ] && continue
  run_remote "cd /opt/observal && sed -i \"s|${key}=.*|${key}=${value}|\" .env || echo '${key}=${value}' >> .env"
done < <(echo "$ENV_OVERRIDES" | python3 -c "import sys,json; [print(f'{k}={v}') for k,v in json.load(sys.stdin).items()]" 2>/dev/null || true)

# ── Configure TLS (if domain set) ───────────────────────────────────────────

if [ -n "$DOMAIN" ]; then
  echo "Obtaining TLS certificate for $DOMAIN..."
  run_remote "certbot certonly --standalone -d $DOMAIN --non-interactive --agree-tos -m admin@$DOMAIN"
fi

# ── Pull and start (pre-built images — fast) ────────────────────────────────

COMPOSE_FILES="-f docker-compose.yml"
COMPOSE_PROFILE_ARGS=""
if [ "$OBSERVABILITY_STACK" != "none" ]; then
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.observability.yml"
fi
if [ "$OBSERVABILITY_STACK" = "grafana" ]; then
  COMPOSE_PROFILE_ARGS="--profile grafana"
fi

echo "Pulling pre-built images from GHCR..."
run_remote "cd /opt/observal && docker compose $COMPOSE_PROFILE_ARGS $COMPOSE_FILES pull" 300

echo "Starting services..."
run_remote "cd /opt/observal && docker compose $COMPOSE_PROFILE_ARGS $COMPOSE_FILES --env-file .env up -d"

# ── Health check ─────────────────────────────────────────────────────────────

echo "Waiting for Observal to become healthy..."
URL="${DOMAIN:+https://$DOMAIN}"
URL="${URL:-http://$PUBLIC_IP}"

for i in $(seq 1 40); do
  status=$(curl -sf -o /dev/null -w "%{http_code}" "$URL/readyz" 2>/dev/null || echo "000")
  if [ "$status" = "200" ]; then
    echo ""
    echo "=== Observal is live ==="
    echo "  URL: $URL"
    echo "  SSM: aws ssm start-session --target $INSTANCE_ID --region $REGION"
    echo ""
    echo "  Default login: super@demo.example / super-changeme"
    echo ""
    exit 0
  fi
  printf "."
  sleep 15
done

echo ""
echo "WARNING: Health check did not pass within 10 minutes."
echo "Services may still be starting. Check with:"
echo "  aws ssm start-session --target $INSTANCE_ID --region $REGION"
echo "  sudo docker compose -f /opt/observal/docker-compose.yml ps"
echo ""
exit 1
