"""Routes REST : actions ponctuelles (statut, config, logs, données de graphique).

Le temps réel passe par api_websocket.py, jamais par ici.

NOTE données factices : tant que la gateway broker n'est pas câblée,
/api/charts/price-history et /api/positions servent des données de
démonstration DÉTERMINISTES (seed = symbole + minute) — la série est
stable d'un rafraîchissement à l'autre et la dernière bougie « vit ».
Le branchement réel remplacera uniquement les fonctions _demo_*.
"""

from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger, web_log_buffer
from pyea.storage.storage_trading_state import (
    get_trading_states,
    is_trading_enabled,
    set_trading_enabled,
)
from pyea.strategies.strategy_registry import list_strategies

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["api"])


def _require_known_symbol(symbol: str) -> None:
    if symbol not in get_settings().history_instruments:
        raise HTTPException(status_code=404, detail=f"Symbole inconnu : {symbol}")


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """État global de PyEA, affiché en tête du dashboard."""
    settings = get_settings()
    return {
        "app_version": "0.1.0",
        "trading_mode": settings.trading_mode,
        "broker": settings.broker_name,
        "broker_connected": False,  # Branché sur la gateway réelle plus tard.
        "strategy": settings.strategy_name,
        "strategy_enabled": settings.strategy_enabled,
        "available_strategies": list_strategies(),
        "chart_refresh_seconds": settings.ui_chart_refresh_seconds,
    }


@router.get("/symbols")
async def get_symbols() -> dict[str, Any]:
    """Watchlist du dashboard : chaque symbole + badge « en trading ».

    Watchlist = history.instruments ; « en trading » = interrupteur
    par symbole (bouton Trading/Stopped), persisté en base.
    """
    settings = get_settings()
    states = get_trading_states()
    return {
        "symbols": [
            {"symbol": symbol, "trading": states.get(symbol, False)}
            for symbol in settings.history_instruments
        ],
    }


class TradingToggle(BaseModel):
    enabled: bool


@router.get("/trading/{symbol}")
async def get_trading_state(symbol: str) -> dict[str, Any]:
    """État du trading d'une paire — consulté à chaque changement d'onglet."""
    _require_known_symbol(symbol)
    return {"symbol": symbol, "enabled": is_trading_enabled(symbol)}


@router.put("/trading/{symbol}")
async def put_trading_state(symbol: str, toggle: TradingToggle) -> dict[str, Any]:
    """Arme (Trading) ou arrête (Stopped) le trading d'une paire."""
    _require_known_symbol(symbol)
    enabled = set_trading_enabled(symbol, toggle.enabled)
    logger.info("Trading %s : %s", symbol, "ARMÉ" if enabled else "ARRÊTÉ")
    return {"symbol": symbol, "enabled": enabled}


@router.get("/logs")
async def get_logs(count: int = 100) -> dict[str, list[str]]:
    """Dernières lignes de log pour l'affichage web."""
    return {"lines": web_log_buffer.tail(count)}


# --- Données de démonstration (remplacées au câblage broker réel) ---


def _base_price(symbol: str) -> float:
    """Prix de base plausible et stable par symbole (démo uniquement)."""
    seed = zlib.crc32(symbol.encode())
    if symbol.startswith("XAU"):
        return float(1800 + seed % 400)
    if symbol in ("US500", "US30", "NAS100"):
        return float(3000 + seed % 2000)
    if symbol.endswith("JPY"):
        return float(100 + seed % 60)
    return round(0.8 + (seed % 100) / 100, 4)


# Profondeur de l'historique de démo : 3 jours de M1. Au-delà, l'API
# répond « plus de données » — le front arrête alors de paginer.
_DEMO_HISTORY_MINUTES = 3 * 24 * 60


def _demo_origin_minute() -> int:
    """Première minute de l'historique de démo (origine FIXE de la marche
    aléatoire, alignée sur minuit UTC pour être stable entre requêtes)."""
    now_minute = int(datetime.now(timezone.utc).timestamp() // 60)
    return (now_minute // 1440) * 1440 - _DEMO_HISTORY_MINUTES


def _demo_candles(symbol: str, end_minute: int, points: int) -> list[dict[str, float]]:
    """Bougies M1 déterministes se terminant à ``end_minute`` inclus.

    La marche aléatoire part toujours de l'origine fixe (seed par
    symbole+minute) : deux requêtes sur la même plage donnent les mêmes
    bougies, et la pagination vers le passé reste cohérente.
    """
    origin = _demo_origin_minute()
    start_minute = max(origin, end_minute - points + 1)
    if end_minute < origin:
        return []
    base = _base_price(symbol)
    price = base
    candles: list[dict[str, float]] = []
    for minute in range(origin, end_minute + 1):
        rng = random.Random(f"{symbol}:{minute}")
        open_ = price
        close = open_ + rng.uniform(-1, 1) * base * 0.0008
        if minute >= start_minute:
            high = max(open_, close) + rng.uniform(0, base * 0.0004)
            low = min(open_, close) - rng.uniform(0, base * 0.0004)
            candles.append(
                {
                    "time": minute * 60,  # secondes epoch (format Lightweight Charts)
                    "open": round(open_, 5),
                    "high": round(high, 5),
                    "low": round(low, 5),
                    "close": round(close, 5),
                }
            )
        price = close
    return candles


@router.get("/charts/price-history")
async def get_price_history(
    symbol: str = "EURUSD", points: int = 120, before: int | None = None
) -> dict[str, Any]:
    """Bougies M1 du symbole pour le graphique central (démo déterministe).

    Sans ``before`` : les ``points`` dernières bougies. Avec ``before``
    (epoch secondes) : les ``points`` bougies STRICTEMENT antérieures —
    c'est la pagination utilisée par le défilement vers le passé.
    ``has_more`` indique s'il reste de l'historique plus ancien.
    """
    _require_known_symbol(symbol)
    points = max(10, min(points, 1000))
    now_minute = int(datetime.now(timezone.utc).timestamp() // 60)
    end_minute = now_minute if before is None else min(before // 60 - 1, now_minute)
    candles = _demo_candles(symbol, end_minute, points)
    has_more = bool(candles) and candles[0]["time"] // 60 > _demo_origin_minute()
    return {"symbol": symbol, "candles": candles, "has_more": has_more}


def _demo_positions(settings: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Positions de démonstration : paires armées, sinon têtes de watchlist."""
    armed = [symbol for symbol, on in get_trading_states().items() if on]
    demo_symbols = (armed or settings.history_instruments)[:3]
    now = datetime.now(timezone.utc)
    now_minute = int(now.timestamp() // 60)
    open_positions = []
    for i, symbol in enumerate(demo_symbols):
        rng = random.Random(f"open:{symbol}")
        entry = _base_price(symbol)
        current = _demo_candles(symbol, now_minute, 1)[-1]["close"]
        quantity = rng.choice([0.5, 1.0, 2.0])
        side = rng.choice(["BUY", "SELL"])
        direction = 1 if side == "BUY" else -1
        open_positions.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": round(entry, 5),
                "current_price": current,
                "pnl": round((current - entry) * direction * quantity * 100, 2),
                "opened_at": (now - timedelta(hours=2 + i)).isoformat(),
            }
        )
    closed_positions = []
    for i in range(5):
        symbol = demo_symbols[i % len(demo_symbols)]
        rng = random.Random(f"closed:{symbol}:{i}")
        entry = _base_price(symbol)
        exit_ = entry * (1 + rng.uniform(-0.004, 0.006))
        quantity = rng.choice([0.5, 1.0, 2.0])
        side = rng.choice(["BUY", "SELL"])
        direction = 1 if side == "BUY" else -1
        closed_positions.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": round(entry, 5),
                "close_price": round(exit_, 5),
                "pnl": round((exit_ - entry) * direction * quantity * 100, 2),
                "opened_at": (now - timedelta(days=i + 1, hours=3)).isoformat(),
                "closed_at": (now - timedelta(days=i + 1)).isoformat(),
            }
        )
    return open_positions, closed_positions


@router.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Positions ouvertes + fermées (récentes d'abord) + P&L total (démo)."""
    settings = get_settings()
    open_positions, closed_positions = _demo_positions(settings)
    total_pnl = round(
        sum(p["pnl"] for p in open_positions) + sum(p["pnl"] for p in closed_positions), 2
    )
    return {
        "open": open_positions,
        "closed": closed_positions,  # déjà triées : plus récentes en premier
        "total_pnl": total_pnl,
    }
