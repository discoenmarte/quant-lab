# quant-lab developer commands. `make help` lists them.
.PHONY: help setup test lint build serve dev-api dev-web demo paper-nightly paper-launchd report alert-test \
	deploy deploy-first tunnel pull server-status

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

deploy: ## Update the server: code, packages and services (HOST=<server address>)
	ops/server/deploy.sh $(HOST)

deploy-first: ## First move to the server: also copy the data folder and broker keys (HOST=<server address>)
	ops/server/deploy.sh $(HOST) --data

tunnel: ## Open the server's lab at http://127.0.0.1:8770 through SSH (HOST=<server address>; Ctrl-C closes it)
	@echo "open http://127.0.0.1:8770 (Ctrl-C to close)"
	ssh -N -L 8770:127.0.0.1:8765 qlab@$(HOST)

pull: ## Copy the server's snapshots and reports to this Mac, into ~/quant-lab-server (HOST=<server address>)
	mkdir -p ~/quant-lab-server
	rsync -az --stats qlab@$(HOST):/srv/quant-lab/data/backups qlab@$(HOST):/srv/quant-lab/data/reports \
		~/quant-lab-server/

server-status: ## The server's lab services, timers and the last nightly log (HOST=<server address>)
	ssh root@$(HOST) "systemctl --no-pager status qlab-api qlab-bot | head -20; systemctl list-timers 'qlab-*' \
		--no-pager; journalctl -u qlab-nightly -n 30 --no-pager"
