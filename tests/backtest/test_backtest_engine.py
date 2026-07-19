"""Tests du moteur de backtest avec une stratégie scriptée."""

import asyncio
from typing import Any

import pandas as pd

from pyea.backtest import BacktestEngine
from pyea.config.config_settings import get_settings
from pyea.core.core_domain import Signal, SignalAction, TickData
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy


class ScriptedStrategy(Strategy):
    """Émet des actions prédéfinies à des indices de bougie donnés."""

    name = "scripted_test"
    version = "0.0.0"

    def __init__(self, script: dict[int, SignalAction]) -> None:
        self._script = script
        self._index = -1

    async def warmup(self, params: dict[str, Any]) -> None:
        pass

    async def on_tick(self, tick: TickData) -> Signal | None:
        self._index += 1
        action = self._script.get(self._index)
        if action is None:
            return None
        return Signal(strategy_name=self.name, symbol=tick.symbol, action=action)

    async def shutdown(self) -> None:
        pass


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({"bid_close": closes}, index=index)


def _run(script: dict[int, SignalAction], closes: list[float]):
    engine = BacktestEngine(ScriptedStrategy(script), RiskManager(get_settings()))
    return asyncio.run(engine.run("EURUSD", _frame(closes), "H1"))


def test_aller_retour_long_gagnant() -> None:
    # Achat à 1.0 (bougie 0), sortie à 1.5 (bougie 2) → P&L = +0.5 × taille.
    result = _run(
        {0: SignalAction.ENTER_LONG, 2: SignalAction.EXIT}, [1.0, 1.2, 1.5, 1.4]
    )
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "BUY"
    assert trade.entry_price == 1.0 and trade.exit_price == 1.5
    assert trade.pnl == round(0.5 * size, 5)
    assert result.stats["total_pnl"] == round(0.5 * size, 5)
    assert result.stats["win_rate"] == 1.0


def test_short_et_liquidation_fin_de_backtest() -> None:
    # Vente à 2.0 (bougie 1), jamais fermée → liquidée à la dernière (1.0).
    result = _run({1: SignalAction.ENTER_SHORT}, [2.1, 2.0, 1.5, 1.0])
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "SELL"
    assert trade.pnl == round(1.0 * size, 5)  # short 2.0 → 1.0


def test_strategie_muette_zero_trade() -> None:
    result = _run({}, [1.0, 1.1, 1.2])
    assert result.trades == []
    assert result.stats["trades"] == 0
    assert result.stats["win_rate"] is None
    assert result.bars == 3
    # La courbe d'équité existe même sans trade (plate à 0).
    assert all(value == 0.0 for _, value in result.equity_curve)


def test_courbe_equite_bornee() -> None:
    result = _run({}, [1.0] * 3000)
    assert len(result.equity_curve) <= 502  # MAX_EQUITY_POINTS + extrémités
