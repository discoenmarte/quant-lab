# quant-lab developer commands. `make help` lists them.
.PHONY: help setup test lint build serve dev-api dev-web demo

help: ## List the commands
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

setup: ## Install Python and web dependencies
	uv sync --group dev
	cd web && npm install

test: ## Run the Python tests
	uv run pytest

lint: ## Lint Python code
	uv run ruff check .

build: ## Type-check and build the web app into web/dist
	cd web && npm run build

serve: build ## Serve the API and the built web app on http://127.0.0.1:8765
	uv run qlab serve

dev-api: ## API with auto-reload on :8765
	uv run qlab serve --reload

dev-web: ## Web app with hot reload on :5173 (needs dev-api running)
	cd web && npm run dev

demo: ## Run the demo pipeline once from the command line
	uv run qlab demo
