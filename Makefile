.PHONY: help install install-dev dev start test test-int lint typecheck up down logs sync validate

DB_HOST_LOCAL := localhost

help: ## Muestra los comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Crea .venv e instala dependencias de producción y pytest con uv
	uv venv
	uv pip install -r requirements.txt pytest

install-dev: ## Instala herramientas de desarrollo (pytest, ruff, mypy, httpx)
	uv venv
	uv pip install -r requirements-dev.txt

dev: ## Levanta PostgreSQL en Docker y ejecuta FastAPI en modo desarrollo (reload)
	docker compose up -d postgres
	@bash -c 'set -a; [ -f .env ] && source .env; set +a; export DB_HOST=$(DB_HOST_LOCAL); uv run uvicorn app.main:app --reload'

start: dev ## Alias de dev

test: ## Ejecuta las pruebas unitarias y HTTP con pytest
	uv run pytest -v -m "not integration"

test-int: ## Ejecuta pruebas de integración contra PostgreSQL (requiere DB_HOST/DB_USER/etc.)
	uv run pytest -v -m integration

lint: ## Ejecuta ruff sobre app y tests
	uv run ruff check app tests

typecheck: ## Ejecuta mypy sobre app
	uv run mypy app

up: ## Construye y levanta todo el stack (PostgreSQL + API) en Docker
	docker compose up -d --build

down: ## Detiene los contenedores de Docker Compose
	docker compose down

logs: ## Muestra los logs en vivo del servicio api
	docker compose logs -f api

sync: ## Dispara una sincronización manual contra la API local
	curl -X POST http://localhost:8000/api/v1/sync

validate: ## Valida un RNC/Cédula vía CLI (uso: make validate RNC=130663041)
	uv run python -m app.cli validate "$(RNC)"
