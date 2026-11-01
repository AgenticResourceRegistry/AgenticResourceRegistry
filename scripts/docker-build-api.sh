#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Build the API image from a git snapshot streamed to `docker build -`.
#
# The builder receives only the paths Dockerfile.api copies, so it never walks
# the working tree. This matters when the checkout sits on a slow filesystem
# (a WSL /mnt/c mount, a synced folder) where the context scan dominates.
#
# Tracked changes are included via `git stash create`; untracked files are not.
#
# Usage:  scripts/docker-build-api.sh [tag]
#         DOCKER="wsl docker" scripts/docker-build-api.sh   # Git Bash -> WSL engine
set -euo pipefail

tag="${1:-observal-api:dev}"
docker_cmd="${DOCKER:-docker}"

cd "$(git rev-parse --show-toplevel)"
rev="$(git stash create)"
rev="${rev:-HEAD}"

git archive --format=tar "$rev" \
  observal-server packages/observal-shared docker/entrypoint.sh docker/Dockerfile.api |
  $docker_cmd build -f docker/Dockerfile.api -t "$tag" -
