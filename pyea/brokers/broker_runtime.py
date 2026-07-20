"""État d'exécution du broker (gateway active + connexion).

Singleton de module, même statut assumé que ``broker_credentials``,
``event_bus`` et ``job_manager`` (cf. points de vigilance de CLAUDE.md) :
il donne à l'API un point d'accès unique à la gateway et à son état de
connexion RÉEL — plus de ``broker_connected`` codé en dur.

Principe : PyEA ne fabrique jamais de positions ni de trades. Tant que la
gateway n'est pas connectée, l'interface montre honnêtement « déconnecté »,
zéro position, zéro trade — l'utilisateur ne doit pas pouvoir confondre la
démo de rendu avec un compte réel.
"""

from __future__ import annotations

from pyea.brokers.broker_gateway import BrokerGateway
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class BrokerRuntime:
    def __init__(self) -> None:
        self._gateway: BrokerGateway | None = None

    def set_gateway(self, gateway: BrokerGateway) -> None:
        """Câblé par ``app_factory`` au démarrage (une gateway par serveur)."""
        self._gateway = gateway

    @property
    def gateway(self) -> BrokerGateway | None:
        return self._gateway

    def is_connected(self) -> bool:
        return self._gateway is not None and self._gateway.is_connected()

    async def connect(self) -> None:
        if self._gateway is None:
            raise RuntimeError("Aucune gateway broker configurée.")
        await self._gateway.connect()

    async def disconnect(self) -> None:
        if self._gateway is not None and self._gateway.is_connected():
            await self._gateway.disconnect()


# Instance unique de l'application.
broker_runtime = BrokerRuntime()
