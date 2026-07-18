"""Contrat abstrait que toute stratégie doit respecter.

Le moteur ne connaît que cette interface : il pousse des ticks, récupère
des ``Signal``. Une stratégie ne parle jamais directement au broker ni à
la base — c'est ce qui la rend testable et interchangeable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pyea.core.core_domain import Signal, TickData


class Strategy(ABC):
    """Interface commune à toutes les stratégies."""

    #: Identifiant unique, utilisé par le registre et la config (strategy.name).
    name: str
    #: Version sémantique de la stratégie, tracée avec chaque signal.
    version: str

    @abstractmethod
    async def warmup(self, params: dict[str, Any]) -> None:
        """Prépare la stratégie (chargement du modèle, historique, features)."""

    @abstractmethod
    async def on_tick(self, tick: TickData) -> Signal | None:
        """Traite un point de marché ; retourne un signal ou ``None``."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Libère proprement les ressources (modèle, buffers)."""

    def describe(self) -> dict[str, str]:
        """Métadonnées affichées sur le dashboard."""
        return {"name": self.name, "version": self.version}
