"""Contrat générique ``BrokerGateway`` + registre d'implémentations.

Le reste du système (stratégie, risque, API web) ne voit QUE cette
interface. Changer de broker = changer la clé ``broker.name`` dans
config.yaml, jamais le code appelant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Type

from pyea.core.core_domain import ExecutionReport, OrderRequest, Position, TickData
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

TickCallback = Callable[[TickData], Awaitable[None]]
#: Reçoit chaque compte rendu d'exécution rapporté par le broker.
ExecutionCallback = Callable[[ExecutionReport], Awaitable[None]]

_REGISTRY: dict[str, Type["BrokerGateway"]] = {}


class BrokerGateway(ABC):
    """Contrat que toute passerelle broker doit implémenter."""

    #: Identifiant unique, utilisé par le registre et la config (broker.name).
    name: str
    #: Nom lisible affiché dans l'UI (liste déroulante, titre de fenêtre).
    #: Défaut = ``name`` si laissé vide.
    label: str = ""

    # --- Description pour la fenêtre de connexion (lecture seule) ---
    def connection_info(self) -> dict[str, str]:
        """Paramètres de connexion à afficher (libellé → valeur).

        Chaque broker décrit les SIENS (IB : hôte/port/client ID ;
        MetaTrader : chemin du terminal). Jamais de secret ici — ces
        paramètres sont en lecture seule dans la fenêtre du dashboard.
        """
        return {}

    def connection_hint(self) -> str:
        """Phrase d'explication affichée sous les paramètres (comment PyEA
        s'authentifie auprès de ce broker). Vide = aucune note."""
        return ""

    # --- Cycle de vie ---
    @abstractmethod
    async def connect(self) -> None:
        """Ouvre la connexion au broker (paper ou live selon la config)."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Ferme proprement la connexion."""

    @abstractmethod
    def is_connected(self) -> bool:
        """État de la connexion, affiché sur le dashboard."""

    # --- Exécution ---
    @abstractmethod
    async def place_order(self, order: OrderRequest) -> str:
        """Envoie un ordre ; retourne l'identifiant d'ordre du broker."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        """Annule un ordre en attente."""

    # --- Comptes rendus d'exécution ---
    #: Destinataire des ``ExecutionReport`` (câblé par ``LiveRuntime``).
    _execution_callback: ExecutionCallback | None = None

    def set_execution_callback(self, callback: ExecutionCallback | None) -> None:
        """Branche (ou débranche) le destinataire des comptes rendus.

        ``place_order`` ne fait que SOUMETTRE : c'est par ce canal que le
        moteur live apprend ce que le broker a réellement fait de l'ordre
        (rempli, annulé, refusé) — ce qui libère la réservation d'ordre en vol
        et alimente le journal des trades. Une gateway qui ne sait pas
        rapporter ses exécutions n'appelle simplement jamais le callback : le
        moteur ne journalisera alors aucun trade, plutôt que d'en inventer.
        """
        self._execution_callback = callback

    async def _emit_execution(self, report: ExecutionReport) -> None:
        """À appeler par l'implémentation quand le broker rapporte un sort.

        Défensif : une erreur du destinataire ne doit jamais casser le flux
        d'exécutions du broker (même principe que l'isolation du bus).
        """
        callback = self._execution_callback
        if callback is None:
            return
        try:
            await callback(report)
        except Exception as exc:  # pragma: no cover - défensif
            logger.warning(
                "Traitement du compte rendu d'exécution %s en échec : %s.",
                report.order_id, exc,
            )

    # --- État du compte ---
    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Positions actuellement ouvertes."""

    @abstractmethod
    async def get_account_summary(self) -> dict[str, float]:
        """Valeur du compte, marge disponible, P&L, etc."""

    # --- Données de marché ---
    @abstractmethod
    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        """S'abonne au flux de prix d'un symbole ; ``on_tick`` reçoit chaque tick."""

    @abstractmethod
    async def unsubscribe_market_data(self, symbol: str) -> None:
        """Coupe le flux de prix d'un symbole."""


def register_gateway(cls: Type[BrokerGateway]) -> Type[BrokerGateway]:
    """Décorateur : ``@register_gateway`` sur une implémentation de BrokerGateway."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} doit définir un attribut de classe 'name'.")
    if cls.name in _REGISTRY:
        raise ValueError(f"Gateway '{cls.name}' déjà enregistrée.")
    _REGISTRY[cls.name] = cls
    return cls


def get_gateway(name: str) -> Type[BrokerGateway]:
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "(aucune)"
        raise KeyError(f"Gateway inconnue '{name}'. Disponibles : {available}")


def list_gateways() -> list[dict[str, str]]:
    """Brokers enregistrés, pour peupler la liste déroulante de l'UI.

    Retour trié : ``[{"name": ..., "label": ...}, ...]``.
    """
    return [
        {"name": cls.name, "label": cls.label or cls.name}
        for _, cls in sorted(_REGISTRY.items())
    ]
