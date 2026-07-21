"""Implémentation Interactive Brokers du contrat ``BrokerGateway``.

Basée sur ``ib_async`` (fork communautaire maintenu d'ib_insync) : API
asyncio de haut niveau au-dessus de TWS/IB Gateway, bien plus simple à
maintenir que ``ibapi`` natif.

**PyEA ne s'authentifie PAS par login/mot de passe** : c'est TWS / IB
Gateway (déjà logué par l'utilisateur) qui gère le compte. PyEA se connecte
au *socket API* du terminal via hôte/port/client ID (config). Le mode
paper/live est entièrement déterminé par la config : seule la valeur de
``settings.ib_port`` change (7497 paper / 7496 live pour TWS).

``ib_async`` est importé **paresseusement** (dans les méthodes) — même
principe que MetaTrader : la gateway s'enregistre et l'app démarre même si
le paquet n'est pas installé (sandbox de dev). Une connexion sans le paquet
lève une ImportError claire (« installez ib_async »), jamais un import
cassé au démarrage. Comme pour MetaTrader et Dukascopy, **le premier run
réel (TWS ouvert, API activée) est à valider chez l'utilisateur** : la
sandbox n'a ni le paquet ni un terminal IB.

Livrés ici : cycle de vie (``connect``/``disconnect``/``is_connected``) et
lecture de compte (``get_positions``/``get_account_summary``) — tous
read-only, sûrs à écrire sans test live. Le **routage d'ordres**
(``place_order``/``cancel_order``) et le **flux de prix**
(``subscribe_market_data``) restent en ``NotImplementedError`` tant que le
flux live n'est pas monté dans ``app_factory`` — on ne simule jamais un
envoi d'ordre.
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

#: Délai d'attente (s) de la connexion au socket API. Court : si TWS est
#: éteint ou l'API désactivée, on veut échouer vite avec un message clair.
_CONNECT_TIMEOUT = 8.0

#: Tags du résumé de compte IB → clés normalisées (mêmes intitulés que MT5,
#: pour une lecture homogène côté API/UI). Valeurs IB en devise de base.
_ACCOUNT_TAGS = {
    "NetLiquidation": "equity",
    "TotalCashValue": "balance",
    "FullMaintMarginReq": "margin",
    "AvailableFunds": "margin_free",
    "UnrealizedPnL": "profit",
}


def _import_ib() -> Any:
    """Import paresseux du paquet ``ib_async`` avec message clair si absent."""
    try:
        from ib_async import IB  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de l'install
        raise ImportError(
            "Le paquet 'ib_async' est requis pour se connecter à Interactive "
            "Brokers. Installez-le : pip install ib_async, puis lancez TWS ou "
            "IB Gateway avec l'API socket activée."
        ) from exc
    return IB


@register_gateway
class InteractiveBrokersGateway(BrokerGateway):
    name = "interactive_brokers"
    label = "Interactive Brokers"

    def __init__(self, settings: Settings) -> None:
        self._host = settings.ib_host
        self._port = settings.ib_port  # paper ou live selon trading_mode
        self._client_id = settings.ib_client_id
        self._ib: Any | None = None  # instance ib_async.IB une fois connectée

    def connection_info(self) -> dict[str, str]:
        return {
            "Hôte": self._host,
            "Port": str(self._port),
            "Client ID": str(self._client_id),
        }

    def connection_hint(self) -> str:
        return (
            "L'authentification se fait dans TWS / IB Gateway (session déjà "
            "ouverte). PyEA s'y connecte via le socket API — hôte, port et "
            "client ID se règlent dans .env / config.yaml."
        )

    async def connect(self) -> None:
        # L'API IB ne prend PAS de login/mot de passe : l'authentification est
        # gérée par TWS / IB Gateway (déjà logué par l'utilisateur). PyEA se
        # connecte au socket API via host/port/client_id (config).
        IB = _import_ib()
        ib = IB()
        try:
            await ib.connectAsync(
                self._host,
                self._port,
                clientId=self._client_id,
                timeout=_CONNECT_TIMEOUT,
                readonly=False,
            )
        except Exception as exc:  # TWS éteint, API désactivée, port occupé…
            # Nettoyage défensif : connectAsync peut laisser un socket ouvert.
            try:
                ib.disconnect()
            except Exception:  # pragma: no cover - défensif
                pass
            raise ConnectionError(
                f"Connexion à Interactive Brokers impossible ({self._host}:"
                f"{self._port}, client {self._client_id}) : {exc}. Vérifiez que "
                "TWS / IB Gateway est lancé, l'API socket activée et le port "
                "correct (7497 paper / 7496 live)."
            ) from exc
        self._ib = ib
        account = ", ".join(ib.managedAccounts()) or "?"
        logger.info(
            "Interactive Brokers connecté — %s:%s (client %s), compte(s) %s.",
            self._host, self._port, self._client_id, account,
        )

    async def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()  # synchrone côté ib_async
            self._ib = None

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return bool(self._ib.isConnected())
        except Exception:  # pragma: no cover - défensif (socket coupé)
            return False

    async def get_positions(self) -> list[Position]:
        if not self.is_connected():
            return []
        raw = await self._ib.reqPositionsAsync()
        if not raw:
            return []
        # Le P&L latent n'est pas porté par les positions : on le récupère du
        # portefeuille (peuplé par les mises à jour de compte), indexé par conId.
        pnl_by_conid = {
            item.contract.conId: item.unrealizedPNL
            for item in self._ib.portfolio()
        }
        positions: list[Position] = []
        for p in raw:
            if not p.position:  # position soldée (0) — ignorée
                continue
            contract = p.contract
            positions.append(
                Position(
                    symbol=contract.localSymbol or contract.symbol,
                    quantity=p.position,  # déjà signé (long > 0, short < 0)
                    average_price=p.avgCost,
                    unrealized_pnl=pnl_by_conid.get(contract.conId),
                )
            )
        return positions

    async def get_account_summary(self) -> dict[str, float]:
        if not self.is_connected():
            return {}
        values = await self._ib.accountSummaryAsync()
        summary: dict[str, float] = {}
        for av in values:
            key = _ACCOUNT_TAGS.get(av.tag)
            if key is None:
                continue
            try:
                summary[key] = float(av.value)
            except (TypeError, ValueError):  # pragma: no cover - défensif
                continue
        return summary

    # --- Routage d'ordres et flux de prix : câblage live à venir ---
    # Comme pour MetaTrader, aucune de ces méthodes n'a d'appelant tant que le
    # flux live (Strategy → RiskManager → BrokerGateway + MarketDataFeed) n'est
    # pas monté dans app_factory. On ne simule surtout jamais un envoi d'ordre.
    async def place_order(self, order: OrderRequest) -> str:
        raise NotImplementedError(
            "Envoi d'ordre Interactive Brokers à câbler avec le flux live "
            "(bracket ib_async : ordre parent + stop/limit attachés)."
        )

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError(
            "Annulation d'ordre Interactive Brokers à câbler avec le flux live."
        )

    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        raise NotImplementedError(
            "Flux de prix Interactive Brokers à câbler avec le MarketDataFeed live."
        )

    async def unsubscribe_market_data(self, symbol: str) -> None:
        raise NotImplementedError
