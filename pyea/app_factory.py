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

from pyea.api import api_backtest, api_pages, api_rest, api_training, api_websocket
from pyea.brokers import get_gateway
from pyea.brokers.broker_runtime import broker_runtime
from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger, setup_logging
from pyea.live.live_runtime import live_runtime
from pyea.storage.storage_database import init_db
from pyea.storage.storage_training_runs import fail_orphan_runs

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger = get_logger(__name__)
    init_db()
    orphans = fail_orphan_runs()
    if orphans:
        logger.warning(
            "%d entraînement(s) interrompu(s) par un arrêt du serveur marqué(s) « failed ».",
            orphans,
        )
    api_websocket.wire_event_bus()
    # Gateway broker instanciée (pas connectée : la connexion réelle est une
    # action explicite de l'utilisateur). L'API lit son état via broker_runtime
    # — plus jamais de broker_connected codé en dur.
    broker_runtime.set_gateway(get_gateway(settings.broker_name)(settings))
    # Flux live (feed + moteur) assemblé ici, DÉMARRÉ à la connexion broker
    # (endpoint /api/broker/connect) — le flux n'a de sens que broker connecté.
    live_runtime.configure(settings)
    logger.info(
        "PyEA démarré — broker=%s mode=%s stratégie=%s",
        settings.broker_name,
        settings.trading_mode,
        settings.strategy_name,
    )
    yield
    await live_runtime.stop()
    await broker_runtime.disconnect()
    logger.info("PyEA arrêté.")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file, settings.log_web_buffer_size)

    app = FastAPI(title="PyEA", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api_pages.router)
    app.include_router(api_rest.router)
    app.include_router(api_backtest.router)
    app.include_router(api_training.router)
    app.include_router(api_websocket.router)
    return app
