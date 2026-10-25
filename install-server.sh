#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# Observal Server Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install-server.sh | bash -s -- [OPTIONS]
#
# Options:
#   --version VERSION          Version to install (default: latest)
#   --install-dir DIR          Install directory (default: ~/.observal on macOS, /opt/observal on Linux)
#   --force                    Skip overwrite confirmation on re-install
#
# Environment variable overrides (lower priority than flags):
#   OBSERVAL_VERSION=latest    Version to install
#   OBSERVAL_INSTALL_DIR       Install directory
#   OBSERVAL_FORCE=1           Skip overwrite confirmation

GITHUB_REPO="Observal/Observal"


# ── Helpers ──────────────────────────────────────────────────

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*"; }
error() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die() {
    error "$@"
    exit 1
}

# ── Parse arguments ──────────────────────────────────────────

VERSION="${OBSERVAL_VERSION:-latest}"
FORCE="${OBSERVAL_FORCE:-0}"
BASE_URL="${OBSERVAL_BASE_URL:-}"

# Default install directory
if [ -z "${OBSERVAL_INSTALL_DIR:-}" ]; then
    case "$(uname -s)" in
    Darwin) INSTALL_DIR="$HOME/.observal" ;;
    *) INSTALL_DIR="/opt/observal" ;;
    esac
else
    INSTALL_DIR="$OBSERVAL_INSTALL_DIR"
fi

while [ $# -gt 0 ]; do
    case "$1" in
    --license-key)
        [ -n "${2:-}" ] || die "--license-key requires a value"
        shift 2
        ;;
    --license-key=*)
        shift
        ;;
    --version)
        [ -n "${2:-}" ] || die "--version requires a value"
        VERSION="$2"
        shift 2
        ;;
    --version=*)
        VERSION="${1#--version=}"
        shift
        ;;
    --install-dir)
        [ -n "${2:-}" ] || die "--install-dir requires a value"
        INSTALL_DIR="$2"
        shift 2
        ;;
    --install-dir=*)
        INSTALL_DIR="${1#--install-dir=}"
        shift
        ;;
    --force)
        FORCE=1
        shift
        ;;
    -h | --help)
        cat <<'HELP'
Observal Server Installer
Usage: curl -fsSL https://raw.githubusercontent.com/Observal/Observal/main/install-server.sh | bash -s -- [OPTIONS]

Options:
  --version VERSION    Version to install (default: latest)
  --install-dir DIR    Install directory (default: ~/.observal on macOS, /opt/observal on Linux)
  --force              Skip overwrite confirmation on re-install

Environment variable overrides (lower priority than flags):
  OBSERVAL_VERSION       Version to install
  OBSERVAL_INSTALL_DIR   Install directory
  OBSERVAL_FORCE=1       Skip overwrite confirmation
HELP
        exit 0
        ;;
    *)
        die "Unknown option: $1"
        ;;
    esac
done

# ── Pre-flight ───────────────────────────────────────────────

command -v curl >/dev/null 2>&1 || die "'curl' is required but not found."
command -v docker >/dev/null 2>&1 || die "Docker is required. Install: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."

# ── Resolve version ──────────────────────────────────────────

if [ "$VERSION" = "latest" ]; then
    VERSION=$(curl -fsSL "https://api.github.com/repos/$GITHUB_REPO/releases/latest" |
        grep '"tag_name"' | head -1 | cut -d'"' -f4)
    [ -n "$VERSION" ] || die "Could not determine latest version"
fi

info "Installing Observal Server $VERSION"

# ── Download ─────────────────────────────────────────────────

ARTIFACT="observal-server-${VERSION}.tar.gz"

if [ -n "$BASE_URL" ]; then
    URL="${BASE_URL}/${ARTIFACT}"
else
    URL="https://github.com/$GITHUB_REPO/releases/download/$VERSION/$ARTIFACT"
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

info "Downloading server package..."
if ! curl -fsSL -o "$TMPDIR/$ARTIFACT" "$URL"; then
    die "Download failed. Check that $VERSION exists at https://github.com/$GITHUB_REPO/releases"
fi

# ── Stage and preserve previous package ────────────────────

INSTALL_DIR="${INSTALL_DIR%/}"
[ -n "$INSTALL_DIR" ] && [ "$INSTALL_DIR" != "." ] || die "Choose a dedicated install directory"
PARENT=$(dirname "$INSTALL_DIR")
BACKUP_ROOT="${INSTALL_DIR}.backups"
MARKER="${INSTALL_DIR}.upgrade-in-progress"
[ ! -L "$INSTALL_DIR" ] && [ ! -L "$BACKUP_ROOT" ] || die "Install or backup path is a symlink"
[ ! -e "$MARKER" ] || die "Interrupted upgrade marker at $MARKER; inspect the backup and restore manually before retrying"

USE_SUDO=0
if [ ! -w "$PARENT" ]; then
    command -v sudo >/dev/null 2>&1 || die "Cannot write to $PARENT; run with suitable permissions"
    USE_SUDO=1
fi
fs() {
    if [ "$USE_SUDO" = 1 ]; then sudo "$@"; else "$@"; fi
}
fs mkdir -p "$PARENT"

if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    if [ "$FORCE" = "1" ]; then
        info "Preparing a backup of $INSTALL_DIR before replacement"
    else
        warn "Directory $INSTALL_DIR already exists and is not empty."
        printf 'Back up and replace? [y/N]: '
        read -r confirm </dev/tty
        [ "$confirm" = "y" ] || [ "$confirm" = "Y" ] || die "Aborted."
    fi
fi
[ ! -e "$INSTALL_DIR" ] || [ -d "$INSTALL_DIR" ] || die "Install path is not a directory"

STAGE=$(fs mktemp -d "${INSTALL_DIR}.stage.XXXXXX")
if [ "$USE_SUDO" = 1 ]; then fs chown "$(id -u):$(id -g)" "$STAGE"; fi
info "Unpacking to staging directory..."
if ! tar -xzf "$TMPDIR/$ARTIFACT" -C "$STAGE" --strip-components=1; then
    fs rm -rf "$STAGE"
    die "Invalid package archive; existing installation was not touched"
fi
[ -f "$STAGE/setup.sh" ] && [ ! -L "$STAGE/setup.sh" ] || die "Package has no regular setup.sh"
if [ -d "$INSTALL_DIR" ]; then
    for protected in .env secrets; do
        [ ! -L "$INSTALL_DIR/$protected" ] || die "Existing $protected is a symlink; inspect it before upgrading"
        if [ -e "$INSTALL_DIR/$protected" ]; then
            fs rm -rf "$STAGE/$protected"
            cp -a "$INSTALL_DIR/$protected" "$STAGE/$protected"
        fi
    done
fi

# Keep the old directory intact as a sibling backup; moving directories on the
# same filesystem avoids extracting over live files. The marker makes an
# interrupted upgrade visible to the next invocation. Only backup the package
# here: a database snapshot/downgrade is an independent operator action.
BACKUP_DIR=""
if [ -d "$INSTALL_DIR" ]; then
    fs mkdir -p "$BACKUP_ROOT"
    BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    [ ! -e "$BACKUP_DIR" ] || die "Backup already exists at $BACKUP_DIR"
fi
# mkdir is an exclusive cooperating-installer claim; two simultaneous upgrades
# cannot both pass a non-atomic existence check and swap the same destination.
if ! fs mkdir "$MARKER"; then
    fs rm -rf "$STAGE"
    die "Another upgrade owns $MARKER; inspect it before retrying"
fi
if [ -n "$BACKUP_DIR" ]; then fs mv "$INSTALL_DIR" "$BACKUP_DIR"; fi
fs mv "$STAGE" "$INSTALL_DIR"

# ── Run setup and restore on ordinary failure ───────────────

setup_ok=0
if ( : </dev/tty ) 2>/dev/null; then
    info "Running guided setup..."
    OBSERVAL_INSTALL_DIR="$INSTALL_DIR" bash "$INSTALL_DIR/setup.sh" </dev/tty && setup_ok=1
else
    info "No terminal detected; using safe setup defaults..."
    OBSERVAL_INSTALL_DIR="$INSTALL_DIR" bash "$INSTALL_DIR/setup.sh" </dev/null && setup_ok=1
fi
if [ "$setup_ok" != 1 ]; then
    warn "Setup failed; retaining failed package for inspection. Database changes are NOT rolled back."
    fs mv "$INSTALL_DIR" "${INSTALL_DIR}.failed-$$"
    if [ -n "$BACKUP_DIR" ]; then
        fs cp -a "$BACKUP_DIR" "$INSTALL_DIR"
        fs rmdir "$MARKER"
        die "Previous package restored from $BACKUP_DIR; verify database compatibility before restarting"
    fi
    die "First install failed; inspect $MARKER and restore manually"
fi
fs rmdir "$MARKER"
if [ -n "$BACKUP_DIR" ]; then info "Previous package and configuration retained at $BACKUP_DIR"; fi
