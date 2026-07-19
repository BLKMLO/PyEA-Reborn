"""Tests de la v1 du RiskManager (taille fixe, plafond de positions)."""

import asyncio

from pyea.config.config_settings import get_settings
from pyea.core.core_domain import OrderSide, Position, Signal, SignalAction
from pyea.risk.risk_manager import RiskManager


def _signal(action: SignalAction) -> Signal:
    return Signal(strategy_name="test", symbol="EURUSD", action=action)


def _position(quantity: float) -> Position:
    return Position(symbol="EURUSD", quantity=quantity, average_price=1.1)


def test_entree_convertie_en_ordre() -> None:
    risk = RiskManager(get_settings())
    order = asyncio.run(risk.evaluate(_signal(SignalAction.ENTER_LONG), []))
    assert order is not None
    assert order.side == OrderSide.BUY
    assert order.quantity == get_settings().risk_max_position_size


def test_entree_rejetee_si_plafond_atteint() -> None:
    risk = RiskManager(get_settings())
    open_positions = [_position(1.0)] * get_settings().risk_max_open_positions
    order = asyncio.run(risk.evaluate(_signal(SignalAction.ENTER_SHORT), open_positions))
    assert order is None


def test_exit_ferme_la_position_existante() -> None:
    risk = RiskManager(get_settings())
    order = asyncio.run(risk.evaluate(_signal(SignalAction.EXIT), [_position(2.0)]))
    assert order is not None
    assert order.side == OrderSide.SELL and order.quantity == 2.0
    # Position short → rachat.
    order = asyncio.run(risk.evaluate(_signal(SignalAction.EXIT), [_position(-2.0)]))
    assert order.side == OrderSide.BUY


def test_exit_sans_position_et_hold_ignores() -> None:
    risk = RiskManager(get_settings())
    assert asyncio.run(risk.evaluate(_signal(SignalAction.EXIT), [])) is None
    assert asyncio.run(risk.evaluate(_signal(SignalAction.HOLD), [])) is None
