"""Singleton d'exécution du flux live (feed + moteur).

Même statut que ``broker_runtime`` (singleton de module, cf. points de
vigilance de CLAUDE.md) : il donne à l'application UN point d'accès pour
démarrer/arrêter le flux temps réel. ``app_factory`` le configure au
démarrage ; les endpoints de connexion/déconnexion broker le pilotent (le
flux n'a de sens que broker connecté).

Câblage (règle #2 : uniquement ici et dans ``app_factory``) :
``MarketDataFeed`` (broker → bus) et ``LiveTradingEngine`` (bus → flux
strict → broker) sont assemblés ici avec leurs dépendances réelles
(``broker_runtime`` pour la gateway connectée, la config pour le
kill-switch global, la table SQLite pour les paires armées).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from pyea.brokers.broker_gateway import BrokerGateway
from pyea.brokers.broker_runtime import broker_runtime
from pyea.config.config_settings import Settings, get_settings
from pyea.core.core_events import EventBus, event_bus
from pyea.core.core_logging import get_logger
from pyea.data.data_history_downloader import load_history, resample_history
from pyea.data.data_market_feed import MarketDataFeed
from pyea.live.live_engine import LiveTradingEngine
from pyea.live.live_models import resolve_live_model
from pyea.risk.risk_manager import RiskManager
from pyea.storage.storage_trading_state import is_trading_enabled
from pyea.strategies.strategy_registry import get_strategy

logger = get_logger(__name__)

#: Fenêtre d'historique local chargée pour amorcer la chauffe live d'un
#: symbole (jours). Au-delà, le tampon glissant de la stratégie tronque de
#: toute façon ; en deçà, un modèle D1 aurait trop peu de bougies.
_WARMUP_HISTORY_DAYS = 400
#: Bougies de chauffe conservées (tail) avant remise à la stratégie.
_WARMUP_SEED_BARS = 500


class LiveRuntime:
    """Assemble et pilote le feed + le moteur live de l'application."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._settings: Settings | None = None
        self._strategy_name: str = ""
        self._feed: MarketDataFeed | None = None
        self._engine: LiveTradingEngine | None = None
        self._running = False

    def configure(self, settings: Settings) -> None:
        """Câblé par ``app_factory`` au démarrage."""
        self._settings = settings
        self._strategy_name = settings.strategy_name

    @property
    def is_running(self) -> bool:
        return self._running

    def _connected_gateway(self) -> BrokerGateway | None:
        """La gateway active si (et seulement si) elle est connectée."""
        gateway = broker_runtime.gateway
        if gateway is not None and gateway.is_connected():
            return gateway
        return None

    def _build_engine(self, settings: Settings) -> LiveTradingEngine:
        strategy_cls = get_strategy(settings.strategy_name)
        return LiveTradingEngine(
            bus=self._bus,
            risk_manager=RiskManager(settings),
            strategy_factory=lambda: strategy_cls(),
            connected_gateway=self._connected_gateway,
            # Kill-switch global relu à chaque tick (get_settings est caché).
            is_globally_enabled=lambda: get_settings().strategy_enabled,
            is_symbol_armed=is_trading_enabled,
        )

    def _warmup_for(self, symbol: str) -> dict[str, Any]:
        """Paramètres de chauffe live d'un symbole : modèle + historique récent.

        Sélectionne le modèle du dernier run réussi de la paire, charge un
        historique récent (chauffe des features/indicateurs récursifs), et
        renvoie les paramètres du mode live de la stratégie. **Aucun modèle
        entraîné → ``{}``** : la stratégie reste muette (honnête, jamais un
        trade sans modèle).
        """
        model = resolve_live_model(self._strategy_name, symbol)
        if model is None:
            logger.info(
                "Live %s : aucun modèle entraîné → paire non tradée (honnête).",
                symbol,
            )
            return {}
        frame = self._load_warmup_frame(symbol, model.timeframe)
        logger.info(
            "Live %s : modèle %s (pli %d, %s), chauffe %s bougies.",
            symbol, model.run_id, model.fold, model.timeframe,
            0 if frame is None else len(frame),
        )
        return {
            "live": True,
            "model_path": str(model.model_path),
            "timeframe": model.timeframe,
            "frame": frame,
        }

    def _load_warmup_frame(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """Historique local récent, ré-échantillonné au timeframe du modèle.

        Best-effort : l'absence d'historique local n'empêche PAS le live (le
        tampon de la stratégie se remplira des bougies live) — on renvoie
        ``None`` et on log, sans lever.
        """
        settings = self._settings or get_settings()
        data_dir = Path(settings.history_data_dir)
        start = pd.Timestamp(
            datetime.now(timezone.utc) - timedelta(days=_WARMUP_HISTORY_DAYS)
        )
        try:
            frame = load_history(data_dir, symbol, start=start, end=None)
        except FileNotFoundError:
            try:  # fenêtre récente absente : on prend tout ce qui existe
                frame = load_history(data_dir, symbol)
            except FileNotFoundError:
                logger.info("Live %s : pas d'historique local pour la chauffe.", symbol)
                return None
        except Exception as exc:  # historique illisible : on n'échoue pas le live
            logger.warning("Live %s : historique de chauffe ignoré (%s).", symbol, exc)
            return None
        resampled = resample_history(frame, timeframe)
        if resampled.empty:
            return None
        return resampled.tail(_WARMUP_SEED_BARS)

    async def start(self) -> None:
        """Démarre le flux live (idempotent). Broker connecté requis.

        Le moteur est démarré et abonné au bus AVANT le feed : ainsi, même si
        le flux de prix du broker n'est pas encore câblé
        (``NotImplementedError``), le moteur est prêt et l'échec de feed reste
        cantonné (aucun tick fabriqué).
        """
        if self._running:
            return
        if self._connected_gateway() is None:
            logger.info("Flux live non démarré : broker déconnecté.")
            return
        settings = self._settings or get_settings()
        symbols = list(settings.history_instruments)

        self._engine = self._build_engine(settings)
        # Chaque symbole est chauffé avec SON modèle (un modèle par actif) :
        # le provider résout le dernier modèle entraîné + l'historique récent.
        # Aucun modèle pour un symbole → stratégie muette (honnête), pas d'erreur.
        await self._engine.start(symbols, warmup_provider=self._warmup_for)

        gateway = broker_runtime.gateway
        assert gateway is not None  # garanti par _connected_gateway ci-dessus
        self._feed = MarketDataFeed(gateway, self._bus)
        try:
            await self._feed.start(symbols)
        except NotImplementedError:
            # Flux de prix broker pas encore câblé : le moteur reste en place
            # (prêt), simplement sans ticks. Déjà journalisé par le feed.
            self._feed = None
        self._running = True
        logger.info("Flux live actif.")

    async def stop(self) -> None:
        """Arrête proprement feed + moteur (idempotent)."""
        if not self._running:
            return
        if self._feed is not None:
            await self._feed.stop()
            self._feed = None
        if self._engine is not None:
            await self._engine.stop()
            self._engine = None
        self._running = False
        logger.info("Flux live arrêté.")


# Instance unique de l'application.
live_runtime = LiveRuntime(event_bus)
