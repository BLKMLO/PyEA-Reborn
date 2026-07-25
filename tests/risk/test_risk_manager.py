"""Tests du RiskManager : taille fixe, plafonds de positions (par symbole et
sur le compte) et limite de perte journalière."""

import asyncio

from pyea.config.config_settings import get_settings
from pyea.core.core_domain import (
    AccountState,
    OrderSide,
    Position,
    Signal,
    SignalAction,
)
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


# --- Plafonds distincts : par symbole vs sur le compte ---------------------
# Les confondre gelait toutes les paires armées dès qu'UNE position existait.


def _risk(**overrides) -> RiskManager:
    settings = get_settings().model_copy(update=overrides)
    return RiskManager(settings)


def test_position_sur_une_autre_paire_ne_bloque_pas() -> None:
    risk = _risk(risk_max_positions_per_symbol=1, risk_max_open_positions=5)
    autre = Position(symbol="GBPUSD", quantity=1.0, average_price=1.3)
    order = asyncio.run(risk.evaluate(_signal(SignalAction.ENTER_LONG), [autre]))
    assert order is not None, "une position sur GBPUSD ne doit pas geler EURUSD"


def test_plafond_par_symbole_bloque_l_empilement() -> None:
    risk = _risk(risk_max_positions_per_symbol=1, risk_max_open_positions=5)
    order = asyncio.run(
        risk.evaluate(_signal(SignalAction.ENTER_LONG), [_position(1.0)])
    )
    assert order is None


def test_plafond_de_compte_borne_l_exposition_totale() -> None:
    risk = _risk(risk_max_positions_per_symbol=1, risk_max_open_positions=2)
    ouvertes = [
        Position(symbol="GBPUSD", quantity=1.0, average_price=1.3),
        Position(symbol="USDJPY", quantity=1.0, average_price=150.0),
    ]
    order = asyncio.run(risk.evaluate(_signal(SignalAction.ENTER_LONG), ouvertes))
    assert order is None


# --- Perte journalière maximale (garde LIVE) -------------------------------


def test_perte_journaliere_bloque_les_entrees() -> None:
    risk = _risk(risk_max_daily_loss_pct=2.0)
    compte = AccountState(equity=9_750.0, day_start_equity=10_000.0)  # -2,5 %
    assert asyncio.run(
        risk.evaluate(_signal(SignalAction.ENTER_LONG), [], compte)
    ) is None


def test_perte_journaliere_laisse_toujours_sortir() -> None:
    # Quel que soit l'état du compte, on doit TOUJOURS pouvoir fermer.
    risk = _risk(risk_max_daily_loss_pct=2.0)
    compte = AccountState(equity=5_000.0, day_start_equity=10_000.0)  # -50 %
    order = asyncio.run(
        risk.evaluate(_signal(SignalAction.EXIT), [_position(2.0)], compte)
    )
    assert order is not None and order.side == OrderSide.SELL


def test_sous_le_plafond_l_entree_passe() -> None:
    risk = _risk(risk_max_daily_loss_pct=2.0)
    compte = AccountState(equity=9_900.0, day_start_equity=10_000.0)  # -1 %
    assert asyncio.run(
        risk.evaluate(_signal(SignalAction.ENTER_LONG), [], compte)
    ) is not None


def test_sans_etat_de_compte_la_regle_ne_fait_pas_semblant() -> None:
    # Backtest : aucune équité réelle → la limite ne s'applique pas, et le
    # RiskManager ne prétend pas l'appliquer (cf. docstring du module).
    risk = _risk(risk_max_daily_loss_pct=2.0)
    assert asyncio.run(risk.evaluate(_signal(SignalAction.ENTER_LONG), [])) is not None


def test_plafond_a_zero_desactive_la_limite() -> None:
    risk = _risk(risk_max_daily_loss_pct=0.0)
    compte = AccountState(equity=1.0, day_start_equity=10_000.0)  # -99,99 %
    assert asyncio.run(
        risk.evaluate(_signal(SignalAction.ENTER_LONG), [], compte)
    ) is not None
