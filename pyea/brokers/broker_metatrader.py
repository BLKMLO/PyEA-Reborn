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
"""

from __future__ import annotations

from typing import Any

from pyea.brokers.broker_gateway import (
    BrokerGateway,
    TickCallback,
    register_gateway,
)
from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, Position
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


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

    # --- Routage d'ordres et flux de prix : câblage live à venir ---
    # Comme pour IB, aucune de ces méthodes n'a d'appelant tant que le flux
    # live (Strategy → RiskManager → BrokerGateway + MarketDataFeed) n'est pas
    # monté dans app_factory. On ne simule surtout pas un envoi d'ordre.
    async def place_order(self, order: OrderRequest) -> str:
        raise NotImplementedError(
            "Envoi d'ordre MetaTrader 5 à câbler avec le flux live (order_send)."
        )

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError(
            "Annulation d'ordre MetaTrader 5 à câbler avec le flux live."
        )

    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        raise NotImplementedError(
            "Flux de prix MetaTrader 5 à câbler avec le MarketDataFeed live."
        )

    async def unsubscribe_market_data(self, symbol: str) -> None:
        raise NotImplementedError
