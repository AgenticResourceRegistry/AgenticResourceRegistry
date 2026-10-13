#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0
#
# Unified Observal AWS Deployment
# ================================
# Interactive guided setup for all deployment tiers.
#
# Usage:
#   ./deploy.sh          # Full guided flow
#   ./deploy.sh --help   # Show usage

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Output formatting ────────────────────────────────────────────────────────
PASS='\033[0;32m✓\033[0m'
FAIL='\033[0;31m✗\033[0m'
WARN='\033[1;33m!\033[0m'
INFO='\033[0;36m→\033[0m'
BOLD='\033[1m'
NC='\033[0m'

ERRORS=0

pass() { echo -e "  ${PASS} $*"; }
fail() { echo -e "  ${FAIL} $*"; ERRORS=$((ERRORS + 1)); }
warn() { echo -e "  ${WARN} $*"; }
info() { echo -e "  ${INFO} $*"; }

section() {
  echo ""
  echo -e "${BOLD}$*${NC}"
  echo -e "${BOLD}$(printf '%.0s─' $(seq 1 ${#1}))${NC}"
}

# ── Help ─────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
  echo "Usage: $0"
  echo ""
  echo "Interactive guided deployment for Observal on AWS."
  echo "Walks you through tier selection, configuration, validation, and apply."
  echo ""
  echo "Tiers:"
  echo "  1) Single     — All-in-one EC2 (~\$60/mo)"
  echo "  2) Standard   — ECS EC2 + data host (~\$155/mo)"
  echo "  3) Enterprise — ECS Fargate + managed services (~\$255/mo)"
  exit 0
fi

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         Observal AWS Deployment                     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"

# ── Tier Selection ───────────────────────────────────────────────────────────
section "Choose Deployment Tier"
echo ""
echo -e "  ${BOLD}1) Single${NC}      — Everything on one EC2 instance"
echo "                   Docker Compose, ~\$60/mo"
echo "                   Best for: dev, demos, small teams (<20 users)"
echo ""
echo -e "  ${BOLD}2) Standard${NC}    — ECS on EC2 + separate data host"
echo "                   Rolling deploys, scalable app tier, ~\$155/mo"
echo "                   Best for: production teams, moderate load"
echo ""
echo -e "  ${BOLD}3) Enterprise${NC}  — ECS Fargate + RDS + ElastiCache"
echo "                   Fully managed, autoscaling, HA, ~\$255/mo"
echo "                   Best for: large orgs, compliance, SLA-required"
echo ""
read -rp "  Choose tier [1/2/3]: " TIER_CHOICE

case "$TIER_CHOICE" in
  1) MODULE_DIR="$SCRIPT_DIR/aws-ec2";     TIER_NAME="Single";     TIER_COST="~\$60/mo" ;;
  2) MODULE_DIR="$SCRIPT_DIR/aws-standard"; TIER_NAME="Standard";   TIER_COST="~\$155/mo" ;;
  3) MODULE_DIR="$SCRIPT_DIR/aws";          TIER_NAME="Enterprise"; TIER_COST="~\$255/mo" ;;
  *)
    echo "Invalid choice. Enter 1, 2, or 3."
    exit 1
    ;;
esac

echo ""
info "Selected: ${BOLD}$TIER_NAME${NC} ($TIER_COST)"

# ── Pre-flight: Terraform ────────────────────────────────────────────────────
section "Pre-flight Checks"

TF=""
if command -v terraform >/dev/null 2>&1; then
  TF="terraform"
elif command -v tofu >/dev/null 2>&1; then
  TF="tofu"
fi

if [ -z "$TF" ]; then
  fail "terraform/tofu not found"
  info "Install: https://developer.hashicorp.com/terraform/install"
  echo ""
  exit 1
else
  TF_VERSION=$($TF version -json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("terraform_version","0.0.0"))' 2>/dev/null || echo "0.0.0")
  TF_MAJOR=$(echo "$TF_VERSION" | cut -d. -f1)
  TF_MINOR=$(echo "$TF_VERSION" | cut -d. -f2)
  if [ "$TF_MAJOR" -lt 1 ] || ([ "$TF_MAJOR" -eq 1 ] && [ "$TF_MINOR" -lt 6 ]); then
    fail "$TF version $TF_VERSION < 1.6.0 (required)"
    exit 1
  else
    pass "$TF $TF_VERSION"
  fi
fi

# ── Pre-flight: AWS credentials ──────────────────────────────────────────────
if ! command -v aws >/dev/null 2>&1; then
  fail "aws CLI not found"
  info "Install: https://aws.amazon.com/cli/"
  exit 1
else
  pass "aws CLI installed"
  if CALLER=$(aws sts get-caller-identity --output json 2>/dev/null); then
    ARN=$(echo "$CALLER" | python3 -c 'import sys,json;print(json.load(sys.stdin)["Arn"])')
    pass "Authenticated: $ARN"
  else
    fail "AWS credentials not configured or expired"
    info "Run: aws configure"
    exit 1
  fi
fi

# ── terraform.tfvars setup ───────────────────────────────────────────────────
section "Configuration"

TFVARS="$MODULE_DIR/terraform.tfvars"
TFVARS_EXAMPLE="$MODULE_DIR/terraform.tfvars.example"

if [ ! -f "$TFVARS" ]; then
  if [ -f "$TFVARS_EXAMPLE" ]; then
    cp "$TFVARS_EXAMPLE" "$TFVARS"
    pass "Created terraform.tfvars from example"
  else
    fail "No terraform.tfvars.example found in $MODULE_DIR"
    exit 1
  fi
else
  pass "terraform.tfvars exists"
fi

echo ""
echo -e "  ${BOLD}Edit your configuration:${NC}"
echo -e "    $TFVARS"
echo ""
echo "  At minimum, set: region"
if [ "$TIER_CHOICE" != "1" ]; then
  echo "  For HTTPS: set domain_name + route53_zone_id"
fi
echo ""

# ── Validation loop ──────────────────────────────────────────────────────────

validate_single() {
  ERRORS=0
  local get_var
  get_var() { grep "^$1" "$TFVARS" 2>/dev/null | sed 's/.*=\s*"\(.*\)"/\1/' | head -1; }

  local region name image_tag domain zone
  region=$(get_var "region")
  name=$(get_var "name")
  image_tag=$(get_var "image_tag")
  domain=$(get_var "domain")
  zone=$(get_var "route53_zone_id")

  [ -n "$region" ] && pass "region = $region" || fail "region not set"
  [ -n "$name" ] && pass "name = $name" || fail "name not set (deployment identifier)"
  [ -n "$image_tag" ] && pass "image_tag = $image_tag" || pass "image_tag not set (will use 'latest')"

  if [ -n "$domain" ] && [ -z "$zone" ]; then
    fail "domain is set but route53_zone_id is missing"
  elif [ -n "$domain" ]; then
    pass "domain = $domain"
  fi
}

validate_standard() {
  ERRORS=0
  local get_var
  get_var() { grep "^$1" "$TFVARS" 2>/dev/null | sed 's/.*=\s*"\(.*\)"/\1/' | head -1; }

  local region environment image_tag domain zone vpc_id

  region=$(get_var "region")
  environment=$(get_var "environment")
  image_tag=$(get_var "image_tag")
  domain=$(get_var "domain_name")
  zone=$(get_var "route53_zone_id")
  vpc_id=$(get_var "vpc_id")

  [ -n "$region" ] && pass "region = $region" || fail "region not set"
  [ -n "$environment" ] && pass "environment = $environment" || pass "environment not set (defaults to 'prod')"
  [ -n "$image_tag" ] && pass "image_tag = $image_tag" || pass "image_tag not set (will use 'latest')"

  if [ -n "$domain" ] && [ -z "$zone" ]; then
    fail "domain_name is set but route53_zone_id is missing"
  elif [ -n "$domain" ]; then
    pass "domain = $domain (HTTPS)"
  else
    pass "No domain (HTTP on ALB DNS)"
  fi

  if [ -n "$vpc_id" ]; then
    local priv pub
    priv=$(grep "private_subnet_ids" "$TFVARS" 2>/dev/null || echo "")
    pub=$(grep "public_subnet_ids" "$TFVARS" 2>/dev/null || echo "")
    [ -n "$priv" ] && pass "private_subnet_ids set" || fail "vpc_id set but private_subnet_ids missing"
    [ -n "$pub" ] && pass "public_subnet_ids set" || fail "vpc_id set but public_subnet_ids missing"
  fi

  # Check for resource naming conflicts
  local prefix="${environment:-prod}"
  local full_name="observal-${prefix}"
  local region_flag="--region ${region:-us-east-1}"
  local existing_alb
  existing_alb=$(aws elbv2 describe-load-balancers $region_flag --names "${full_name}-alb" --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "None")
  if [ "$existing_alb" != "None" ] && [ -n "$existing_alb" ]; then
    fail "ALB '${full_name}-alb' already exists in this account/region"
    info "Change name_prefix in terraform.tfvars or delete the existing ALB first"
  fi

  # Check GHCR image accessibility (requires token even for public images)
  local tag="${image_tag:-latest}"
  local token status
  # Python performs the HTTP fetch + JSON parse directly so no remote content
  # is piped into an interpreter (Scorecard supply-chain check).
  token=$(python3 -c 'import json,urllib.request
try:
    with urllib.request.urlopen("https://ghcr.io/token?scope=repository:observal/observal-api:pull", timeout=10) as r:
        print(json.load(r).get("token",""))
except Exception:
    pass
' 2>/dev/null || echo "")
  if [ -n "$token" ]; then
    status=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $token" -H "Accept: application/vnd.docker.distribution.manifest.v2+json" "https://ghcr.io/v2/observal/observal-api/manifests/$tag" 2>/dev/null || echo "000")
  else
    status="000"
  fi
  if [ "$status" = "200" ]; then
    pass "GHCR image observal-api:$tag reachable"
  else
    warn "Cannot verify GHCR image observal-api:$tag (HTTP $status)"
  fi
}

validate_enterprise() {
  ERRORS=0
  local get_var
  get_var() { grep "^$1" "$TFVARS" 2>/dev/null | sed 's/.*=\s*"\(.*\)"/\1/' | head -1; }

  local region environment image_tag domain zone vpc_id

  region=$(get_var "region")
  environment=$(get_var "environment")
  image_tag=$(get_var "image_tag")
  domain=$(get_var "domain_name")
  zone=$(get_var "route53_zone_id")
  vpc_id=$(get_var "vpc_id")

  [ -n "$region" ] && pass "region = $region" || fail "region not set"
  [ -n "$environment" ] && pass "environment = $environment" || pass "environment defaults to 'prod'"
  [ -n "$image_tag" ] && pass "image_tag = $image_tag" || pass "image_tag defaults to 'latest'"

  if [ -n "$domain" ] && [ -z "$zone" ]; then
    fail "domain_name is set but route53_zone_id is missing"
  elif [ -n "$domain" ]; then
    pass "domain = $domain (HTTPS)"
  fi

  if [ -n "$vpc_id" ]; then
    local priv pub
    priv=$(grep "private_subnet_ids" "$TFVARS" 2>/dev/null || echo "")
    pub=$(grep "public_subnet_ids" "$TFVARS" 2>/dev/null || echo "")
    [ -n "$priv" ] && pass "private_subnet_ids set" || fail "vpc_id set but private_subnet_ids missing"
    [ -n "$pub" ] && pass "public_subnet_ids set" || fail "vpc_id set but public_subnet_ids missing"
  fi

  # IAM smoke test
  if aws ecs list-clusters --region "${region:-us-east-1}" --max-results 1 >/dev/null 2>&1; then
    pass "ECS access confirmed"
  else
    fail "Cannot access ECS (check IAM permissions)"
  fi
}

# Run validation loop
while true; do
  section "Validating Configuration"

  case "$TIER_CHOICE" in
    1) validate_single ;;
    2) validate_standard ;;
    3) validate_enterprise ;;
  esac

  if [ "$ERRORS" -eq 0 ]; then
    echo ""
    pass "All checks passed."
    break
  else
    echo ""
    echo -e "  ${FAIL} ${BOLD}$ERRORS error(s) found.${NC}"
    echo ""
    echo "  Fix the issues in: $TFVARS"
    echo ""
    read -rp "  Press Enter to re-validate (or 'quit' to abort): " RESP
    [ "$RESP" = "quit" ] && exit 0
  fi
done

# ── Summary ──────────────────────────────────────────────────────────────────
section "Deployment Summary"
echo ""
echo -e "  Tier:     ${BOLD}$TIER_NAME${NC}"
echo -e "  Cost:     $TIER_COST"
echo -e "  Module:   $MODULE_DIR"
echo -e "  Config:   $TFVARS"
echo ""

# ── Apply ────────────────────────────────────────────────────────────────────
read -rp "  Proceed with deployment? [y/N]: " PROCEED
PROCEED="${PROCEED:-N}"

if [[ ! "$PROCEED" =~ ^[Yy] ]]; then
  echo ""
  info "Aborted. To deploy manually:"
  echo "    cd $MODULE_DIR"
  echo "    $TF init && $TF plan -out=tfplan && $TF apply tfplan"
  echo ""
  exit 0
fi

section "Deploying ($TIER_NAME)"

echo ""
info "Running: $TF -chdir=$MODULE_DIR init"
if ! $TF -chdir="$MODULE_DIR" init; then
  fail "terraform init failed"
  exit 1
fi

echo ""
info "Running: $TF -chdir=$MODULE_DIR plan -out=tfplan"
if ! $TF -chdir="$MODULE_DIR" plan -out=tfplan; then
  fail "terraform plan failed"
  exit 1
fi

echo ""
read -rp "  Plan generated. Apply now? [y/N]: " CONFIRM
CONFIRM="${CONFIRM:-N}"

if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
  info "Skipped. Apply manually: $TF -chdir=$MODULE_DIR apply tfplan"
  exit 0
fi

echo ""
info "Applying (live output below)..."
echo ""
$TF -chdir="$MODULE_DIR" apply tfplan 2>&1 | tee /tmp/observal-apply-output.log
APPLY_EXIT=${PIPESTATUS[0]}

if [ "$APPLY_EXIT" -ne 0 ]; then
  echo ""

  if grep -qi "already exists\|AlreadyExists\|BucketAlreadyExists\|EntityAlreadyExists\|ResourceAlreadyExistsException\|ParameterAlreadyExists" /tmp/observal-apply-output.log 2>/dev/null; then
    echo -e "  ${FAIL} ${BOLD}Resource conflict: some AWS resources already exist.${NC}"
    echo ""
    echo "  This usually means a previous deployment with the same name_prefix + environment"
    echo "  left resources behind (no terraform state to track them)."
    echo ""
    echo -e "  ${BOLD}Options to fix:${NC}"
    echo ""
    echo "  1. Use a different name (easiest):"
    echo "     Edit $TFVARS and change:"
    echo "       name_prefix = \"observal-v2\"   # or any unique name"
    echo "     Then re-run this script."
    echo ""
    echo "  2. Delete the old resources first:"
    echo "     The conflicting resources are listed in the errors above."
    echo "     Delete them via AWS Console or CLI, then re-run."
    echo ""
    echo "  3. Import them into Terraform state:"
    echo "     terraform -chdir=$MODULE_DIR import <resource_address> <resource_id>"
    echo "     (advanced — run for each conflicting resource)"
    echo ""
  else
    fail "terraform apply failed (see errors above)"
  fi
  rm -f /tmp/observal-apply-output.log
  exit 1
fi
rm -f /tmp/observal-apply-output.log

# ── Post-apply ───────────────────────────────────────────────────────────────
section "Post-Deploy"

case "$TIER_CHOICE" in
  1)
    echo ""
    info "Running application deployment to EC2 via SSM..."
    echo ""
    (cd "$MODULE_DIR" && ./deploy.sh)
    ;;
  2|3)
    echo ""
    info "ECS services are pulling images from GHCR and starting..."
    info "This typically takes 2-4 minutes."
    echo ""

    APP_URL=$($TF -chdir="$MODULE_DIR" output -raw app_url 2>/dev/null || echo "")
    if [ -n "$APP_URL" ]; then
      echo "  Waiting for $APP_URL/readyz ..."
      for i in $(seq 1 30); do
        status=$(curl -sf -o /dev/null -w "%{http_code}" "$APP_URL/readyz" 2>/dev/null || echo "000")
        if [ "$status" = "200" ]; then
          echo ""
          pass "Observal is live!"
          break
        fi
        printf "."
        sleep 10
      done
      if [ "$status" != "200" ]; then
        echo ""
        warn "Health check did not pass yet. Services may still be starting."
        info "Check: $TF -chdir=$MODULE_DIR output"
      fi
    fi
    ;;
esac

# ── Final output ─────────────────────────────────────────────────────────────
echo ""
section "Deployment Complete"
echo ""
echo "  Useful commands:"
echo ""
echo -e "  ${BOLD}View outputs:${NC}    $TF -chdir=$MODULE_DIR output"
echo -e "  ${BOLD}View logs:${NC}       $TF -chdir=$MODULE_DIR output log_group_names"
echo -e "  ${BOLD}Destroy:${NC}         $TF -chdir=$MODULE_DIR destroy"
echo ""
echo "  Get started:"
echo -e "  ${BOLD}1.${NC} observal config set server_url <app_url>"
echo -e "  ${BOLD}2.${NC} observal auth login"
echo ""
