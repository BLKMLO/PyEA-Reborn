"""Moteur de backtest de PyEA, adossé à **backtrader** (vendorisé dans ``lib/``).

Rejoue un DataFrame d'historique (issu de ``load_history`` + ``resample_history``)
à travers le MÊME flux que le live : ``Strategy → Signal → RiskManager →
OrderRequest``. La différence avec l'ancien moteur maison : **l'exécution et la
comptabilité ne sont plus recalculées à la main**, elles sont déléguées à
backtrader (moteur événementiel éprouvé, GPLv3, pur Python), qui fournit aussi
les métriques standard (Sharpe, SQN, drawdown %, profit factor…).

Modèle d'exécution (fidèle à l'ancien, validé bougie à bougie) :
- **entrée au close de la bougie de décision** — backtrader en mode
  *cheat-on-close* (``broker.set_coc(True)``) : la stratégie décide au close,
  l'ordre Market est rempli à ce même close (comme l'ancien moteur) ;
- **triple-barrier** : ``Signal.stop_loss``/``take_profit`` (via le RiskManager)
  deviennent un ordre **Stop** (SL) et un ordre **Limit** (TP) natifs, liés en
  **OCO** — remplis au PRIX EXACT de la barrière quand le high/low la franchit,
  sur les bougies suivantes. Si les deux sont franchies dans la même bougie,
  backtrader retient le **stop** (convention conservatrice, comme l'ancien) ;
- **clôture forcée de fin de semaine ISO** et **liquidation finale** : ordre
  Market de clôture (jamais de portage week-end, position résiduelle liquidée) ;
- une position à la fois (plafond du RiskManager).

Détails d'implémentation :
- on ne trade qu'**1 unité** nominale dans backtrader ; le P&L linéaire est
  re-scalé par ``risk.max_position_size`` (Sharpe/SQN/drawdown % sont invariants
  d'échelle, seuls les montants absolus sont mis à l'échelle) ;
- ``Open`` synthétisé = close précédent borné dans [low, high] : PyEA modélise un
  marché continu (close-à-close, sans gap), ce qui reproduit exactement les
  barrières « au prix exact » et évite de fausses ouvertures en gap. Frames sans
  high/low (tests) : high=low=close → barrières évaluées sur le close (neutre) ;
- une **bougie « fantôme »** (copie de la dernière) est ajoutée au flux
  backtrader : sous cheat-on-close, un ordre de clôture émis à la toute dernière
  bougie a besoin d'une bougie suivante pour se réaliser (elle n'influe sur
  aucune décision et n'est jamais renvoyée dans les résultats).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncio

import pandas as pd

import backtrader as bt  # vendorisé : pyea/__init__.py préfixe lib/ dans sys.path

from pyea.core.core_domain import OrderRequest, Position, Signal, TickData
from pyea.core.core_logging import get_logger
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy

logger = get_logger(__name__)

MAX_EQUITY_POINTS = 500       # Taille max de la courbe renvoyée à l'interface.
_NOMINAL_CASH = 1_000_000.0   # Capital nominal backtrader (on ne trade qu'1 unité).


def _last_bars_of_week(index: pd.DatetimeIndex) -> list[bool]:
    """Marque, pour chaque bougie, si elle est la dernière de sa semaine ISO.

    Le forex Dukascopy n'a pas de bougie le week-end : une semaine se termine
    quand la bougie suivante bascule sur une autre (année, semaine) ISO — robuste
    aux frontières d'année. La dernière bougie de la série reste ``False`` : la
    liquidation finale la couvre déjà (pas de double clôture).
    """
    n = len(index)
    if n == 0:
        return []
    iso = index.isocalendar()
    keys = list(zip(iso["year"].to_numpy(), iso["week"].to_numpy()))
    flags = [False] * n
    for i in range(n - 1):
        if keys[i] != keys[i + 1]:
            flags[i] = True
    return flags


@dataclass(frozen=True)
class BacktestTrade:
    """Un aller-retour complet (ouverture puis clôture)."""

    symbol: str
    side: str            # BUY / SELL (sens de l'ouverture)
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl: float


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


class _StrategyBridge(bt.Strategy):
    """Pont backtrader ↔ flux PyEA.

    À chaque bougie : pousse un ``TickData`` à la stratégie PyEA, fait valider le
    ``Signal`` par le RiskManager, puis traduit l'``OrderRequest`` en ordres
    backtrader (Market + Stop/Limit OCO). Les méthodes PyEA étant asynchrones,
    on les exécute sur une boucle asyncio dédiée (aucune E/S réelle : la stratégie
    ne fait que lire ses probas pré-calculées).
    """

    params = dict(
        pyea_strategy=None, risk=None, symbol="", loop=None,
        index=None, closes=None, last_of_week=None, n=0,
    )

    def __init__(self) -> None:
        self._i = -1
        self._open_side: str | None = None       # "long" / "short" / None
        self._entry_time: datetime | None = None
        self._entry_price: float | None = None
        self._pending_exit: tuple[datetime, float] | None = None  # clôture forcée
        self.trades: list[dict[str, Any]] = []
        self.equity: list[tuple[datetime, float]] = []

    # -- helpers ------------------------------------------------------------
    def _await(self, coro):
        return self.p.loop.run_until_complete(coro)

    def _cancel_live(self) -> None:
        for order in list(self.broker.get_orders_open()):
            self.cancel(order)

    # -- callbacks backtrader ----------------------------------------------
    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed or self._open_side is None:
            return
        side = self._open_side
        entry_price = self._entry_price
        pnl = trade.pnl  # net, correct même sous cheat-on-close
        if self._pending_exit is not None:      # clôture forcée : temps/prix connus
            exit_time, exit_price = self._pending_exit
        else:                                   # barrière : prix reconstruit du P&L
            bar = min(len(self.data) - 1, self.p.n - 1)
            exit_time = self.p.index[bar]
            exit_price = entry_price + pnl if side == "long" else entry_price - pnl
        self.trades.append({
            "side": "BUY" if side == "long" else "SELL",
            "entry_time": self._entry_time, "entry_price": entry_price,
            "exit_time": exit_time, "exit_price": round(exit_price, 5),
            "pnl": round(pnl, 5),
        })
        self._open_side = None
        self._entry_time = self._entry_price = self._pending_exit = None

    def next(self) -> None:
        self._i += 1
        i, n = self._i, self.p.n
        if i >= n:  # bougie fantôme : réalise les clôtures coc, aucune décision.
            self.equity.append((self.p.index[n - 1], self.broker.getvalue()))
            return

        price = float(self.p.closes[i])
        ts = self.p.index[i]

        # 1) Décision de la stratégie, prise au close.
        tick = TickData(symbol=self.p.symbol, price=price, timestamp=ts)
        signal: Signal | None = self._await(self.p.pyea_strategy.on_tick(tick))
        if signal is not None:
            open_positions = self._open_positions()
            order: OrderRequest | None = self._await(
                self.p.risk.evaluate(signal, open_positions)
            )
            if order is not None:
                self._apply_order(order, ts, price)

        # 2) Clôture forcée : fin de semaine ISO ou dernière bougie (liquidation).
        if (
            self._open_side is not None
            and self.position
            and self._entry_time != ts
            and (self.p.last_of_week[i] or i == n - 1)
        ):
            self._cancel_live()
            self._pending_exit = (ts, price)
            self.close(exectype=bt.Order.Market)

        self.equity.append((ts, self.broker.getvalue()))

    # -- traduction du domaine vers backtrader -----------------------------
    def _open_positions(self) -> list[Position]:
        if self.position.size == 0:
            return []
        return [Position(
            symbol=self.p.symbol,
            quantity=self.position.size,
            average_price=self.position.price,
        )]

    def _apply_order(self, order: OrderRequest, ts: datetime, price: float) -> None:
        # Position ouverte + ordre validé = ordre inverse (EXIT) → on clôture.
        if self.position:
            self._cancel_live()
            self._pending_exit = (ts, price)
            self.close(exectype=bt.Order.Market)
            return
        # Sinon : entrée (le RiskManager bloque les entrées si une position existe).
        side = "long" if order.side.value == "BUY" else "short"
        self._open_side = side
        self._entry_time = ts
        self._entry_price = price
        open_fn = self.buy if side == "long" else self.sell
        barrier_fn = self.sell if side == "long" else self.buy
        open_fn(size=1, exectype=bt.Order.Market)
        stop_order = None
        if order.stop_loss is not None:
            stop_order = barrier_fn(size=1, exectype=bt.Order.Stop, price=order.stop_loss)
        if order.take_profit is not None:
            barrier_fn(
                size=1, exectype=bt.Order.Limit, price=order.take_profit,
                oco=stop_order,  # OCO si un stop existe ; sinon Limit seul.
            )


class BacktestEngine:
    """Simule la boucle de trading sur un historique via backtrader."""

    def __init__(self, strategy: Strategy, risk_manager: RiskManager) -> None:
        self._strategy = strategy
        self._risk = risk_manager
        self._size = float(risk_manager._max_position_size)  # échelle du P&L

    def run(self, symbol: str, frame: pd.DataFrame, timeframe: str) -> BacktestResult:
        """Exécute le backtest (synchrone : backtrader l'est ; les méthodes
        asynchrones de la stratégie sont pontées sur une boucle dédiée)."""
        n = len(frame)
        result = BacktestResult(symbol=symbol, timeframe=timeframe, bars=n)
        if n == 0:
            result.stats = _empty_stats(0)
            return result

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._strategy.warmup(
                {"symbol": symbol, "timeframe": timeframe, "frame": frame}
            ))
            strat = self._run_cerebro(symbol, frame, timeframe, loop)
        finally:
            try:
                loop.run_until_complete(self._strategy.shutdown())
            finally:
                loop.close()

        self._collect(result, strat)
        logger.info(
            "Backtest %s %s : %d bougies, %d trades, P&L %.5f",
            symbol, timeframe, n, len(result.trades), result.stats["total_pnl"],
        )
        return result

    # -- interne ------------------------------------------------------------
    def _run_cerebro(self, symbol, frame, timeframe, loop) -> _StrategyBridge:
        feed = _to_backtrader_feed(frame)
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.set_coc(True)           # entrée/clôture au close de décision
        cerebro.broker.setcash(_NOMINAL_CASH)
        cerebro.adddata(feed)
        cerebro.addstrategy(
            _StrategyBridge,
            pyea_strategy=self._strategy, risk=self._risk, symbol=symbol, loop=loop,
            index=list(frame.index), closes=frame["bid_close"].to_numpy(),
            last_of_week=_last_bars_of_week(frame.index), n=len(frame),
        )
        # riskfreerate=0 : on ne trade qu'1 unité sur un capital nominal élevé,
        # le rendement relatif au cash est ~0 ; un taux sans risque non nul
        # dominerait et rendrait le Sharpe absurde. À 0, Sharpe = μ/σ des
        # rendements, invariant d'échelle et représentatif du flux de P&L.
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                            timeframe=bt.TimeFrame.Days, riskfreerate=0.0,
                            annualize=True)
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
        return cerebro.run()[0]

    def _collect(self, result: BacktestResult, strat: _StrategyBridge) -> None:
        size = self._size
        result.trades = [
            BacktestTrade(
                symbol=result.symbol, side=t["side"], quantity=size,
                entry_time=t["entry_time"], entry_price=t["entry_price"],
                exit_time=t["exit_time"], exit_price=t["exit_price"],
                pnl=round(t["pnl"] * size, 5),
            )
            for t in strat.trades
        ]
        result.equity_curve = _downsample_equity(strat.equity, size, result.bars)
        result.stats = _build_stats(result, strat, size)


# --------------------------------------------------------------------------
# Fonctions utilitaires (données, courbe, statistiques)
# --------------------------------------------------------------------------
def _to_backtrader_feed(frame: pd.DataFrame) -> bt.feeds.PandasData:
    """DataFrame PyEA → feed backtrader (OHLCV) + bougie fantôme finale.

    ``Open`` = close précédent borné dans [low, high] (marché continu, sans gap).
    Sans high/low (tests) : high=low=close.
    """
    close = frame["bid_close"].astype(float)
    high = frame["bid_high"].astype(float) if "bid_high" in frame.columns else close
    low = frame["bid_low"].astype(float) if "bid_low" in frame.columns else close
    prev_close = close.shift(1)
    prev_close.iloc[0] = close.iloc[0]
    open_ = prev_close.clip(lower=low, upper=high)

    data = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 0.0}
    )
    data.index = frame.index.tz_localize(None)  # backtrader veut un index naïf.

    # Bougie fantôme : copie de la dernière (réalise les clôtures cheat-on-close
    # émises à la dernière vraie bougie). Elle n'influe sur aucune décision.
    if len(data) >= 2:
        step = data.index[-1] - data.index[-2]
    else:
        step = pd.Timedelta(minutes=1)
    phantom = data.iloc[[-1]].copy()
    phantom.index = [data.index[-1] + step]
    data = pd.concat([data, phantom])
    return bt.feeds.PandasData(dataname=data)


def _downsample_equity(
    equity: list[tuple[datetime, float]], size: float, bars: int
) -> list[tuple[datetime, float]]:
    """Ramène la courbe (P&L = valeur - capital nominal, re-scalée) à ≤ 500 points."""
    points = [(ts, round((value - _NOMINAL_CASH) * size, 5)) for ts, value in equity]
    step = max(1, bars // MAX_EQUITY_POINTS)
    keep = [p for idx, p in enumerate(points) if idx % step == 0]
    if points and points[-1] not in keep:
        keep.append(points[-1])
    return keep


def _max_drawdown(equity_curve: list[tuple[datetime, float]]) -> float:
    peak, max_dd = float("-inf"), 0.0
    for _, value in equity_curve:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return round(max_dd, 5)


def _empty_stats(bars: int) -> dict[str, Any]:
    return {
        "bars": bars, "trades": 0, "total_pnl": 0.0, "win_rate": None,
        "max_drawdown": 0.0, "sharpe_ratio": None, "sqn": None,
        "profit_factor": None, "avg_trade_pnl": None,
        "best_trade": None, "worst_trade": None,
    }


def _build_stats(
    result: BacktestResult, strat: _StrategyBridge, size: float
) -> dict[str, Any]:
    pnls = [t.pnl for t in result.trades]
    wins = [p for p in pnls if p > 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)

    # Sharpe (riskfreerate=0) et SQN sont invariants d'échelle → exploitables.
    # Le « drawdown % » de backtrader, lui, serait rapporté au capital nominal
    # (1 unité sur 1 M) et donc dénué de sens : on ne garde que le drawdown
    # ABSOLU, calculé sur la courbe d'équité re-scalée (montant réel).
    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
    sqn = strat.analyzers.sqn.get_analysis().get("sqn")

    return {
        "bars": result.bars,
        "trades": len(pnls),
        "total_pnl": round(sum(pnls), 5),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "max_drawdown": _max_drawdown(result.equity_curve),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "sqn": round(sqn, 4) if sqn else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "avg_trade_pnl": round(sum(pnls) / len(pnls), 5) if pnls else None,
        "best_trade": round(max(pnls), 5) if pnls else None,
        "worst_trade": round(min(pnls), 5) if pnls else None,
    }
