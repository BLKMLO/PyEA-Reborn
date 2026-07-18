"""Vérifie que le registre expose bien Couleuvre_v0.1 et respecte le contrat."""

from pyea.strategies import Strategy, get_strategy
from pyea.strategies.strategy_registry import list_strategies


def test_couleuvre_v0_1_est_enregistree() -> None:
    assert "couleuvre_v0_1" in list_strategies()


def test_couleuvre_v0_1_respecte_le_contrat() -> None:
    cls = get_strategy("couleuvre_v0_1")
    assert issubclass(cls, Strategy)
    assert cls.name == "couleuvre_v0_1"
