"""Routes REST : actions ponctuelles (statut, config, logs, données de graphique).

Le temps réel passe par api_websocket.py, jamais par ici.

HONNÊTETÉ DE L'INTERFACE (règle utilisateur) : PyEA ne fabrique JAMAIS de
données de COMPTE (positions, trades, P&L, état de connexion). Elles
viennent du broker (gateway) ou du journal SQL des trades ; tant que le
broker est déconnecté, l'interface montre « déconnecté », zéro position,
zéro trade. Seules les données de MARCHÉ (graphique, prix watchlist)
restent une DÉMO déterministe tant que le flux réel n'est pas branché —
et le dashboard l'affiche explicitement comme « DÉMO » (`market_data_live`
dans /api/status). Le câblage réel remplacera les fonctions _demo_*.
"""

from __future__ import annotations

import random
import zlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pyea.brokers.broker_credentials import broker_credentials
from pyea.brokers.broker_runtime import broker_runtime
from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger, web_log_buffer
from pyea.storage.storage_trades import list_recent_trades
from pyea.storage.storage_trading_state import (
    get_trading_states,
    is_trading_enabled,
    set_trading_enabled,
)
from pyea.strategies.strategy_registry import list_strategies

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["api"])

# Les données de marché sont-elles réelles ? False tant que le flux broker
# n'est pas câblé (le graphique/watchlist affichent alors une démo étiquetée).
MARKET_DATA_LIVE = False


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
        "broker_connected": broker_runtime.is_connected(),  # état RÉEL de la gateway
        "broker_credentials_set": broker_credentials.is_configured(),
        "market_data_live": MARKET_DATA_LIVE,  # False → l'UI marque « DÉMO »
        "strategy": settings.strategy_name,
        "strategy_enabled": settings.strategy_enabled,
        "available_strategies": list_strategies(),
        "chart_refresh_seconds": settings.ui_chart_refresh_seconds,
    }


@router.post("/broker/connect")
async def connect_broker() -> dict[str, Any]:
    """Tente la connexion au broker. Retour HONNÊTE : tant que la gateway IB
    réelle n'est pas implémentée, renvoie 501 (pas de fausse connexion)."""
    try:
        await broker_runtime.connect()
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Connexion au broker indisponible : la passerelle IB est "
            "encore en développement (aucune fausse connexion n'est simulée).",
        )
    except Exception as exc:  # échec réel (TWS éteint, identifiants…)
        logger.warning("Connexion broker échouée : %s", exc)
        raise HTTPException(status_code=502, detail=f"Connexion au broker échouée : {exc}")
    logger.info("Connexion au broker établie.")
    return {"broker_connected": broker_runtime.is_connected()}


@router.post("/broker/disconnect")
async def disconnect_broker() -> dict[str, Any]:
    """Coupe la connexion au broker (toujours autorisé)."""
    await broker_runtime.disconnect()
    logger.info("Déconnexion du broker.")
    return {"broker_connected": broker_runtime.is_connected()}


# --- Identifiants broker (saisis au runtime, gardés en mémoire) ---


class BrokerCredentialsIn(BaseModel):
    """Corps du PUT : le mot de passe est optionnel pour permettre de ne
    changer que l'identifiant sans re-saisir le mot de passe masqué."""

    username: str
    password: str | None = None


def _broker_credentials_view() -> dict[str, Any]:
    """Vue publique des identifiants : JAMAIS le mot de passe en clair.

    On renvoie l'identifiant (utile pour reconnaître le compte) et un
    booléen ``configured`` ; le front masque le mot de passe par des
    étoiles lorsque ``configured`` est vrai.
    """
    return {
        "broker": get_settings().broker_name,
        "configured": broker_credentials.is_configured(),
        "username": broker_credentials.username,
    }


@router.get("/broker/credentials")
async def get_broker_credentials() -> dict[str, Any]:
    """Indique si des identifiants broker sont en mémoire (sans les révéler)."""
    return _broker_credentials_view()


@router.put("/broker/credentials")
async def put_broker_credentials(payload: BrokerCredentialsIn) -> dict[str, Any]:
    """Enregistre les identifiants broker EN MÉMOIRE (jusqu'à l'arrêt serveur).

    Mot de passe vide + identifiants déjà présents = on garde le mot de
    passe existant (l'utilisateur n'a pas re-saisi les étoiles). Mot de
    passe vide sans identifiants préalables = erreur 422.
    """
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Nom d'utilisateur requis.")
    password = payload.password or ""
    if not password:
        if not broker_credentials.is_configured():
            raise HTTPException(status_code=422, detail="Mot de passe requis.")
        broker_credentials.update_username(username)
    else:
        broker_credentials.set(username, password)
    # Journalisation SANS le mot de passe (jamais de secret dans les logs).
    logger.info("Identifiants broker enregistrés (utilisateur : %s).", username)
    return _broker_credentials_view()


@router.delete("/broker/credentials")
async def delete_broker_credentials() -> dict[str, Any]:
    """Efface les identifiants broker de la mémoire."""
    broker_credentials.clear()
    logger.info("Identifiants broker effacés.")
    return _broker_credentials_view()


@router.get("/symbols")
async def get_symbols() -> dict[str, Any]:
    """Watchlist du dashboard (façon « Market Watch ») : chaque symbole +
    dernier prix + variation sur 24 h + badge « en trading ».

    Watchlist = history.instruments ; « en trading » = interrupteur
    par symbole (bouton Trading/Stopped), persisté en base. Prix et
    variation sont des données de DÉMO déterministes (_demo_quote),
    cohérentes avec les bougies du graphique (même marche aléatoire).
    """
    settings = get_settings()
    states = get_trading_states()
    symbols = []
    for symbol in settings.history_instruments:
        last, change_pct = _demo_quote(symbol)
        symbols.append(
            {
                "symbol": symbol,
                "trading": states.get(symbol, False),
                "last": last,
                "change_pct": change_pct,
            }
        )
    return {"symbols": symbols}


class TradingToggle(BaseModel):
    enabled: bool


@router.get("/trading/{symbol}")
async def get_trading_state(symbol: str) -> dict[str, Any]:
    """État du trading d'une paire — consulté à chaque changement d'onglet."""
    _require_known_symbol(symbol)
    return {"symbol": symbol, "enabled": is_trading_enabled(symbol)}


@router.put("/trading/{symbol}")
async def put_trading_state(symbol: str, toggle: TradingToggle) -> dict[str, Any]:
    """Arme (Trading) ou arrête (Stopped) le trading d'une paire.

    ARMER exige un broker CONNECTÉ : sans ça, armer ne ferait que produire
    l'illusion de trades. Arrêter est toujours autorisé (sécurité)."""
    _require_known_symbol(symbol)
    if toggle.enabled and not broker_runtime.is_connected():
        raise HTTPException(
            status_code=409,
            detail="Broker déconnecté : connectez-vous au broker avant d'armer "
            "une paire. (Pour des trades fictifs, utilisez un compte démo IB.)",
        )
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


def _demo_quote(symbol: str) -> tuple[float, float]:
    """Dernier prix et variation sur ~24 h (démo déterministe).

    Rejoue la MÊME marche aléatoire que ``_demo_candles`` (seed
    symbole+minute) depuis l'origine fixe jusqu'à maintenant : le prix
    renvoyé est donc exactement le close de la dernière bougie du
    graphique. La variation compare le prix courant à celui d'il y a
    1440 minutes (une « journée » de démo).
    """
    origin = _demo_origin_minute()
    now_minute = int(datetime.now(timezone.utc).timestamp() // 60)
    base = _base_price(symbol)
    day_ago_minute = now_minute - 1440
    price = base
    reference = base  # avant un jour complet d'historique : pas de variation
    for minute in range(origin, now_minute + 1):
        rng = random.Random(f"{symbol}:{minute}")
        price = price + rng.uniform(-1, 1) * base * 0.0008
        if minute == day_ago_minute:
            reference = price
    change_pct = (price - reference) / reference * 100 if reference else 0.0
    return round(price, 5), round(change_pct, 2)


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


@router.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Positions ouvertes (broker) + trades exécutés (journal SQL) + P&L.

    RIEN n'est simulé ici : les positions ouvertes viennent de la gateway
    (uniquement si connectée), les trades exécutés du journal SQL. Broker
    déconnecté → listes vides, P&L 0 — l'interface ne ment pas.
    """
    open_positions: list[dict[str, Any]] = []
    gateway = broker_runtime.gateway
    if broker_runtime.is_connected() and gateway is not None:
        for position in await gateway.get_positions():
            open_positions.append(
                {
                    "symbol": position.symbol,
                    "side": "BUY" if position.quantity >= 0 else "SELL",
                    "quantity": abs(position.quantity),
                    "entry_price": position.average_price,
                    "current_price": None,
                    "pnl": position.unrealized_pnl,
                }
            )
    executed_trades = list_recent_trades()  # journal réel (vide sans broker)
    open_pnl = sum(p["pnl"] or 0 for p in open_positions)
    return {
        "broker_connected": broker_runtime.is_connected(),
        "open": open_positions,
        "trades": executed_trades,  # plus récents d'abord
        "total_pnl": round(open_pnl, 2),
    }
