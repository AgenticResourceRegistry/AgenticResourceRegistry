# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Hemalatha Madeswaran <hemalathamadeswaran@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Swathi Saravanan <ss4522@cornell.edu>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0

.PHONY: lint format check test test-adversarial test-eval-completeness test-fuzz test-all hooks clean migrate migrate-clickhouse check-migrations new-migration reset rebuild rebuild-fast rebuild-prometheus rebuild-observability rebuild-local reset-prometheus reset-observability up-prometheus up-observability down-prometheus down-observability logs-prometheus logs-observability release release-preview sync-skill ensure-host-dirs

# ── Linting ──────────────────────────────────────────────

lint:  ## Run all linters
	uv run --with ruff==0.15.10 ruff check .

format:  ## Auto-format all code
	uv run --with ruff==0.15.10 ruff format .
	uv run --with ruff==0.15.10 ruff check --fix .

check:  ## Full pre-commit check on all files
	SKIP=no-commit-to-branch uvx --from pre-commit pre-commit run --all-files

# ── Testing ──────────────────────────────────────────────

test:  ## Run Python tests (parallel across cores)
	cd observal-server && uv run --with pytest --with pytest-asyncio --with pytest-xdist --with pyyaml --with typer --with rich --with hypothesis --with pyarrow pytest ../tests/ -q -n auto

test-v:  ## Run Python tests (verbose, parallel across cores)
	cd observal-server && uv run --with pytest --with pytest-asyncio --with pytest-xdist --with pyyaml --with typer --with rich --with hypothesis --with pyarrow pytest ../tests/ -v -n auto

test-adversarial:  ## Run BenchJack self-test suite
	cd observal-server && uv run --with pytest --with pytest-asyncio --with pyyaml --with typer --with rich pytest ../tests/test_adversarial_self.py -v --tb=short

test-eval-completeness:  ## Run eval completeness tests
	cd observal-server && uv run --with pytest --with pytest-asyncio --with pyyaml --with typer --with rich pytest ../tests/test_eval_completeness.py -v --tb=short

test-fuzz:  ## Smoke-test the OSS-Fuzz targets over their seed corpora (see fuzz/README.md)
	cd observal-server && uv run --with pytest --with pytest-asyncio --with atheris --with hypothesis --with loguru pytest ../tests/test_fuzz_targets.py -q

test-all: test test-eval-completeness test-adversarial  ## Run all tests including adversarial and completeness

sync-skill:  ## Regenerate the auto-generated command reference in the bundled Observal skill
	cd observal-server && uv run --with typer --with rich --with loguru --with pyyaml python ../scripts/sync_observal_skill.py

# ── Setup ────────────────────────────────────────────────

hooks:  ## Install pre-commit hooks
	uvx --from pre-commit pre-commit install
	uvx --from pre-commit pre-commit install --hook-type commit-msg
	uvx --from pre-commit pre-commit install --hook-type pre-push
	@echo "✓ Hooks installed"

# ── Docker ───────────────────────────────────────────────

COMPOSE_FILES := -f docker-compose.yml
OBSERVABILITY_COMPOSE_FILES := $(COMPOSE_FILES) -f docker-compose.observability.yml

# Pre-create bind-mount sources so the Docker daemon doesn't create them root-owned
# (breaks CLI writes to ~/.observal). On Linux, an ACL lets the container's appuser
# (uid 1001, see docker/Dockerfile.api) write dev.log for `observal logs`; macOS
# has no setfacl but Docker Desktop maps container uids to the host user anyway.
ensure-host-dirs:
	@mkdir -p "$(HOME)/.observal/logs"
	@command -v setfacl >/dev/null 2>&1 && setfacl -m u:1001:rwx "$(HOME)/.observal/logs" 2>/dev/null || true

up: ensure-host-dirs  ## Start Docker stack
	cd docker && docker compose $(COMPOSE_FILES) up -d

up-prometheus: ensure-host-dirs  ## Start Docker stack with Prometheus
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) up -d

up-observability: ensure-host-dirs  ## Start Docker stack with Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) up -d

down:  ## Stop Docker stack, including optional Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down

down-prometheus:  ## Stop Docker stack with Prometheus
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down

down-observability:  ## Stop Docker stack with Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down

migrate:  ## Run Postgres migrations
	cd docker && docker compose $(COMPOSE_FILES) exec observal-api /app/.venv/bin/python -m alembic upgrade head

migrate-clickhouse:  ## Run ClickHouse migrations manually
	cd docker && docker compose $(COMPOSE_FILES) run --rm --no-deps observal-api /app/.venv/bin/python -m services.clickhouse.migrations

check-migrations:  ## Validate alembic migration chain (no duplicates, no forks)
	python3 scripts/check_migrations.py

new-migration:  ## Autogenerate a new migration (requires running stack): make new-migration MSG="add foo to bar"
	@test -n "$(MSG)" || (echo 'Usage: make new-migration MSG="description"' && exit 1)
	cd observal-server && DATABASE_URL="$${DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/observal}" uv run alembic revision --autogenerate -m "$(MSG)"
	@echo ""
	@echo "Migration generated in observal-server/alembic/versions/."
	@echo "Inspect the file, then apply with: make migrate"

rebuild rebuild-fast: ensure-host-dirs  ## Rebuild and restart Docker stack (builds API and web images once; rebuild-fast is the same target)
	cd docker && docker compose $(COMPOSE_FILES) build observal-api observal-web
	cd docker && docker compose $(COMPOSE_FILES) up -d --no-build
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose $(COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose $(COMPOSE_FILES) restart observal-lb
	@echo "API is healthy."

rebuild-prometheus: ensure-host-dirs  ## Rebuild and restart Docker stack with Prometheus
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose $(OBSERVABILITY_COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) restart observal-lb
	@echo "API is healthy."

rebuild-observability: ensure-host-dirs  ## Rebuild and restart Docker stack with Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) restart observal-lb
	@echo "API is healthy."

rebuild-local: ensure-host-dirs  ## Rebuild local Docker stack
	cd docker && docker compose -f docker-compose.yml up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose -f docker-compose.yml exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose -f docker-compose.yml restart observal-lb
	@echo "✓ Running local Docker stack"

reset: ensure-host-dirs  ## Nuke all Docker volumes, including optional observability, and rebuild core
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down -v
	cd docker && docker compose $(COMPOSE_FILES) up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose $(COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose $(COMPOSE_FILES) restart observal-lb
	@echo "API is healthy — all data has been reset."

reset-prometheus: ensure-host-dirs  ## Nuke all Docker volumes and rebuild with Prometheus
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) down -v
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose $(OBSERVABILITY_COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) restart observal-lb
	@echo "API is healthy: all data has been reset."

reset-observability: ensure-host-dirs  ## Nuke all Docker volumes and rebuild with Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down -v
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) up --build -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) restart observal-lb
	@echo "API is healthy: all data has been reset."

rebuild-clean: ensure-host-dirs  ## Rebuild from scratch (no Docker cache), remove volumes, and restart
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) down -v && docker compose $(COMPOSE_FILES) build --no-cache && docker compose $(COMPOSE_FILES) up -d
	@echo "Waiting for API to be healthy..."
	@cd docker && until docker compose $(COMPOSE_FILES) exec observal-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; do sleep 1; done
	cd docker && docker compose $(COMPOSE_FILES) restart observal-lb
	@echo "API is healthy."

logs:  ## Tail Docker logs
	cd docker && docker compose logs -f --tail=50

logs-prometheus:  ## Tail Docker logs with Prometheus
	cd docker && docker compose $(OBSERVABILITY_COMPOSE_FILES) logs -f --tail=50

logs-observability:  ## Tail Docker logs with Prometheus and Grafana
	cd docker && COMPOSE_PROFILES=grafana docker compose $(OBSERVABILITY_COMPOSE_FILES) logs -f --tail=50

# ── Cleanup ──────────────────────────────────────────────

clean:  ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ htmlcov/ .coverage

# ── Release ─────────────────────────────────────────────────

release:  ## Prepare a release-line PR (pass ARGS for cut, channel, backport, status)
	uv run python tools/release.py $(ARGS)

release-preview:  ## Preview a release-line PR without writing changes
	uv run python tools/release.py --preview $(ARGS)

help:  ## Show this help
	@grep -E '^[a-zA-Z_ -]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
