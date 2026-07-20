"""Implémentation Interactive Brokers du contrat ``BrokerGateway``.

Basée sur ``ib_async`` (fork communautaire maintenu d'ib_insync) : API
asyncio de haut niveau au-dessus de TWS/IB Gateway, bien plus simple à
maintenir que ``ibapi`` natif.

Le mode paper/live est entièrement déterminé par la config : seule la
valeur de ``settings.ib_port`` change (7497 paper / 7496 live pour TWS).

Squelette volontairement vide : les appels ib_async réels seront écrits
lors du branchement de l'exécution.
"""

from __future__ import annotations

from pyea.brokers.broker_credentials import broker_credentials
from pyea.brokers.broker_gateway import (
    BrokerGateway,
    TickCallback,
    register_gateway,
)
from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, Position
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


@register_gateway
class InteractiveBrokersGateway(BrokerGateway):
    name = "interactive_brokers"

    def __init__(self, settings: Settings) -> None:
        self._host = settings.ib_host
        self._port = settings.ib_port  # paper ou live selon trading_mode
        self._client_id = settings.ib_client_id
        self._connected = False

    async def connect(self) -> None:
        # Host/port/client_id viennent de la config ; les identifiants de
        # connexion (nom d'utilisateur + mot de passe) sont saisis au runtime
        # depuis le dashboard et lus ici via ``broker_credentials`` (jamais
        # écrits sur disque).
        _ = broker_credentials.password  # utilisé par IB.connectAsync à venir
        raise NotImplementedError("À implémenter avec ib_async (IB.connectAsync).")

    async def disconnect(self) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        return self._connected

    async def place_order(self, order: OrderRequest) -> str:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    async def get_account_summary(self) -> dict[str, float]:
        raise NotImplementedError

    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        raise NotImplementedError

    async def unsubscribe_market_data(self, symbol: str) -> None:
        raise NotImplementedError
