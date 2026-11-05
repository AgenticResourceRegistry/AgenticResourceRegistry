#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Terraform <-> App environment consistency checker.

Ensures that:
1. Common variables exist across all Terraform modules
2. Every required app env var has a Terraform provisioning path (errors if missing)
3. Optional env vars not exposed via Terraform are surfaced as warnings
4. Injected secrets/env match what config.py expects
5. .env.example stays in sync with Terraform

Stdlib-only (no pip dependencies) so it runs in CI without install steps.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODULES: dict[str, Path] = {
    "aws": ROOT / "infra" / "terraform" / "aws",
    "gcp": ROOT / "infra" / "terraform" / "gcp",
}

# ── Check 1: Common variables across modules ─────────────────────────────────

COMMON_VARS = {
    "environment",
    "name_prefix",
    "image_tag",
}

PROVIDER_SPECIFIC: dict[str, set[str]] = {
    "aws": {"region"},
    "gcp": {"region", "project_id"},
}


def parse_variables(tf_path: Path) -> set[str]:
    """Extract variable names from a variables.tf file (ignoring commented lines)."""
    content = tf_path.read_text()
    results: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        m = re.match(r'\s*variable\s+"([^"]+)"', line)
        if m:
            results.add(m.group(1))
    return results


def check_common_variables() -> list[str]:
    errors = []
    for name, path in MODULES.items():
        vars_file = path / "variables.tf"
        if not vars_file.exists():
            errors.append(f"  {name}/variables.tf does not exist")
            continue
        declared = parse_variables(vars_file)
        required = COMMON_VARS | PROVIDER_SPECIFIC.get(name, set())
        missing = required - declared
        if missing:
            errors.append(f"  {name}/variables.tf missing: {sorted(missing)}")
    return errors


# ── Check 2 & 3: App env var coverage ────────────────────────────────────────

PLACEHOLDER_DEFAULTS = {"change-me-to-a-random-string"}

# Env vars in .env.example that only apply to docker-compose (not Terraform deployments)
DOCKER_COMPOSE_ONLY = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "SEED_DEMO_ACCOUNTS",
}


def parse_dynamic_env_imports() -> set[str]:
    """Parse env vars imported once by services.dynamic_settings."""
    path = ROOT / "observal-server" / "services" / "dynamic_settings.py"
    if not path.exists():
        return set()
    content = path.read_text()
    match = re.search(r"SSO_ENV_IMPORTS\s*:\s*dict\[str, str\]\s*=\s*\{(.*?)\n\}", content, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r'"([A-Z][A-Z_0-9]+)"\s*:', match.group(1)))


def parse_settings_fields() -> dict[str, bool]:
    """Parse observal-server/config.py Settings class for field names + defaults.

    Returns dict of {FIELD_NAME: has_usable_default}.
    """
    config_path = ROOT / "observal-server" / "config.py"
    if not config_path.exists():
        print("  WARNING: observal-server/config.py not found")
        return {}

    content = config_path.read_text()

    # Extract the Settings class body
    match = re.search(r"class Settings\(BaseSettings\):(.*?)(?=\n\S|\nclass |\Z)", content, re.DOTALL)
    if not match:
        return {}

    class_body = match.group(1)
    fields: dict[str, bool] = {}

    for line in class_body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("model_config"):
            continue

        # Match: FIELD_NAME: type = default
        m = re.match(r"([A-Z][A-Z_0-9]+)\s*:\s*.+?=\s*(.+)$", line)
        if m:
            name, default = m.group(1), m.group(2).strip()
            # None is a usable default (means "optional, not configured")
            has_usable_default = default.strip("\"'") not in PLACEHOLDER_DEFAULTS
            fields[name] = has_usable_default
            continue

        # Match: FIELD_NAME: type (no default)
        m = re.match(r"([A-Z][A-Z_0-9]+)\s*:\s*\S+", line)
        if m:
            fields[m.group(1)] = False

    return fields


def parse_terraform_provisioned(module_path: Path) -> set[str]:
    """Extract env var names provisioned by Terraform for a module.

    Only matches name = "UPPER_CASE" patterns which in these .tf files
    exclusively appear inside container env blocks (environment/secrets lists
    in ecs.tf, env {} blocks in cloud-run.tf). Resource names use lowercase.
    """
    provisioned: set[str] = set()

    # secrets.tf: keys in local maps like "DATABASE_URL" = ...
    secrets_tf = module_path / "secrets.tf"
    if secrets_tf.exists():
        content = secrets_tf.read_text()
        provisioned.update(re.findall(r'"([A-Z][A-Z_0-9]+)"\s*=', content))

    # ecs.tf (AWS) or cloud-run.tf (GCP): env var names injected into containers
    for tf_file in ("ecs.tf", "cloud-run.tf"):
        path = module_path / tf_file
        if path.exists():
            content = path.read_text()
            provisioned.update(re.findall(r'name\s*=\s*"([A-Z][A-Z_0-9]+)"', content))

    # variables.tf: demo_* terraform vars map to DEMO_* env vars
    vars_tf = module_path / "variables.tf"
    if vars_tf.exists():
        content = vars_tf.read_text()
        for var_name in re.findall(r'variable\s+"(demo_[^"]+)"', content):
            provisioned.add(var_name.upper())

    return provisioned


def check_env_coverage() -> tuple[list[str], list[str]]:
    """Check env var coverage. Returns (errors, warnings).

    Errors: vars with no usable default AND no Terraform provisioning (app would crash).
    Warnings: vars with usable defaults but not exposed via Terraform (can't customize without rebuilding).
    """
    fields = parse_settings_fields()
    errors = []
    warnings = []

    for module_name, module_path in MODULES.items():
        provisioned = parse_terraform_provisioned(module_path)

        # Errors: required vars (no usable default) missing from Terraform
        required_missing = {name for name, has_default in fields.items() if not has_default} - provisioned
        if required_missing:
            errors.append(
                f"  {module_name}: config.py requires these env vars but Terraform "
                f"doesn't provision them: {sorted(required_missing)}"
            )

        # Warnings: optional vars (have defaults) not exposed via Terraform
        optional_unexposed = {name for name, has_default in fields.items() if has_default} - provisioned
        if optional_unexposed:
            warnings.append(
                f"  {module_name}: these config.py vars have defaults but aren't "
                f"exposed in Terraform (can't customize via tfvars): {sorted(optional_unexposed)}"
            )

    return errors, warnings


# ── Check 4: Cross-check injected secrets ─────────────────────────────────────

# Env vars that Terraform injects but config.py doesn't read via Settings
KNOWN_NON_CONFIG_VARS = {
    "PORT",  # injected on the web task definition, not read by config.py
}

# Raw secrets used to build connection URLs, not injected as env vars
RAW_SECRETS = {
    "GRAFANA_ADMIN_PASSWORD",
    "DB_PASSWORD",
    "CLICKHOUSE_PASSWORD",
}


def check_injected_vars() -> list[str]:
    """Every injected env var should map to something the app reads."""
    fields = parse_settings_fields()
    field_names = set(fields.keys()) | KNOWN_NON_CONFIG_VARS | parse_dynamic_env_imports()
    errors = []

    for module_name, module_path in MODULES.items():
        provisioned = parse_terraform_provisioned(module_path)
        unknown = provisioned - field_names - RAW_SECRETS
        if unknown:
            errors.append(f"  {module_name}: Terraform injects vars not found in config.py: {sorted(unknown)}")
    return errors


# ── Check 5: .env.example coverage ───────────────────────────────────────────


def parse_env_example() -> set[str]:
    """Extract KEY names from .env.example."""
    env_path = ROOT / ".env.example"
    if not env_path.exists():
        return set()
    keys: set[str] = set()
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z][A-Z_0-9]+)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


def check_env_example_coverage() -> list[str]:
    """Every .env.example var should have a Terraform path or be docker-only."""
    env_keys = parse_env_example()
    if not env_keys:
        return ["  .env.example not found or empty"]

    errors = []

    all_provisioned: set[str] = set()
    for module_path in MODULES.values():
        all_provisioned.update(parse_terraform_provisioned(module_path))

    fields = parse_settings_fields()
    dynamic_env_imports = parse_dynamic_env_imports()

    for key in sorted(env_keys):
        if key in DOCKER_COMPOSE_ONLY:
            continue
        if key in all_provisioned:
            continue
        if fields.get(key, False):
            continue
        if key in KNOWN_NON_CONFIG_VARS:
            continue
        if key in dynamic_env_imports:
            continue
        errors.append(f"  .env.example has {key} but no Terraform module provisions it")

    return errors


# ── Main ──────────────────────────────────────────────────────────────────────

WARN_ICON = "\033[1;33m!\033[0m"
PASS_ICON = "\033[0;32m✓\033[0m"
FAIL_ICON = "\033[0;31m✗\033[0m"


def main() -> None:
    print("Terraform <-> App consistency checks")
    print("=" * 60)

    all_errors: list[str] = []
    all_warnings: list[str] = []

    print("\n[1/4] Common variables across modules...")
    errs = check_common_variables()
    all_errors.extend(errs)
    print(f"  {PASS_ICON} PASS" if not errs else "\n".join(errs))

    print("\n[2/4] App env var coverage...")
    errs, warns = check_env_coverage()
    all_errors.extend(errs)
    all_warnings.extend(warns)
    if errs:
        print("\n".join(errs))
    else:
        print(f"  {PASS_ICON} Required vars: PASS")
    if warns:
        for w in warns:
            print(f"  {WARN_ICON} {w.strip()}")
            print(f"::warning title=Terraform env gap::{w.strip()}")
    else:
        print(f"  {PASS_ICON} Optional vars: all exposed")

    print("\n[3/4] Injected secrets cross-check...")
    errs = check_injected_vars()
    all_errors.extend(errs)
    print(f"  {PASS_ICON} PASS" if not errs else "\n".join(errs))

    print("\n[4/4] .env.example coverage...")
    errs = check_env_example_coverage()
    all_errors.extend(errs)
    print(f"  {PASS_ICON} PASS" if not errs else "\n".join(errs))

    print("\n" + "=" * 60)
    if all_errors:
        print(f"{FAIL_ICON} FAILED: {len(all_errors)} error(s)")
        sys.exit(1)
    elif all_warnings:
        print(f"{WARN_ICON} PASSED with {len(all_warnings)} warning(s) (non-blocking)")
        sys.exit(0)
    else:
        print(f"{PASS_ICON} ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
