"""Couleuvre_v0.1 — stratégie basée sur un modèle LightGBM.

Squelette volontairement vide : les features, les seuils de décision et
la walk-forward validation seront développés plus tard. Seul le contrat
``Strategy`` est en place, ce qui suffit pour brancher le moteur, l'API
et le dashboard dès maintenant.
"""

from __future__ import annotations

from typing import Any

from pyea.core.core_domain import Signal, TickData
from pyea.strategies.strategy_base import Strategy
from pyea.strategies.strategy_registry import register_strategy


@register_strategy
class CouleuvreV01(Strategy):
    name = "couleuvre_v0_1"
    version = "0.1.0"

    async def warmup(self, params: dict[str, Any]) -> None:
        # Plus tard : chargement du modèle LightGBM et de l'historique de features.
        pass

    async def on_tick(self, tick: TickData) -> Signal | None:
        # Plus tard : calcul des features, inférence LightGBM, seuils de décision.
        return None

    async def shutdown(self) -> None:
        pass
