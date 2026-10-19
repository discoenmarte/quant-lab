"""FastAPI application: the API under ``/api`` and the built web app at ``/``."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config
from . import (
    routes_backtest,
    routes_broker,
    routes_core,
    routes_data,
    routes_gauntlet,
    routes_ledger,
    routes_paper,
    routes_portfolio,
    routes_registry,
    routes_report,
)

ROUTERS = [routes_core.router, routes_data.router, routes_backtest.router, routes_gauntlet.router,
           routes_ledger.router, routes_portfolio.router, routes_paper.router, routes_registry.router,
           routes_report.router, routes_broker.router]


def create_app() -> FastAPI:
    """Build the application.

    The web app is served only if it has been built (``web/dist/index.html`` exists). Unknown paths fall back
    to ``index.html`` so client-side routes like ``/gauntlet`` work on reload.

    Returns:
        A configured FastAPI instance.
    """
    app = FastAPI(title="quant-lab", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in ROUTERS:
        app.include_router(router, prefix="/api")

    dist = config.web_dist()
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            """Serve a built file, or the app shell for client-side routes."""
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
