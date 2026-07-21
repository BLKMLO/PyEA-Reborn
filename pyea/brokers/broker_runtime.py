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

from typing import Any

from pyea.brokers.broker_gateway import BrokerGateway, get_gateway, list_gateways
from pyea.config.config_settings import Settings, get_settings
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class BrokerRuntime:
    """Broker ACTIF (une gateway à la fois) + son état de connexion réel.

    Le broker actif est celui de la config au démarrage, mais l'utilisateur
    peut en CHANGER depuis la fenêtre de connexion (liste déroulante). Un seul
    broker trade à la fois (un compte) : basculer exige d'être déconnecté du
    broker courant. La config (``broker.name``) reste le défaut au démarrage —
    un changement runtime ne la réécrit pas.
    """

    def __init__(self) -> None:
        self._gateway: BrokerGateway | None = None

    def set_gateway(self, gateway: BrokerGateway) -> None:
        """Câblé par ``app_factory`` au démarrage (broker par défaut = config)."""
        self._gateway = gateway

    @property
    def gateway(self) -> BrokerGateway | None:
        return self._gateway

    @property
    def active_name(self) -> str | None:
        return self._gateway.name if self._gateway is not None else None

    def select(self, name: str, settings: Settings | None = None) -> None:
        """Change le broker actif (par nom du registre).

        Refuse le changement si le broker courant est CONNECTÉ (il faut se
        déconnecter d'abord — on ne bascule pas un compte sous les pieds d'une
        connexion vivante). Sélectionner le broker déjà actif est sans effet.
        """
        if self._gateway is not None and self._gateway.name == name:
            return
        if self.is_connected():
            raise RuntimeError(
                "Déconnectez-vous du broker courant avant d'en changer."
            )
        settings = settings or get_settings()
        self._gateway = get_gateway(name)(settings)
        logger.info("Broker actif → %s", name)

    def is_connected(self) -> bool:
        return self._gateway is not None and self._gateway.is_connected()

    async def connect(self) -> None:
        if self._gateway is None:
            raise RuntimeError("Aucune gateway broker configurée.")
        await self._gateway.connect()

    async def disconnect(self) -> None:
        if self._gateway is not None and self._gateway.is_connected():
            await self._gateway.disconnect()

    def available(self, settings: Settings | None = None) -> list[dict[str, Any]]:
        """Tous les brokers enregistrés + leurs infos, pour la liste déroulante.

        Le broker actif est décrit par SON instance vivante (état de connexion
        réel) ; les autres sont instanciés le temps de lire leurs paramètres
        (constructeur léger : lecture de la config, aucun import broker lourd).
        """
        settings = settings or get_settings()
        active = self.active_name
        result: list[dict[str, Any]] = []
        for entry in list_gateways():
            name = entry["name"]
            if name == active and self._gateway is not None:
                gateway: BrokerGateway = self._gateway
                connected = self._gateway.is_connected()
            else:
                gateway = get_gateway(name)(settings)
                connected = False
            result.append(
                {
                    "name": name,
                    "label": gateway.label or name,
                    "params": gateway.connection_info(),
                    "hint": gateway.connection_hint(),
                    "connected": connected,
                    "active": name == active,
                }
            )
        return result


# Instance unique de l'application.
broker_runtime = BrokerRuntime()
