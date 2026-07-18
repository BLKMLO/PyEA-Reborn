"""Stratégies de trading.

Chaque implémentation vit dans son fichier ``strategy_<nom>.py``, hérite de
``Strategy`` (strategy_base.py) et s'enregistre dans le registre
(strategy_registry.py). L'import ci-dessous suffit à déclencher
l'enregistrement — ajouter une stratégie = ajouter un fichier + une ligne ici.
"""

from couleuvre.strategies import strategy_couleuvre_v0_1  # noqa: F401
from couleuvre.strategies.strategy_base import Strategy
from couleuvre.strategies.strategy_registry import get_strategy, register_strategy

__all__ = ["Strategy", "get_strategy", "register_strategy"]
