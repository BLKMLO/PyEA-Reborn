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


class BarrierStrategy(Strategy):
    """Entre une fois (bougie 0) avec des barrières stop/take-profit fixes."""

    name = "barrier_test"
    version = "0.0.0"

    def __init__(self, action: SignalAction, stop_loss: float, take_profit: float) -> None:
        self._action = action
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._index = -1

    async def warmup(self, params: dict[str, Any]) -> None:
        pass

    async def on_tick(self, tick: TickData) -> Signal | None:
        self._index += 1
        if self._index != 0:
            return None
        return Signal(
            strategy_name=self.name,
            symbol=tick.symbol,
            action=self._action,
            stop_loss=self._stop_loss,
            take_profit=self._take_profit,
        )

    async def shutdown(self) -> None:
        pass


def _frame(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({"bid_close": closes}, index=index)


def _frame_ohlc(bars: list[tuple[float, float, float]]) -> pd.DataFrame:
    """bars = [(high, low, close), ...] ; index horaire lundi (même semaine)."""
    index = pd.date_range("2024-01-01", periods=len(bars), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "bid_high": [b[0] for b in bars],
            "bid_low": [b[1] for b in bars],
            "bid_close": [b[2] for b in bars],
        },
        index=index,
    )


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


def _run_barrier(strategy: BarrierStrategy, frame: pd.DataFrame):
    engine = BacktestEngine(strategy, RiskManager(get_settings()))
    return asyncio.run(engine.run("EURUSD", frame, "H1"))


def test_barriere_take_profit_long() -> None:
    # Entrée long à 1.00 (bougie 0) ; bougie 1 monte à 1.15 → TP 1.10 touché.
    strategy = BarrierStrategy(SignalAction.ENTER_LONG, stop_loss=0.95, take_profit=1.10)
    frame = _frame_ohlc([(1.01, 0.99, 1.00), (1.15, 1.02, 1.05), (1.20, 1.10, 1.18)])
    result = _run_barrier(strategy, frame)
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == 1.00 and trade.exit_price == 1.10
    assert trade.pnl == round(0.10 * size, 5)


def test_barriere_stop_loss_long() -> None:
    # Bougie 1 plonge à 0.90 → SL 0.95 touché (sortie au stop, pas au low).
    strategy = BarrierStrategy(SignalAction.ENTER_LONG, stop_loss=0.95, take_profit=1.10)
    frame = _frame_ohlc([(1.01, 0.99, 1.00), (1.02, 0.90, 0.92), (0.95, 0.85, 0.88)])
    result = _run_barrier(strategy, frame)
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 0.95
    assert result.trades[0].pnl == round(-0.05 * size, 5)


def test_barriere_stop_prioritaire_si_deux_touchees() -> None:
    # Bougie 1 traverse SL (0.95) ET TP (1.10) : hypothèse conservatrice = stop.
    strategy = BarrierStrategy(SignalAction.ENTER_LONG, stop_loss=0.95, take_profit=1.10)
    frame = _frame_ohlc([(1.01, 0.99, 1.00), (1.20, 0.90, 1.00)])
    result = _run_barrier(strategy, frame)
    assert result.trades[0].exit_price == 0.95


def test_barriere_take_profit_short() -> None:
    # Short à 1.00 ; TP en dessous (0.90), SL au-dessus (1.05). Bougie 1 → 0.85.
    strategy = BarrierStrategy(SignalAction.ENTER_SHORT, stop_loss=1.05, take_profit=0.90)
    frame = _frame_ohlc([(1.01, 0.99, 1.00), (0.98, 0.85, 0.88), (0.92, 0.80, 0.82)])
    result = _run_barrier(strategy, frame)
    size = get_settings().risk_max_position_size
    assert result.trades[0].exit_price == 0.90
    assert result.trades[0].pnl == round(0.10 * size, 5)  # short 1.00 → 0.90


def test_barriere_ignoree_sur_bougie_entree() -> None:
    # La bougie d'entrée elle-même n'est pas testée : son low (0.80) franchit
    # pourtant le stop, mais l'entrée est prise au close, barrières dès la suivante.
    strategy = BarrierStrategy(SignalAction.ENTER_LONG, stop_loss=0.95, take_profit=1.10)
    frame = _frame_ohlc([(1.30, 0.80, 1.00), (1.05, 0.98, 1.02), (1.06, 0.99, 1.03)])
    result = _run_barrier(strategy, frame)
    # Aucune barrière touchée après l'entrée → liquidation finale à 1.03.
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 1.03


def test_cloture_forcee_fin_de_semaine() -> None:
    # Jeudi/Vendredi/Lundi : un long ouvert jeudi doit être fermé vendredi,
    # jamais porté sur le week-end jusqu'au lundi (1.5).
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-04", tz="UTC"),  # jeudi, semaine ISO 1
            pd.Timestamp("2024-01-05", tz="UTC"),  # vendredi, semaine ISO 1
            pd.Timestamp("2024-01-08", tz="UTC"),  # lundi, semaine ISO 2
        ]
    )
    frame = pd.DataFrame({"bid_close": [1.0, 1.2, 1.5]}, index=index)
    engine = BacktestEngine(
        ScriptedStrategy({0: SignalAction.ENTER_LONG}), RiskManager(get_settings())
    )
    result = asyncio.run(engine.run("EURUSD", frame, "D1"))
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_time == index[1]  # vendredi, pas lundi
    assert trade.exit_price == 1.2
    assert trade.pnl == round(0.2 * size, 5)
