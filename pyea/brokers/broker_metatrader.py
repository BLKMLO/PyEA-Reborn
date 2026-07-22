"""Implémentation MetaTrader 5 du contrat ``BrokerGateway``.

Basée sur le paquet officiel ``MetaTrader5`` (IPC vers un terminal MT5 en
cours d'exécution, Windows). Comme Interactive Brokers via TWS/IB Gateway,
**PyEA ne s'authentifie PAS par login/mot de passe** : l'utilisateur ouvre
son terminal MetaTrader 5 et se connecte à son compte (démo ou réel) ;
PyEA s'y *attache* par ``MetaTrader5.initialize()``. Aucun identifiant ne
transite donc par PyEA — même principe honnête que pour IB.

Conséquence sur ``trading_mode`` : côté MT5, démo vs réel dépend du COMPTE
connecté dans le terminal, pas de la config PyEA. Le ``trading_mode`` de
config.yaml reste informatif pour MetaTrader (il pilote uniquement le port
IB). C'est assumé et signalé dans la fenêtre de connexion.

Le paquet ``MetaTrader5`` n'est disponible que sous Windows et n'est PAS
installé dans l'environnement de dev (sandbox Linux) : il est donc importé
**paresseusement** (dans les méthodes, jamais au chargement du module) afin
que la gateway s'enregistre et que l'app démarre partout. Une connexion
sans le paquet lève une erreur claire (« installez MetaTrader5 »), jamais
un import cassé au démarrage. Comme pour le téléchargeur Dukascopy, le
premier run réel est à valider chez l'utilisateur (poste Windows + terminal).

Livrés ici : cycle de vie (``connect``/``disconnect``/``is_connected``),
lecture de compte (``get_positions``/``get_account_summary``), **routage
d'ordres** (``place_order`` = ordre au marché ``TRADE_ACTION_DEAL`` avec
SL/TP attachés NATIVEMENT à la position — équivalent MT5 du bracket IB —,
``cancel_order`` = suppression d'un ordre en attente) et **flux de prix**
(``subscribe_market_data``). Différence majeure avec IB : MetaTrader 5
**n'a pas de callback push** ; le flux se fait par **scrutation** de
``symbol_info_tick`` dans une tâche asyncio (l'appel IPC bloquant est
déporté dans un exécuteur pour ne pas geler la boucle). Comme la connexion,
routage et flux exigent un terminal MT5 réel : écrits sur le même modèle
prudent (import paresseux, échec honnête si déconnecté — jamais d'ordre ni
de tick simulé) et **à valider chez l'utilisateur** au premier run réel.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from pyea.brokers.broker_gateway import (
    BrokerGateway,
    TickCallback,
    register_gateway,
)
from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, OrderSide, Position, TickData
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

#: Intervalle de scrutation du flux de prix (s). MT5 n'a pas de push : on
#: interroge ``symbol_info_tick`` et on ne relaie qu'un tick NOUVEAU (dédup
#: par ``time_msc``). Couleuvre est indexée par bougie (M1+) → 0,25 s suffit
#: largement sans marteler l'IPC du terminal.
_TICK_POLL_INTERVAL = 0.25

#: Écart de prix toléré (points) sur l'ordre au marché (protection de l'ordre
#: contre un déplacement de prix entre la cotation et l'envoi).
_ORDER_DEVIATION = 20

#: Étiquette numérique des ordres émis par PyEA (champ ``magic`` de MT5),
#: pour les distinguer d'ordres passés à la main dans le terminal.
_PYEA_MAGIC = 770077


def _import_mt5() -> Any:
    """Import paresseux du paquet ``MetaTrader5`` avec message clair si absent."""
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de l'OS
        raise ImportError(
            "Le paquet 'MetaTrader5' est requis pour se connecter à MetaTrader 5 "
            "(disponible sous Windows). Installez-le : pip install MetaTrader5, "
            "puis lancez le terminal MT5 et connectez-vous à votre compte."
        ) from exc
    return mt5


@register_gateway
class MetaTraderGateway(BrokerGateway):
    name = "metatrader5"
    label = "MetaTrader 5"

    def __init__(self, settings: Settings) -> None:
        self._terminal_path = settings.mt5_terminal_path
        self._trading_mode = settings.trading_mode
        self._mt5: Any | None = None  # module gardé une fois initialisé
        # Souscriptions de marché vivantes : symbole → (tâche de scrutation,
        # événement d'arrêt). MT5 n'ayant pas de push, chaque symbole a sa
        # boucle de polling.
        self._md: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}

    def connection_info(self) -> dict[str, str]:
        return {
            "Terminal": self._terminal_path or "auto (terminal déjà ouvert)",
            "Compte": "défini dans le terminal MT5",
        }

    def connection_hint(self) -> str:
        return (
            "PyEA s'attache à un terminal MetaTrader 5 déjà lancé et connecté "
            "à votre compte (démo ou réel). Aucun identifiant n'est saisi dans "
            "PyEA : le compte et le type démo/réel se choisissent dans le "
            "terminal MT5."
        )

    async def connect(self) -> None:
        # L'attache au terminal ne prend pas de login/mdp : le compte est déjà
        # connecté dans MT5. `initialize()` accepte un chemin optionnel vers le
        # terminal (utile s'il n'est pas déjà ouvert).
        mt5 = _import_mt5()
        ok = mt5.initialize(self._terminal_path) if self._terminal_path else mt5.initialize()
        if not ok:
            code, message = mt5.last_error()
            raise ConnectionError(
                f"Connexion au terminal MetaTrader 5 impossible ({code}: {message}). "
                "Vérifiez que le terminal est lancé et connecté à un compte."
            )
        self._mt5 = mt5
        info = mt5.account_info()
        if info is not None:
            logger.info(
                "MetaTrader 5 connecté — compte %s (%s), serveur %s.",
                info.login, info.currency, info.server,
            )

    async def disconnect(self) -> None:
        # Couper d'abord les flux de prix (leurs tâches lisent self._mt5).
        for symbol in list(self._md):
            await self.unsubscribe_market_data(symbol)
        if self._mt5 is not None:
            self._mt5.shutdown()
            self._mt5 = None

    def is_connected(self) -> bool:
        # Connecté = terminal initialisé ET un compte visible (le terminal
        # peut être lancé sans compte connecté).
        if self._mt5 is None:
            return False
        try:
            return self._mt5.account_info() is not None
        except Exception:  # pragma: no cover - défensif (terminal fermé)
            return False

    async def get_positions(self) -> list[Position]:
        if self._mt5 is None:
            return []
        raw = self._mt5.positions_get()
        if not raw:
            return []
        POSITION_TYPE_BUY = 0  # MetaTrader5.POSITION_TYPE_BUY
        positions: list[Position] = []
        for p in raw:
            signed = p.volume if p.type == POSITION_TYPE_BUY else -p.volume
            positions.append(
                Position(
                    symbol=p.symbol,
                    quantity=signed,
                    average_price=p.price_open,
                    unrealized_pnl=p.profit,
                )
            )
        return positions

    async def get_account_summary(self) -> dict[str, float]:
        if self._mt5 is None:
            return {}
        info = self._mt5.account_info()
        if info is None:
            return {}
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_free": info.margin_free,
            "profit": info.profit,
        }

    # --- Routage d'ordres ---
    async def place_order(self, order: OrderRequest) -> str:
        """Envoie un ordre au marché avec SL/TP attachés NATIVEMENT.

        Un ``TRADE_ACTION_DEAL`` MT5 accepte directement les champs ``sl`` et
        ``tp`` : le terminal les rattache à la position ouverte (l'un annule
        l'autre à l'exécution). C'est l'équivalent MetaTrader du bracket OCA
        d'IB — les barrières triple-barrier validées par le RiskManager
        deviennent le SL/TP de la position, cohérent avec le modèle backtest
        (barrières = ordres validés à l'entrée, pas un contournement du risque).
        L'entrée au marché en live est l'équivalent de la décision à la clôture
        de bougie du backtest. On ne simule jamais un envoi : déconnecté →
        ``ConnectionError`` ; rejet du terminal → ``RuntimeError`` explicite.
        """
        if not self.is_connected():
            raise ConnectionError(
                "MetaTrader 5 non connecté : impossible d'envoyer un ordre."
            )
        mt5 = self._mt5
        symbol = self._resolve_symbol(symbol=order.symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise ConnectionError(
                f"Prix indisponible pour « {symbol} » (symbole non coté ?)."
            )
        if order.side == OrderSide.BUY:
            mt5_type, price = mt5.ORDER_TYPE_BUY, tick.ask
        else:
            mt5_type, price = mt5.ORDER_TYPE_SELL, tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(abs(order.quantity)),
            "type": mt5_type,
            "price": float(price),
            "deviation": _ORDER_DEVIATION,
            "magic": _PYEA_MAGIC,
            "comment": "PyEA",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        }
        if order.stop_loss is not None:
            request["sl"] = float(order.stop_loss)
        if order.take_profit is not None:
            request["tp"] = float(order.take_profit)

        result = mt5.order_send(request)
        if result is None:
            code, message = mt5.last_error()
            raise RuntimeError(
                f"Envoi d'ordre MetaTrader 5 échoué ({code}: {message})."
            )
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Ordre MetaTrader 5 rejeté — retcode {result.retcode} "
                f"({result.comment})."
            )
        logger.info(
            "Ordre MT5 soumis — %s %s x%s (ticket %s)%s.",
            order.side.value, symbol, request["volume"], result.order,
            " + SL/TP" if ("sl" in request or "tp" in request) else "",
        )
        return str(result.order)

    async def cancel_order(self, order_id: str) -> None:
        """Supprime un ordre EN ATTENTE par son ticket (``TRADE_ACTION_REMOVE``).

        Un ordre au marché déjà exécuté n'est plus annulable (il est devenu une
        position, fermée par son SL/TP) ; ``cancel_order`` cible donc les ordres
        pendants. Ticket introuvable = log, pas d'erreur (déjà exécuté/annulé),
        comme la gateway IB.
        """
        if not self.is_connected():
            raise ConnectionError(
                "MetaTrader 5 non connecté : impossible d'annuler un ordre."
            )
        mt5 = self._mt5
        try:
            ticket = int(order_id)
        except (TypeError, ValueError):
            raise ValueError(f"Identifiant d'ordre MT5 invalide : {order_id!r}.")
        pending = mt5.orders_get(ticket=ticket)
        if not pending:
            logger.warning(
                "Ordre MT5 %s introuvable (déjà exécuté ou annulé ?).", order_id
            )
            return
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            detail = mt5.last_error() if result is None else result.comment
            raise RuntimeError(f"Annulation de l'ordre MT5 {order_id} échouée ({detail}).")
        logger.info("Ordre MT5 %s annulé.", order_id)

    # --- Flux de prix ---
    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        """S'abonne au flux de prix d'un symbole par SCRUTATION.

        MT5 n'expose pas de callback push : on lance une tâche asyncio qui
        interroge ``symbol_info_tick`` toutes ``_TICK_POLL_INTERVAL`` s et ne
        relaie qu'un tick NOUVEAU (dédup par ``time_msc``). L'appel IPC bloquant
        est déporté dans l'exécuteur par défaut pour ne pas geler la boucle.
        """
        if not self.is_connected():
            raise ConnectionError(
                "MetaTrader 5 non connecté : flux de prix indisponible."
            )
        if symbol in self._md:
            return  # déjà abonné
        resolved = self._resolve_symbol(symbol=symbol)
        stop = asyncio.Event()
        task = asyncio.ensure_future(self._poll_ticks(resolved, on_tick, stop))
        self._md[symbol] = (task, stop)
        logger.info("Flux de marché MT5 abonné — %s.", resolved)

    async def unsubscribe_market_data(self, symbol: str) -> None:
        entry = self._md.pop(symbol, None)
        if entry is None:
            return
        task, stop = entry
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # pragma: no cover - défensif
            pass
        logger.info("Flux de marché MT5 coupé — %s.", symbol)

    async def _poll_ticks(
        self, symbol: str, on_tick: TickCallback, stop: asyncio.Event
    ) -> None:
        """Boucle de scrutation d'un symbole (une par souscription)."""
        loop = asyncio.get_running_loop()
        last_time_msc: int | None = None
        while not stop.is_set():
            mt5 = self._mt5
            if mt5 is None:  # déconnecté entre-temps
                return
            # IPC bloquant → exécuteur, la boucle asyncio reste réactive.
            tick = await loop.run_in_executor(None, mt5.symbol_info_tick, symbol)
            if tick is not None and tick.time_msc != last_time_msc:
                last_time_msc = tick.time_msc
                td = self._to_tick_data(symbol, tick)
                if td is not None:
                    await on_tick(td)
            await asyncio.sleep(_TICK_POLL_INTERVAL)

    @staticmethod
    def _to_tick_data(symbol: str, tick: Any) -> TickData | None:
        """Normalise un tick MT5 en ``TickData`` (mid bid/ask, sinon last)."""
        bid, ask = tick.bid, tick.ask
        if bid and ask:
            price = (bid + ask) / 2
        else:  # forex : last souvent 0 → on prend le seul côté coté
            price = tick.last or bid or ask
        if not price or not math.isfinite(price):
            return None  # ticker pas prêt : jamais de prix fabriqué
        volume = getattr(tick, "volume_real", None) or getattr(tick, "volume", None)
        return TickData(
            symbol=symbol,
            price=float(price),
            volume=float(volume) if volume else None,
            timestamp=datetime.fromtimestamp(tick.time, tz=timezone.utc),
        )

    def _resolve_symbol(self, symbol: str) -> str:
        """Vérifie qu'un symbole existe et l'ajoute au Market Watch du terminal.

        ``symbol_select(symbol, True)`` est requis avant de coter ou de trader
        un symbole non affiché. Échec = symbole inconnu du courtier → erreur
        claire (le MarketDataFeed saute alors ce symbole sans couper les autres).
        """
        mt5 = self._mt5
        normalized = symbol.replace("/", "").upper()
        if not mt5.symbol_select(normalized, True):
            code, message = mt5.last_error()
            raise ValueError(
                f"Symbole « {symbol} » indisponible dans le terminal MT5 "
                f"({code}: {message}). Vérifiez son nom exact chez votre courtier."
            )
        return normalized

    def _filling_mode(self, symbol: str) -> int:
        """Mode d'exécution accepté par le symbole (IOC préféré, sinon FOK).

        Le mode de remplissage autorisé dépend du courtier ; un mode non
        supporté fait rejeter l'ordre. On lit le masque ``filling_mode`` du
        symbole et on choisit un mode compatible pour un ordre au marché.
        """
        mt5 = self._mt5
        info = mt5.symbol_info(symbol)
        allowed = getattr(info, "filling_mode", 0) if info is not None else 0
        if allowed & mt5.SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if allowed & mt5.SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_IOC  # défaut prudent (le plus répandu)
