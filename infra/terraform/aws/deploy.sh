#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0
#
# deploy.sh
# =========
# Pre-flight validation for Observal AWS deployment.
# Checks everything that can go wrong BEFORE you run terraform.
# Does NOT run init/plan/apply. Tells you exactly what to do.
#
# Usage:
#   ./deploy.sh                    # Run all checks
#   ./deploy.sh --generate-tfvars  # Interactive tfvars generation
#   ./deploy.sh --check-only       # Validate existing setup
#
# This script is idempotent. Run it as many times as you want.

set -uo pipefail

# ── Output formatting ────────────────────────────────────────────────────────
PASS='\033[0;32m✓\033[0m'
FAIL='\033[0;31m✗\033[0m'
WARN='\033[1;33m!\033[0m'
INFO='\033[0;36m→\033[0m'
BOLD='\033[1m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

pass() { echo -e "  ${PASS} $*"; }
fail() { echo -e "  ${FAIL} $*"; ERRORS=$((ERRORS + 1)); }
warn() { echo -e "  ${WARN} $*"; WARNINGS=$((WARNINGS + 1)); }
info() { echo -e "  ${INFO} $*"; }

section() {
  echo ""
  echo -e "${BOLD}$*${NC}"
  echo -e "${BOLD}$(printf '%.0s─' $(seq 1 ${#1}))${NC}"
}

# ── Parse flags ──────────────────────────────────────────────────────────────
MODE="full"
for arg in "$@"; do
  case "$arg" in
    --generate-tfvars) MODE="generate" ;;
    --check-only)      MODE="check" ;;
    --help|-h)
      echo "Usage: $0 [--generate-tfvars|--check-only|--help]"
      echo ""
      echo "  (no flags)         Run all checks, offer to generate tfvars if missing"
      echo "  --generate-tfvars  Interactive terraform.tfvars generation"
      echo "  --check-only       Validate existing terraform.tfvars and environment"
      exit 0
      ;;
    *) echo "Unknown flag: $arg. Use --help."; exit 1 ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        Observal Deployment Doctor                   ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"

# ── Check 1: Terraform binary ────────────────────────────────────────────────
section "1. Terraform"

TF=""
if command -v terraform >/dev/null 2>&1; then
  TF="terraform"
elif command -v tofu >/dev/null 2>&1; then
  TF="tofu"
fi

if [ -z "$TF" ]; then
  fail "terraform/tofu not found"
  info "Install: https://developer.hashicorp.com/terraform/install"
  info "Or: brew install terraform"
else
  TF_VERSION=$($TF version -json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("terraform_version","0.0.0"))' 2>/dev/null || echo "0.0.0")
  TF_MAJOR=$(echo "$TF_VERSION" | cut -d. -f1)
  TF_MINOR=$(echo "$TF_VERSION" | cut -d. -f2)
  if [ "$TF_MAJOR" -lt 1 ] || ([ "$TF_MAJOR" -eq 1 ] && [ "$TF_MINOR" -lt 6 ]); then
    fail "$TF version $TF_VERSION < 1.6.0 (required)"
    info "Upgrade: brew upgrade terraform"
  else
    pass "$TF $TF_VERSION"
  fi
fi

# ── Check 2: AWS CLI + credentials ──────────────────────────────────────────
section "2. AWS Credentials"

if ! command -v aws >/dev/null 2>&1; then
  fail "aws CLI not found"
  info "Install: https://aws.amazon.com/cli/"
else
  pass "aws CLI installed"

  if CALLER=$(aws sts get-caller-identity --output json 2>/dev/null); then
    ACCOUNT_ID=$(echo "$CALLER" | python3 -c 'import sys,json;print(json.load(sys.stdin)["Account"])')
    ARN=$(echo "$CALLER" | python3 -c 'import sys,json;print(json.load(sys.stdin)["Arn"])')
    pass "Authenticated: $ARN"
    pass "Account: $ACCOUNT_ID"
  else
    fail "AWS credentials not configured or expired"
    info "Run: aws configure"
    info "Or: export AWS_PROFILE=your-profile"
  fi
fi

# ── Check 3: terraform.tfvars ────────────────────────────────────────────────
section "3. Configuration (terraform.tfvars)"

TFVARS_EXISTS=false
if [ -f "terraform.tfvars" ]; then
  TFVARS_EXISTS=true
  pass "terraform.tfvars exists"

  # Validate key fields
  get_tfvar() { grep "^$1" terraform.tfvars 2>/dev/null | sed 's/.*=\s*"\(.*\)"/\1/' | sed "s/.*=\s*'/\1/" | head -1; }

  REGION=$(get_tfvar "region")
  IMAGE_TAG=$(get_tfvar "image_tag")
  VPC_ID=$(get_tfvar "vpc_id")
  DEMO_EMAIL=$(get_tfvar "demo_super_admin_email")

  [ -n "$REGION" ] && pass "region = $REGION" || fail "region not set"
  [ -n "$IMAGE_TAG" ] && pass "image_tag = $IMAGE_TAG" || warn "image_tag not set (will use 'latest')"

  if [ -n "$VPC_ID" ]; then
    pass "BYO-VPC mode: $VPC_ID"
    PRIV_SUBS=$(grep "private_subnet_ids" terraform.tfvars 2>/dev/null || echo "")
    PUB_SUBS=$(grep "public_subnet_ids" terraform.tfvars 2>/dev/null || echo "")
    [ -n "$PRIV_SUBS" ] && pass "private_subnet_ids set" || fail "private_subnet_ids required with vpc_id"
    [ -n "$PUB_SUBS" ] && pass "public_subnet_ids set" || fail "public_subnet_ids required with vpc_id"
  else
    pass "VPC mode: create new"
  fi

  [ -n "$DEMO_EMAIL" ] && pass "Demo accounts configured" || warn "No demo accounts (you'll need to bootstrap manually via observal auth login)"
else
  if [ "$MODE" = "check" ]; then
    fail "terraform.tfvars not found"
    info "Run: $0 --generate-tfvars"
  else
    warn "terraform.tfvars not found (will generate below)"
  fi
fi

# ── Check 4: Docker images exist ─────────────────────────────────────────────
section "4. Container Images"

check_ghcr_image() {
  local image="$1"
  local tag="$2"
  # Use the GitHub container registry API (anonymous, no auth needed for public images)
  local url="https://ghcr.io/v2/agenticresourceregistry/$image/manifests/$tag"
  local status
  status=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    "$url" 2>/dev/null)
  [ "$status" = "200" ] || [ "$status" = "302" ]
}

TAG="${IMAGE_TAG:-latest}"
for img in observal-api observal-web; do
  if check_ghcr_image "$img" "$TAG"; then
    pass "ghcr.io/agenticresourceregistry/$img:$TAG exists"
  else
    # Try with token auth. Python performs the HTTP fetch + JSON parse directly
    # so no remote content is piped into an interpreter (Scorecard supply-chain check).
    TOKEN=$(GHCR_IMG="$img" python3 -c 'import json,os,urllib.request
try:
    url = "https://ghcr.io/token?scope=repository:agenticresourceregistry/" + os.environ["GHCR_IMG"] + ":pull"
    with urllib.request.urlopen(url, timeout=10) as r:
        print(json.load(r).get("token", ""))
except Exception:
    pass
' 2>/dev/null || echo "")
    if [ -n "$TOKEN" ]; then
      status=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
        -H "Authorization: Bearer $TOKEN" \
        "https://ghcr.io/v2/agenticresourceregistry/$img/manifests/$TAG" 2>/dev/null)
      if [ "$status" = "200" ]; then
        pass "ghcr.io/agenticresourceregistry/$img:$TAG exists"
      else
        fail "ghcr.io/agenticresourceregistry/$img:$TAG not found (HTTP $status)"
        info "Check that a release with this tag exists, or use 'latest'"
      fi
    else
      warn "Cannot verify ghcr.io/agenticresourceregistry/$img:$TAG (auth required or network issue)"
    fi
  fi
done

# ── Check 5: Release tarball (only for non-latest tags) ──────────────────────
section "5. Release Tarball"

if [ "$TAG" = "latest" ]; then
  pass "image_tag=latest: embedded configs used (no tarball needed)"
else
  TARBALL_URL="https://github.com/AgenticResourceRegistry/AgenticResourceRegistry/releases/download/v${TAG}/observal-server-v${TAG}.tar.gz"
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -L "$TARBALL_URL" 2>/dev/null)
  if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "302" ]; then
    pass "Release tarball v$TAG exists"
  else
    warn "Release tarball not found at v$TAG (HTTP $HTTP_STATUS)"
    info "Grafana dashboards won't auto-provision. Core deployment still works."
    info "Embedded ClickHouse configs will be used as fallback."
  fi
fi

# ── Check 6: BYO-VPC validation ──────────────────────────────────────────────
section "6. VPC Validation"

if [ -n "${VPC_ID:-}" ] && [ "$VPC_ID" != "" ]; then
  REGION_FLAG="--region ${REGION:-us-east-1}"

  # Check VPC exists
  VPC_STATE=$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID" $REGION_FLAG --query 'Vpcs[0].State' --output text 2>/dev/null || echo "NOT_FOUND")
  if [ "$VPC_STATE" = "available" ]; then
    pass "VPC $VPC_ID exists and is available"

    # Check DNS settings
    DNS_SUPPORT=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsSupport $REGION_FLAG --query 'EnableDnsSupport.Value' --output text 2>/dev/null)
    DNS_HOSTNAMES=$(aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsHostnames $REGION_FLAG --query 'EnableDnsHostnames.Value' --output text 2>/dev/null)
    [ "$DNS_SUPPORT" = "True" ] && pass "DNS support enabled" || fail "DNS support must be enabled on VPC"
    [ "$DNS_HOSTNAMES" = "True" ] && pass "DNS hostnames enabled" || fail "DNS hostnames must be enabled on VPC"
  else
    fail "VPC $VPC_ID not found or not available (state: $VPC_STATE)"
  fi

  # Check private subnets have route to NAT (needed for image pulls)
  if [ -n "${PRIV_SUBS:-}" ]; then
    FIRST_PRIV=$(echo "$PRIV_SUBS" | grep -o 'subnet-[a-z0-9]*' | head -1)
    if [ -n "$FIRST_PRIV" ]; then
      RT=$(aws ec2 describe-route-tables $REGION_FLAG \
        --filters "Name=association.subnet-id,Values=$FIRST_PRIV" \
        --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].[NatGatewayId,TransitGatewayId]' \
        --output text 2>/dev/null)
      if [ -n "$RT" ] && echo "$RT" | grep -qv "^None[[:space:]]*None$"; then
        pass "Private subnet $FIRST_PRIV has outbound route (NAT/TGW)"
      else
        fail "Private subnet $FIRST_PRIV has no outbound route (ECS tasks need internet for image pulls)"
        info "Add a NAT gateway, Transit Gateway, or VPC peering route to 0.0.0.0/0"
      fi
    fi
  fi
else
  pass "New VPC will be created (no validation needed)"
fi

# ── Check 7: IAM permissions ────────────────────────────────────────────────
section "7. IAM Permissions (spot check)"

# Quick smoke test: can we access key AWS services?
if aws ecs list-clusters --region "${REGION:-us-east-1}" --max-results 1 >/dev/null 2>&1; then
  pass "ECS access confirmed"
else
  fail "Cannot access ECS (check IAM permissions)"
  info "Required: AdministratorAccess or equivalent for initial deployment"
fi

if aws ec2 describe-vpcs --region "${REGION:-us-east-1}" --max-results 1 >/dev/null 2>&1; then
  pass "EC2/VPC access confirmed"
else
  fail "Cannot access EC2/VPC (check IAM permissions)"
fi

if aws ssm describe-parameters --region "${REGION:-us-east-1}" --max-results 1 >/dev/null 2>&1; then
  pass "SSM access confirmed"
else
  fail "Cannot access SSM Parameter Store (check IAM permissions)"
fi

# ── Check 8: Name conflicts ─────────────────────────────────────────────────
section "8. Resource Conflicts"

ENV_NAME=$(get_tfvar "environment" 2>/dev/null || echo "")
ENV_NAME="${ENV_NAME:-prod}"
PREFIX="observal-${ENV_NAME}"

# Check if ECS cluster already exists
EXISTING_CLUSTER=$(aws ecs describe-clusters --region "${REGION:-us-east-1}" --clusters "${PREFIX}-cluster" --query 'clusters[?status==`ACTIVE`].clusterName' --output text 2>/dev/null || echo "")
if [ -n "$EXISTING_CLUSTER" ]; then
  warn "ECS cluster '${PREFIX}-cluster' already exists (terraform will import or conflict)"
  info "If this is a re-deploy, this is expected. Use: terraform import"
else
  pass "No existing '${PREFIX}-cluster' found"
fi

# Check SSM namespace
SSM_COUNT=$(aws ssm describe-parameters --region "${REGION:-us-east-1}" --parameter-filters "Key=Name,Option=BeginsWith,Values=/${PREFIX}/" --query 'length(Parameters)' --output text 2>/dev/null || echo "0")
if [ "$SSM_COUNT" != "0" ] && [ "$SSM_COUNT" != "None" ]; then
  warn "$SSM_COUNT existing SSM parameters under /${PREFIX}/ (previous deployment?)"
  info "If re-deploying, terraform state should track these. Otherwise clean up first."
else
  pass "No existing SSM parameters under /${PREFIX}/"
fi

# ── Check 9: Terraform state ────────────────────────────────────────────────
section "9. Terraform State"

if [ -f "terraform.tfstate" ] || [ -d ".terraform" ]; then
  pass "Existing terraform state found"
  if [ -n "$TF" ] && [ -d ".terraform" ]; then
    if $TF validate >/dev/null 2>&1; then
      pass "terraform validate passes"
    else
      fail "terraform validate failed"
      info "Run: $TF validate (to see errors)"
    fi
  fi
else
  pass "Fresh deployment (no existing state)"
  info "Run: $TF init (after fixing any errors above)"
fi

# ── Generate tfvars (interactive) ────────────────────────────────────────────
if [ "$TFVARS_EXISTS" = "false" ] && [ "$MODE" != "check" ]; then
  section "Generate terraform.tfvars"
  echo ""
  read -rp "  Generate terraform.tfvars now? [Y/n]: " GEN
  GEN="${GEN:-Y}"

  if [[ "$GEN" =~ ^[Yy] ]]; then
    echo ""

    read -rp "  AWS Region [us-east-1]: " V_REGION
    V_REGION="${V_REGION:-us-east-1}"

    read -rp "  Environment [prod]: " V_ENV
    V_ENV="${V_ENV:-prod}"

    read -rp "  Image tag [latest]: " V_TAG
    V_TAG="${V_TAG:-latest}"

    echo ""
    echo "  Sizing (determines CPU, memory, instance types, replica counts):"
    echo "    small  — evaluation / dev  (~\$150/mo): 1× api, 1× web, t3.medium data"
    echo "    medium — production team   (~\$255/mo): 2× api, 2× web, t3.large data"
    echo "    large  — enterprise scale  (~\$600/mo): 3× api, 3× web, r6i.xlarge data"
    echo "    custom — set individual sizes in terraform.tfvars manually"
    read -rp "  Sizing [medium]: " V_SIZING
    V_SIZING="${V_SIZING:-medium}"

    echo ""
    read -rp "  Use existing VPC? [y/N]: " V_BYO
    V_BYO="${V_BYO:-N}"
    V_VPC="" V_PRIV="" V_PUB=""
    if [[ "$V_BYO" =~ ^[Yy] ]]; then
      read -rp "  VPC ID: " V_VPC
      read -rp "  Private subnet IDs (comma-separated): " V_PRIV
      read -rp "  Public subnet IDs (comma-separated): " V_PUB
    fi

    echo ""
    read -rp "  Create demo accounts? [Y/n]: " V_DEMO
    V_DEMO="${V_DEMO:-Y}"
    V_DEMO_EMAIL="" V_DEMO_PASS=""
    if [[ "$V_DEMO" =~ ^[Yy] ]]; then
      read -rp "  Admin email [admin@observal.local]: " V_DEMO_EMAIL
      V_DEMO_EMAIL="${V_DEMO_EMAIL:-admin@observal.local}"
      read -rsp "  Admin password [observal-demo-2026]: " V_DEMO_PASS
      V_DEMO_PASS="${V_DEMO_PASS:-observal-demo-2026}"
      echo ""
    fi

    echo ""
    read -rp "  Custom domain (empty = ALB URL): " V_DOMAIN
    V_ZONE=""
    if [ -n "$V_DOMAIN" ]; then
      read -rp "  Route53 zone ID for $V_DOMAIN: " V_ZONE
    fi

    # Write tfvars
    cat > terraform.tfvars <<EOF
# Generated by observal-deploy-doctor.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)

region      = "$V_REGION"
environment = "$V_ENV"
name_prefix = "observal"
image_tag   = "$V_TAG"
EOF

    [ "$V_SIZING" != "custom" ] && echo "sizing = \"$V_SIZING\"" >> terraform.tfvars
    [ -n "$V_DOMAIN" ] && echo "domain_name     = \"$V_DOMAIN\"" >> terraform.tfvars
    [ -n "$V_ZONE" ] && echo "route53_zone_id = \"$V_ZONE\"" >> terraform.tfvars

    if [ -n "$V_VPC" ]; then
      PRIV_LIST=$(echo "$V_PRIV" | sed 's/ //g' | sed 's/,/", "/g')
      PUB_LIST=$(echo "$V_PUB" | sed 's/ //g' | sed 's/,/", "/g')
      cat >> terraform.tfvars <<EOF
vpc_id             = "$V_VPC"
private_subnet_ids = ["$PRIV_LIST"]
public_subnet_ids  = ["$PUB_LIST"]
EOF
    fi

    if [[ "${V_DEMO:-N}" =~ ^[Yy] ]]; then
      cat >> terraform.tfvars <<EOF
demo_super_admin_email    = "$V_DEMO_EMAIL"
demo_super_admin_password = "$V_DEMO_PASS"
demo_admin_email          = "demo-admin@observal.local"
demo_admin_password       = "$V_DEMO_PASS"
demo_reviewer_email       = "demo-reviewer@observal.local"
demo_reviewer_password    = "$V_DEMO_PASS"
demo_user_email           = "demo-user@observal.local"
demo_user_password        = "$V_DEMO_PASS"
EOF
    fi

    echo ""
    pass "Written: terraform.tfvars"
  fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
section "Summary"

if [ "$ERRORS" -gt 0 ]; then
  echo ""
  echo -e "  ${FAIL} ${BOLD}$ERRORS error(s)${NC} found. Fix them before deploying."
  [ "$WARNINGS" -gt 0 ] && echo -e "  ${WARN} $WARNINGS warning(s) (non-blocking)"
  echo ""
  echo -e "  Re-run this script after fixing to verify: ${BOLD}$0${NC}"
  echo ""
  exit 1
fi

echo ""
if [ "$WARNINGS" -gt 0 ]; then
  echo -e "  ${WARN} $WARNINGS warning(s) (non-blocking, deployment will still work)"
fi
echo -e "  ${PASS} ${BOLD}All checks passed.${NC}"
echo ""

# ── Optional: Auto-apply ────────────────────────────────────────────────────
if [ -n "$TF" ] && [ -f "terraform.tfvars" ]; then
  echo ""
  read -rp "  Run terraform init + plan + apply now? [y/N]: " V_APPLY
  V_APPLY="${V_APPLY:-N}"
  if [[ "$V_APPLY" =~ ^[Yy] ]]; then
    section "Deploying"
    echo ""
    info "Running: $TF init"
    if ! $TF init; then
      fail "terraform init failed"
      exit 1
    fi
    echo ""
    info "Running: $TF plan -out=tfplan"
    if ! $TF plan -out=tfplan; then
      fail "terraform plan failed"
      exit 1
    fi
    echo ""
    read -rp "  Plan looks good. Apply? [y/N]: " V_CONFIRM
    V_CONFIRM="${V_CONFIRM:-N}"
    if [[ "$V_CONFIRM" =~ ^[Yy] ]]; then
      $TF apply tfplan
      echo ""
      pass "Deployment complete!"
      echo ""
      echo -e "  App URL: ${BOLD}$($TF output -raw app_url 2>/dev/null || echo '(check: terraform output app_url)')${NC}"
      echo ""
      echo "  After deployment:"
      echo -e "  ${BOLD}1.${NC} $TF output          # Get ALB URL and connection info"
      echo -e "  ${BOLD}2.${NC} observal config set server_url <URL>"
      echo -e "  ${BOLD}3.${NC} observal auth login  # Use demo credentials"
      echo ""
    else
      info "Skipped. Run manually: $TF apply tfplan"
    fi
  else
    section "Next Steps"
    echo ""
    echo "  Run these commands in order:"
    echo ""
    echo -e "  ${BOLD}1.${NC} $TF init"
    echo -e "  ${BOLD}2.${NC} $TF plan -out=tfplan"
    echo -e "  ${BOLD}3.${NC} Review the plan output carefully"
    echo -e "  ${BOLD}4.${NC} $TF apply tfplan"
    echo ""
    echo "  Estimated time: 8-15 minutes for first deploy."
    echo ""
    echo "  After deployment:"
    echo -e "  ${BOLD}5.${NC} $TF output          # Get ALB URL and connection info"
    echo -e "  ${BOLD}6.${NC} observal config set server_url <URL>"
    echo -e "  ${BOLD}7.${NC} observal auth login  # Use demo credentials"
    echo ""
    echo "  To tear down:"
    echo -e "     $TF destroy"
    echo ""
  fi
else
  section "Next Steps"
  echo ""
  echo "  Run these commands in order:"
  echo ""
  echo -e "  ${BOLD}1.${NC} $TF init"
  echo -e "  ${BOLD}2.${NC} $TF plan -out=tfplan"
  echo -e "  ${BOLD}3.${NC} Review the plan output carefully"
  echo -e "  ${BOLD}4.${NC} $TF apply tfplan"
  echo ""
  echo "  Estimated time: 8-15 minutes for first deploy."
  echo ""
fi
