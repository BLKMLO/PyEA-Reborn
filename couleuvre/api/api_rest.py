"""Routes REST : actions ponctuelles (statut, config, logs, données de graphique).

Le temps réel passe par api_websocket.py, jamais par ici.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from couleuvre.config.config_settings import get_settings
from couleuvre.core.core_logging import web_log_buffer
from couleuvre.strategies.strategy_registry import list_strategies

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """État global de l'EA, affiché en tête du dashboard."""
    settings = get_settings()
    return {
        "app_version": "0.1.0",
        "trading_mode": settings.trading_mode,
        "broker": settings.broker_name,
        "broker_connected": False,  # Branché sur la gateway réelle plus tard.
        "strategy": settings.strategy_name,
        "strategy_enabled": settings.strategy_enabled,
        "available_strategies": list_strategies(),
    }


@router.get("/logs")
async def get_logs(count: int = 100) -> dict[str, list[str]]:
    """Dernières lignes de log pour l'affichage web."""
    return {"lines": web_log_buffer.tail(count)}


@router.get("/charts/price-history")
async def get_price_history(symbol: str = "DEMO", points: int = 60) -> dict[str, Any]:
    """Données de graphique au format attendu par static/js/charts.js.

    Données FACTICES (marche aléatoire) tant que le flux broker n'est pas
    branché : elles permettent de valider la chaîne API → Chart.js.
    """
    now = datetime.now(timezone.utc)
    labels: list[str] = []
    prices: list[float] = []
    price = 100.0
    for i in range(points):
        timestamp = now - timedelta(minutes=points - i)
        price += random.uniform(-0.5, 0.5) + 0.1 * math.sin(i / 8)
        labels.append(timestamp.strftime("%H:%M"))
        prices.append(round(price, 2))
    return {"symbol": symbol, "labels": labels, "prices": prices}
