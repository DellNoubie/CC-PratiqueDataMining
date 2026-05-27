.PHONY: help dev down logs test lint migrate seed admin build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Docker ─────────────────────────────────────────────────

dev: ## Start all services in development mode
	docker compose up -d
	@echo ""
	@echo "Services running:"
	@echo "  API:       http://localhost:8000"
	@echo "  Docs:      http://localhost:8000/docs"
	@echo "  Dashboard: http://localhost:3000"
	@echo "  MLflow:    http://localhost:5000"

down: ## Stop all services
	docker compose down

down-clean: ## Stop all services and remove volumes
	docker compose down -v

logs: ## Tail logs for all services
	docker compose logs -f

logs-api: ## Tail API logs
	docker compose logs -f api

logs-worker: ## Tail Celery worker logs
	docker compose logs -f celery-worker

build: ## Build all Docker images
	docker compose build

# ── Database ───────────────────────────────────────────────

migrate: ## Run database migrations
	docker compose exec api alembic upgrade head

migrate-down: ## Rollback last migration
	docker compose exec api alembic downgrade -1

seed: ## Seed database with sample data
	docker compose exec api python -m app.scripts.seed_data

admin: ## Create admin user (interactive)
	docker compose exec api python -m app.scripts.create_admin

# ── Testing ────────────────────────────────────────────────

test: ## Run backend tests
	cd backend && python -m pytest tests/ -v

test-cov: ## Run tests with coverage report
	cd backend && python -m pytest tests/ --cov=app --cov-report=html --cov-report=term

# ── Linting ────────────────────────────────────────────────

lint: ## Lint backend code
	cd backend && ruff check .

lint-fix: ## Fix linting issues
	cd backend && ruff check --fix . && ruff format .

lint-dashboard: ## Lint dashboard code
	cd dashboard && npx biome check .

# ── Dashboard ──────────────────────────────────────────────

dashboard-dev: ## Start dashboard in development mode
	cd dashboard && npm run dev

dashboard-build: ## Build dashboard for production
	cd dashboard && npm run build

dashboard-install: ## Install dashboard dependencies
	cd dashboard && npm install

# ── Quick Start ────────────────────────────────────────────

setup: dev ## Full setup: start services, migrate, seed
	@echo "Waiting for database..."
	@sleep 5
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "Setup complete! Login with admin / admin123"
	@echo "Dashboard: http://localhost:3000"

# ── PaySim Pipeline ────────────────────────────────────────

paysim-import: ## Import PaySim dataset into DB (--limit 250 by default)
	docker compose exec api python -m app.scripts.import_paysim --file "/app/paysim dataset.csv" --limit 250

paysim-label: ## Create Alert+Review labels from imported PaySim transactions
	docker compose exec api python -m app.scripts.label_paysim --limit 250

paysim-train: ## Retrain XGBoost fraud classifier on labeled data
	docker compose run --rm -e PYTHONPATH=/app api python -c \
		"from app.tasks.training import retrain_fraud_classifier; retrain_fraud_classifier()"

paysim-pipeline: paysim-import paysim-label paysim-train ## Full pipeline: import → label → train
