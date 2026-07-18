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

from pyea.config.config_settings import get_settings
from pyea.core.core_logging import web_log_buffer
from pyea.strategies.strategy_registry import list_strategies

router = APIRouter(prefix="/api", tags=["api"])


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

    Watchlist = history.instruments ; « en trading » = symbole listé dans
    strategy.symbols ET stratégie activée.
    """
    settings = get_settings()
    traded = set(settings.strategy_symbols) if settings.strategy_enabled else set()
    return {
        "symbols": [
            {"symbol": symbol, "trading": symbol in traded}
            for symbol in settings.history_instruments
        ],
        "strategy_enabled": settings.strategy_enabled,
    }


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
    if symbol.startswith("US") or symbol.startswith("NAS"):
        return float(3000 + seed % 2000)
    if symbol.endswith("JPY"):
        return float(100 + seed % 60)
    return round(0.8 + (seed % 100) / 100, 4)


def _demo_candles(symbol: str, points: int) -> list[dict[str, float]]:
    """Marche aléatoire M1 déterministe : seed par (symbole, minute)."""
    now_minute = int(datetime.now(timezone.utc).timestamp() // 60)
    base = _base_price(symbol)
    price = base
    candles: list[dict[str, float]] = []
    for minute in range(now_minute - points + 1, now_minute + 1):
        rng = random.Random(f"{symbol}:{minute}")
        open_ = price
        close = open_ + rng.uniform(-1, 1) * base * 0.0008
        high = max(open_, close) + rng.uniform(0, base * 0.0004)
        low = min(open_, close) - rng.uniform(0, base * 0.0004)
        candles.append(
            {
                "time": minute * 60 * 1000,  # ms epoch pour l'axe temps Chart.js
                "open": round(open_, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close, 5),
            }
        )
        price = close
    return candles


@router.get("/charts/price-history")
async def get_price_history(symbol: str = "EURUSD", points: int = 120) -> dict[str, Any]:
    """Bougies M1 du symbole pour le graphique central (démo déterministe)."""
    settings = get_settings()
    if symbol not in settings.history_instruments:
        raise HTTPException(status_code=404, detail=f"Symbole inconnu : {symbol}")
    points = max(10, min(points, 500))
    return {"symbol": symbol, "candles": _demo_candles(symbol, points)}


def _demo_positions(settings: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Positions de démonstration bâties sur les symboles tradés."""
    now = datetime.now(timezone.utc)
    open_positions = []
    for i, symbol in enumerate(settings.strategy_symbols[:3]):
        rng = random.Random(f"open:{symbol}")
        entry = _base_price(symbol)
        current = _demo_candles(symbol, 1)[-1]["close"]
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
        symbol = settings.strategy_symbols[i % len(settings.strategy_symbols)]
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
