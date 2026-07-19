"""Assemblage de l'application FastAPI.

``create_app()`` est le SEUL endroit où les modules sont câblés entre
eux (config, logging, base, routes, bus d'événements). Les modules
eux-mêmes ne s'importent pas en étoile — c'est ce qui garde le découpage
propre quand le projet grossira.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pyea.api import api_backtest, api_pages, api_rest, api_websocket
from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger, setup_logging
from pyea.storage.storage_database import init_db

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger = get_logger(__name__)
    init_db()
    api_websocket.wire_event_bus()
    logger.info(
        "PyEA démarré — broker=%s mode=%s stratégie=%s",
        settings.broker_name,
        settings.trading_mode,
        settings.strategy_name,
    )
    # Plus tard : instancier la gateway broker, la stratégie active et le
    # MarketDataFeed ici, et les arrêter proprement après le yield.
    yield
    logger.info("PyEA arrêté.")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file, settings.log_web_buffer_size)

    app = FastAPI(title="PyEA", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api_pages.router)
    app.include_router(api_rest.router)
    app.include_router(api_backtest.router)
    app.include_router(api_websocket.router)
    return app
