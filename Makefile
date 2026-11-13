.PHONY: help install dev start test up down logs sync

DB_HOST_LOCAL := localhost

help: ## Muestra los comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Crea .venv e instala dependencias y pytest con uv
	uv venv
	uv pip install -r requirements.txt pytest

dev: ## Levanta PostgreSQL en Docker y ejecuta FastAPI en modo desarrollo (reload)
	docker compose up -d postgres
	@bash -c 'set -a; [ -f .env ] && source .env; set +a; export DB_HOST=$(DB_HOST_LOCAL); uv run uvicorn app.main:app --reload'

start: dev ## Alias de dev

test: ## Ejecuta las pruebas unitarias con pytest
	uv run pytest -v

up: ## Construye y levanta todo el stack (PostgreSQL + API) en Docker
	docker compose up -d --build

down: ## Detiene los contenedores de Docker Compose
	docker compose down

logs: ## Muestra los logs en vivo del servicio api
	docker compose logs -f api

sync: ## Dispara una sincronización manual contra la API local
	curl -X POST http://localhost:8000/api/sync

