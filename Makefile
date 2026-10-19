# quant-lab developer commands. `make help` lists them.
.PHONY: help setup test lint build serve dev-api dev-web demo paper-nightly paper-launchd report alert-test

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

paper-nightly: ## Ingest, record the paper book's decisions for the latest close, score it and build the daily report (once, now)
	uv run qlab paper nightly

paper-launchd: ## Write the nightly paper job as a launchd plist and print the install steps (installs nothing)
	uv run qlab paper launchd $(if $(HOUR),--hour $(HOUR) --minute $(or $(MINUTE),0),)

report: ## Build the daily lab report for the time since the last one, print it and raise its alerts
	uv run qlab report

alert-test: ## Send a test alert through every channel (file, macOS, Telegram when configured)
	uv run qlab alerts test
