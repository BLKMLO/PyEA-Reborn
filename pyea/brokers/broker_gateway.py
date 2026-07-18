"""Contrat générique ``BrokerGateway`` + registre d'implémentations.

Le reste du système (stratégie, risque, API web) ne voit QUE cette
interface. Changer de broker = changer la clé ``broker.name`` dans
config.yaml, jamais le code appelant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Type

from pyea.core.core_domain import OrderRequest, Position, TickData

TickCallback = Callable[[TickData], Awaitable[None]]

_REGISTRY: dict[str, Type["BrokerGateway"]] = {}


class BrokerGateway(ABC):
    """Contrat que toute passerelle broker doit implémenter."""

    #: Identifiant unique, utilisé par le registre et la config (broker.name).
    name: str

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
