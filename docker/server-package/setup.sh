#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

INSTALL_DIR="${OBSERVAL_INSTALL_DIR:-/opt/observal}"
ENV_FILE="$INSTALL_DIR/.env"
SECRETS_DIR="$INSTALL_DIR/secrets"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*"; }
error() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die() {
    error "$@"
    exit 1
}

prompt_with_default() {
    local var_name="$1" prompt_text="$2" default="$3" value
    printf '%s [%s]: ' "$prompt_text" "$default"
    read -r value || value=""
    printf -v "$var_name" '%s' "${value:-$default}"
}

generate_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null ||
        openssl rand -hex 32 2>/dev/null ||
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

write_secret() {
    local name="$1" value="$2"
    (umask 027 && printf '%s' "$value" >"$SECRETS_DIR/$name")
    chmod 640 "$SECRETS_DIR/$name"
}

existing_secret_value() {
    local file_name="$1" env_name="$2"
    if [ -f "$SECRETS_DIR/$file_name" ]; then
        cat "$SECRETS_DIR/$file_name"
    elif [ -f "$ENV_FILE" ]; then
        env_value "$env_name"
    fi
}

existing_or_generated_secret() {
    local value
    value=$(existing_secret_value "$1" "$2")
    printf '%s' "${value:-$(generate_secret)}"
}

env_value() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -1
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    else
        shasum -a 256 | cut -d' ' -f1
    fi
}

command -v docker >/dev/null 2>&1 || die "Docker is required. Install: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."

previous_bind=""
if [ -f "$ENV_FILE" ]; then
    previous_bind=$(env_value OBSERVAL_BIND_ADDRESS)
    if [ -z "$previous_bind" ]; then
        previous_bind="0.0.0.0"
        printf '\n# Recorded during upgrade to preserve the previous network exposure.\n' >>"$ENV_FILE"
        printf 'OBSERVAL_BIND_ADDRESS=%s\n' "$previous_bind" >>"$ENV_FILE"
        chmod 600 "$ENV_FILE"
        warn "Recorded the existing bind address as $previous_bind"
    fi
    warn "Existing configuration found at $ENV_FILE"
    printf 'Replace it with a new configuration? [y/N]: '
    read -r confirm || confirm=""
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        info "Kept the existing configuration and bind address $previous_bind"
        warn "Package files were staged, but running services were NOT upgraded or migrated."
        warn "Follow https://github.com/Observal/Observal/blob/main/docs/skill-folder-rollout.md before starting the new stack."
        exit 0
    fi
fi

info "Observal Server Setup"
prompt_with_default FRONTEND_URL "Frontend URL" "http://localhost:3000"
prompt_with_default OBSERVAL_BIND_ADDRESS "HTTP bind address" "${previous_bind:-127.0.0.1}"
if [ "$OBSERVAL_BIND_ADDRESS" != "127.0.0.1" ] && [ "$OBSERVAL_BIND_ADDRESS" != "::1" ]; then
    warn "Remote HTTP is enabled. Put this listener behind TLS unless plaintext access is intentional."
fi
prompt_with_default OBSERVABILITY_STACK "Observability stack (none, prometheus, grafana)" "none"
case "$OBSERVABILITY_STACK" in
    none | prometheus | grafana) ;;
    *) die "Choose none, prometheus, or grafana" ;;
esac

mkdir -p \
    "$SECRETS_DIR/postgres" \
    "$SECRETS_DIR/clickhouse" \
    "$SECRETS_DIR/grafana" \
    "$INSTALL_DIR/clickhouse/users.d"
chmod 750 "$SECRETS_DIR" "$SECRETS_DIR/postgres" "$SECRETS_DIR/clickhouse" "$SECRETS_DIR/grafana"
SECRET_GID=$(id -g)

POSTGRES_PASSWORD=$(existing_or_generated_secret postgres/postgres_password POSTGRES_PASSWORD)
CLICKHOUSE_PASSWORD=$(existing_or_generated_secret clickhouse/clickhouse_password CLICKHOUSE_PASSWORD)
SECRET_KEY=$(existing_or_generated_secret secret_key SECRET_KEY)
JWT_KEY_PASSWORD=$(existing_or_generated_secret jwt_key_password JWT_KEY_PASSWORD)
DEMO_SUPER_ADMIN_EMAIL=$(env_value DEMO_SUPER_ADMIN_EMAIL)
DEMO_SUPER_ADMIN_EMAIL="${DEMO_SUPER_ADMIN_EMAIL:-super@demo.example}"
DEMO_ADMIN_EMAIL=$(env_value DEMO_ADMIN_EMAIL)
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL:-admin@demo.example}"
DEMO_REVIEWER_EMAIL=$(env_value DEMO_REVIEWER_EMAIL)
DEMO_REVIEWER_EMAIL="${DEMO_REVIEWER_EMAIL:-reviewer@demo.example}"
DEMO_USER_EMAIL=$(env_value DEMO_USER_EMAIL)
DEMO_USER_EMAIL="${DEMO_USER_EMAIL:-user@demo.example}"
DEMO_SUPER_ADMIN_PASSWORD=$(existing_or_generated_secret demo_super_admin_password DEMO_SUPER_ADMIN_PASSWORD)
DEMO_ADMIN_PASSWORD=$(existing_or_generated_secret demo_admin_password DEMO_ADMIN_PASSWORD)
DEMO_REVIEWER_PASSWORD=$(existing_or_generated_secret demo_reviewer_password DEMO_REVIEWER_PASSWORD)
DEMO_USER_PASSWORD=$(existing_or_generated_secret demo_user_password DEMO_USER_PASSWORD)
GRAFANA_ADMIN_PASSWORD=$(existing_or_generated_secret grafana/grafana_admin_password GRAFANA_ADMIN_PASSWORD)

write_secret secret_key "$SECRET_KEY"
write_secret postgres/postgres_password "$POSTGRES_PASSWORD"
write_secret clickhouse/clickhouse_password "$CLICKHOUSE_PASSWORD"
write_secret grafana/clickhouse_password "$CLICKHOUSE_PASSWORD"
write_secret jwt_key_password "$JWT_KEY_PASSWORD"
write_secret demo_super_admin_password "$DEMO_SUPER_ADMIN_PASSWORD"
write_secret demo_admin_password "$DEMO_ADMIN_PASSWORD"
write_secret demo_reviewer_password "$DEMO_REVIEWER_PASSWORD"
write_secret demo_user_password "$DEMO_USER_PASSWORD"
write_secret grafana/grafana_admin_password "$GRAFANA_ADMIN_PASSWORD"

DATABASE_URL=$(existing_secret_value database_url DATABASE_URL)
CLICKHOUSE_URL=$(existing_secret_value clickhouse_url CLICKHOUSE_URL)
REDIS_URL=$(existing_secret_value redis_url REDIS_URL)
case "$DATABASE_URL" in "" | *'$'*) DATABASE_URL="postgresql+asyncpg://postgres:$POSTGRES_PASSWORD@observal-db:5432/observal" ;; esac
case "$CLICKHOUSE_URL" in "" | *'$'*) CLICKHOUSE_URL="clickhouse://default:$CLICKHOUSE_PASSWORD@observal-clickhouse:8123/observal" ;; esac
REDIS_URL="${REDIS_URL:-redis://observal-redis:6379}"
write_secret database_url "$DATABASE_URL"
write_secret clickhouse_url "$CLICKHOUSE_URL"
write_secret redis_url "$REDIS_URL"

CLICKHOUSE_PASSWORD_HASH=$(printf '%s' "$CLICKHOUSE_PASSWORD" | sha256)
rm -f "$INSTALL_DIR/clickhouse/users.d/default-user.xml"
cat >"$INSTALL_DIR/clickhouse/users.d/generated-password.xml" <<EOF
<clickhouse>
  <users>
    <default remove="remove" />
    <default>
      <profile>default</profile>
      <networks><ip>::/0</ip></networks>
      <password_sha256_hex>$CLICKHOUSE_PASSWORD_HASH</password_sha256_hex>
      <quota>default</quota>
      <access_management>0</access_management>
    </default>
  </users>
</clickhouse>
EOF
chmod 644 "$INSTALL_DIR/clickhouse/users.d/generated-password.xml"

cp "$INSTALL_DIR/env.template" "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF

OBSERVAL_BIND_ADDRESS=$OBSERVAL_BIND_ADDRESS
OBSERVAL_SECRET_GID=$SECRET_GID
FRONTEND_URL=$FRONTEND_URL
CORS_ALLOWED_ORIGINS=$FRONTEND_URL
DEMO_SUPER_ADMIN_EMAIL=$DEMO_SUPER_ADMIN_EMAIL
DEMO_ADMIN_EMAIL=$DEMO_ADMIN_EMAIL
DEMO_REVIEWER_EMAIL=$DEMO_REVIEWER_EMAIL
DEMO_USER_EMAIL=$DEMO_USER_EMAIL
EOF
chmod 600 "$ENV_FILE"

compose_args=(-f docker-compose.yml)
profile_args=()
if [ "$OBSERVABILITY_STACK" != "none" ]; then
    compose_args+=(-f docker-compose.observability.yml)
fi
if [ "$OBSERVABILITY_STACK" = "grafana" ]; then
    profile_args+=(--profile grafana)
fi

info "Starting Observal services"
cd "$INSTALL_DIR"
docker compose "${profile_args[@]}" "${compose_args[@]}" --env-file .env up -d --wait --wait-timeout 300

docker compose "${profile_args[@]}" "${compose_args[@]}" restart observal-lb

info "Observal is running"
info "Dashboard: $FRONTEND_URL"
info "HTTP bind address: $OBSERVAL_BIND_ADDRESS"
info "Config: $ENV_FILE"
printf '\nInitial administrator:\n  Email: %s\n  Password: %s\n  Password file: %s\n' \
    "$DEMO_SUPER_ADMIN_EMAIL" \
    "$DEMO_SUPER_ADMIN_PASSWORD" \
    "$SECRETS_DIR/demo_super_admin_password"
printf 'Change this password after the first login.\n'
if [ "$OBSERVABILITY_STACK" = "grafana" ]; then
    printf '\nGrafana administrator:\n  User: admin\n  Password: %s\n' "$GRAFANA_ADMIN_PASSWORD"
fi
