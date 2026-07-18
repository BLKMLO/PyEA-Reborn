"""Registre de stratégies (pattern plugin).

Une stratégie se déclare avec le décorateur ``@register_strategy`` ; le
moteur la retrouve par son nom (clé ``strategy.name`` de config.yaml).
Ajouter une stratégie ne demande AUCUNE modification du moteur.
"""

from __future__ import annotations

from typing import Type

from couleuvre.strategies.strategy_base import Strategy

_REGISTRY: dict[str, Type[Strategy]] = {}


def register_strategy(cls: Type[Strategy]) -> Type[Strategy]:
    """Décorateur de classe : ``@register_strategy`` sur une sous-classe de Strategy."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} doit définir un attribut de classe 'name'.")
    if cls.name in _REGISTRY:
        raise ValueError(f"Stratégie '{cls.name}' déjà enregistrée.")
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str) -> Type[Strategy]:
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "(aucune)"
        raise KeyError(f"Stratégie inconnue '{name}'. Disponibles : {available}")


def list_strategies() -> list[str]:
    return sorted(_REGISTRY)
