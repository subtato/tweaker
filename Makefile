.PHONY: install dev test lint format web build docker up seed

install:
	uv sync --extra dev

dev:
	uv run tweaker serve --reload

test:
	uv run pytest

lint:
	uv run ruff check src tests
	cd web && npm run typecheck

format:
	uv run ruff format src tests

web:
	cd web && npm install && npm run dev

build:
	cd web && npm install && npm run build

seed:
	uv run tweaker seed-demo --force

index:
	uv run tweaker index-repos

docker:
	docker compose build

up:
	docker compose up
