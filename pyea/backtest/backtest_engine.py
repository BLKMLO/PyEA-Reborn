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
- une position à la fois (plafond du RiskManager) ;
- **coûts de transaction** : le spread est MESURÉ dans les données (médiane de
  ``ask_close - bid_close`` — le téléchargeur stocke les deux côtés depuis
  toujours, le moteur ne lisait que le bid) et facturé en coût FIXE par côté,
  si bien qu'un aller-retour paie exactement un spread ; s'y ajoute une
  commission optionnelle (``costs.commission_per_unit``). Modéliser le coût
  ainsi plutôt qu'en décalant les prix garde les barrières remplies à leur prix
  EXACT — ce qui est correct, le flux étant en bid : un long sort bien au bid,
  un short entre bien au bid. **Approximation résiduelle assumée** : pour un
  SHORT, la sortie est un achat à l'ask, donc ses barrières sont franchies au
  bid décalé d'un spread ; le coût total est exact, seul le départage d'un
  franchissement quasi simultané peut différer. Sans colonnes ask, AUCUN coût
  n'est modélisé et ``stats["costs_modelled"]`` vaut False — l'interface le
  signale au lieu d'afficher un zéro trompeur.

Détails d'implémentation :
- le backtest tourne sur un **capital de départ réel** (``backtest.initial_capital``)
  et trade la **taille réelle** (``risk.max_position_size``, émise par le
  RiskManager) : le P&L backtrader et ``broker.getvalue()`` sont directement les
  montants du compte, sans re-scaling post-hoc. La courbe d'équité est la
  VALEUR DU COMPTE (elle part du capital initial) ;
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


def measure_spread(frame: pd.DataFrame) -> float | None:
    """Spread MOYEN observé dans les données (``ask_close - bid_close``).

    Le téléchargeur Dukascopy stocke les deux côtés depuis toujours ; jusqu'ici
    le moteur n'utilisait que le bid, donc les backtests achetaient et
    revendaient au même prix — un aller-retour gratuit, qui n'existe pas.

    Retourne ``None`` si le frame n'a pas de colonne ask (frames de test,
    historiques bid seul) : dans ce cas AUCUN spread n'est modélisé et le
    résultat le signale explicitement (``costs_modelled``), plutôt que de
    deviner une valeur plausible.

    On retient la MÉDIANE : le spread s'élargit brutalement à l'ouverture, sur
    les annonces et le week-end ; une moyenne serait tirée par ces pointes et
    surestimerait le coût du régime normal.
    """
    if "ask_close" not in frame.columns or "bid_close" not in frame.columns:
        return None
    spread = (frame["ask_close"] - frame["bid_close"]).dropna()
    spread = spread[spread > 0]
    if spread.empty:
        return None
    return float(spread.median())


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
    pnl: float          # NET de spread et commission — le résultat réel
    gross_pnl: float = 0.0   # avant coûts (diagnostic)
    cost: float = 0.0        # spread + commission payés sur l'aller-retour


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
        self._entry_size: float | None = None    # taille RÉELLE tradée
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
        gross = trade.pnl          # avant coûts
        net = trade.pnlcomm        # après spread + commission — c'est le vrai
        if self._pending_exit is not None:      # clôture forcée : temps/prix connus
            exit_time, exit_price = self._pending_exit
        else:                                   # barrière : prix reconstruit du P&L
            bar = min(len(self.data) - 1, self.p.n - 1)
            exit_time = self.p.index[bar]
            # Reconstruction à partir du P&L BRUT : les coûts ne déplacent pas
            # le prix auquel la barrière a été touchée. Le P&L étant désormais
            # celui de la taille RÉELLE, on le ramène à l'unité.
            unit_pnl = gross / self._entry_size
            exit_price = entry_price + unit_pnl if side == "long" else entry_price - unit_pnl
        self.trades.append({
            "side": "BUY" if side == "long" else "SELL",
            "quantity": self._entry_size,
            "entry_time": self._entry_time, "entry_price": entry_price,
            "exit_time": exit_time, "exit_price": round(exit_price, 5),
            "pnl": round(net, 5), "gross_pnl": round(gross, 5),
            "cost": round(gross - net, 5),
        })
        self._open_side = None
        self._entry_time = self._entry_price = self._pending_exit = None
        self._entry_size = None

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
        # La taille tradée est la taille RÉELLE validée par le RiskManager
        # (``risk.max_position_size``) : le P&L backtrader est directement le
        # montant du compte, sans re-scaling après coup.
        side = "long" if order.side.value == "BUY" else "short"
        self._open_side = side
        self._entry_time = ts
        self._entry_price = price
        self._entry_size = float(order.quantity)
        open_fn = self.buy if side == "long" else self.sell
        barrier_fn = self.sell if side == "long" else self.buy
        open_fn(size=order.quantity, exectype=bt.Order.Market)
        stop_order = None
        if order.stop_loss is not None:
            stop_order = barrier_fn(size=order.quantity, exectype=bt.Order.Stop, price=order.stop_loss)
        if order.take_profit is not None:
            barrier_fn(
                size=order.quantity, exectype=bt.Order.Limit, price=order.take_profit,
                oco=stop_order,  # OCO si un stop existe ; sinon Limit seul.
            )


class BacktestEngine:
    """Simule la boucle de trading sur un historique via backtrader."""

    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        commission_per_unit: float = 0.0,
        initial_capital: float = 10000.0,
    ) -> None:
        self._strategy = strategy
        self._risk = risk_manager
        # Capital de départ du compte simulé : la courbe d'équité est la
        # VALEUR DU COMPTE (elle part de ce capital).
        self._initial_capital = float(initial_capital)
        # Commission éventuelle du courtier, PAR CÔTÉ et par unité, en unités
        # de prix (même échelle que le spread, pour se composer avec le P&L).
        self._commission = float(commission_per_unit)

    def run(
        self,
        symbol: str,
        frame: pd.DataFrame,
        timeframe: str,
        context: pd.DataFrame | None = None,
        model_path: str | None = None,
    ) -> BacktestResult:
        """Exécute le backtest (synchrone : backtrader l'est ; les méthodes
        asynchrones de la stratégie sont pontées sur une boucle dédiée).

        ``context`` = bougies ANTÉRIEures à ``frame``, fournies à la chauffe de
        la stratégie mais **jamais rejouées** : aucune décision n'y est prise,
        aucun ordre, et elles n'entrent ni dans les statistiques ni dans la
        courbe d'équité. Elles servent uniquement à ce que les indicateurs
        soient déjà chauds à la PREMIÈRE bougie de ``frame``.

        Sans contexte, les premières bougies de chaque bloc (~60 barres de
        chauffe, davantage pour les indicateurs récursifs) donnent des features
        NaN, donc zéro trade possible — un pli out-of-sample perdait ainsi le
        début de sa période. Le contexte est strictement causal : ce sont des
        bougies passées, antérieures au bloc évalué, jamais des bougies futures.

        ``model_path`` = artefact de modèle à charger (page backtest : dernier
        run entraîné de la paire). Transmis tel quel à ``Strategy.warmup`` ;
        ``None`` = stratégie non entraînée (muette si elle exige un modèle).
        """
        n = len(frame)
        result = BacktestResult(symbol=symbol, timeframe=timeframe, bars=n)
        if n == 0:
            result.stats = _empty_stats(0, self._initial_capital)
            return result

        warmup_frame = frame
        if context is not None and not context.empty:
            warmup_frame = pd.concat([context, frame])
            warmup_frame = warmup_frame[~warmup_frame.index.duplicated(keep="last")]

        # Coûts de transaction : spread RÉEL mesuré dans les données + éventuelle
        # commission. Sans eux, un aller-retour est gratuit — ce qui n'existe pas.
        spread = measure_spread(frame)
        cost_per_side = (0.0 if spread is None else spread / 2.0) + self._commission

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._strategy.warmup(
                {
                    "symbol": symbol, "timeframe": timeframe,
                    "frame": warmup_frame, "model_path": model_path,
                }
            ))
            strat = self._run_cerebro(symbol, frame, timeframe, loop, cost_per_side)
        finally:
            try:
                loop.run_until_complete(self._strategy.shutdown())
            finally:
                loop.close()

        self._collect(result, strat, spread)
        logger.info(
            "Backtest %s %s : %d bougies, %d trades, P&L net %.5f "
            "(coûts %.5f, spread %s)",
            symbol, timeframe, n, len(result.trades), result.stats["total_pnl"],
            result.stats["total_costs"],
            "non modélisé" if spread is None else f"{spread:.5f}",
        )
        return result

    # -- interne ------------------------------------------------------------
    def _run_cerebro(
        self, symbol, frame, timeframe, loop, cost_per_side: float
    ) -> _StrategyBridge:
        feed = _to_backtrader_feed(frame)
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.set_coc(True)           # entrée/clôture au close de décision
        cerebro.broker.setcash(self._initial_capital)
        # Coût FIXE par unité et par côté : un aller-retour paie donc exactement
        # un spread (deux demi-spreads) plus deux commissions. Modéliser le coût
        # ainsi — plutôt qu'en décalant les prix — garde les barrières remplies
        # à leur prix EXACT, ce qui est correct : le flux est en bid, or un long
        # sort bien au bid (et un short entre bien au bid).
        if cost_per_side > 0:
            cerebro.broker.setcommission(
                commission=cost_per_side,
                commtype=bt.CommInfoBase.COMM_FIXED,
                stocklike=True,
            )
        cerebro.adddata(feed)
        cerebro.addstrategy(
            _StrategyBridge,
            pyea_strategy=self._strategy, risk=self._risk, symbol=symbol, loop=loop,
            index=list(frame.index), closes=frame["bid_close"].to_numpy(),
            last_of_week=_last_bars_of_week(frame.index), n=len(frame),
        )
        # riskfreerate=0 : le compte ne trade qu'une position à la fois et le
        # cash dormant n'est pas rémunéré dans la simulation ; un taux sans
        # risque non nul ajouterait un rendement fictif au numérateur. À 0,
        # Sharpe = μ/σ des rendements quotidiens de la valeur du compte.
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                            timeframe=bt.TimeFrame.Days, riskfreerate=0.0,
                            annualize=True)
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
        return cerebro.run()[0]

    def _collect(
        self, result: BacktestResult, strat: _StrategyBridge, spread: float | None
    ) -> None:
        # Les montants backtrader sont DÉJÀ ceux du compte (taille réelle
        # tradée, capital réel) : aucun re-scaling post-hoc.
        result.trades = [
            BacktestTrade(
                symbol=result.symbol, side=t["side"], quantity=t["quantity"],
                entry_time=t["entry_time"], entry_price=t["entry_price"],
                exit_time=t["exit_time"], exit_price=t["exit_price"],
                pnl=t["pnl"], gross_pnl=t["gross_pnl"], cost=t["cost"],
            )
            for t in strat.trades
        ]
        result.equity_curve = _downsample_equity(strat.equity, result.bars)
        result.stats = _build_stats(
            result, strat, self._initial_capital, spread, self._commission
        )


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
    equity: list[tuple[datetime, float]], bars: int
) -> list[tuple[datetime, float]]:
    """Ramène la courbe (VALEUR DU COMPTE, part du capital initial) à ≤ 500 points."""
    points = [(ts, round(value, 5)) for ts, value in equity]
    step = max(1, bars // MAX_EQUITY_POINTS)
    keep = [p for idx, p in enumerate(points) if idx % step == 0]
    if points and points[-1] not in keep:
        keep.append(points[-1])
    return keep


def _max_drawdown(equity_curve: list[tuple[datetime, float]]) -> tuple[float, float]:
    """Drawdown maximal sur la courbe de valeur du compte.

    Retourne ``(montant_absolu, ratio_par_rapport_au_pic)`` : le montant se lit
    en devise du compte, le ratio en fraction du pic (0,12 = -12 % depuis le
    plus haut). C'est la métrique « combien le compte a perdu depuis son plus
    haut », dans les deux langages (absolu et relatif).
    """
    peak, max_dd, max_dd_pct = float("-inf"), 0.0, 0.0
    for _, value in equity_curve:
        peak = max(peak, value)
        dd = peak - value
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak)
    return round(max_dd, 5), round(max_dd_pct, 5)


def _empty_stats(bars: int, initial_capital: float) -> dict[str, Any]:
    return {
        "bars": bars, "trades": 0, "total_pnl": 0.0, "win_rate": None,
        "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
        "sharpe_ratio": None, "sqn": None,
        "profit_factor": None, "avg_trade_pnl": None,
        "best_trade": None, "worst_trade": None,
        "gross_pnl": 0.0, "total_costs": 0.0,
        "costs_modelled": False, "spread": None, "commission_per_unit": 0.0,
        "initial_capital": initial_capital, "final_equity": initial_capital,
        "return_pct": 0.0,
    }


def _build_stats(
    result: BacktestResult,
    strat: _StrategyBridge,
    initial_capital: float,
    spread: float | None,
    commission: float,
) -> dict[str, Any]:
    pnls = [t.pnl for t in result.trades]  # NETS : toutes les stats sont nettes
    wins = [p for p in pnls if p > 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    total_pnl = round(sum(pnls), 5)
    max_dd, max_dd_pct = _max_drawdown(result.equity_curve)
    final_equity = (
        result.equity_curve[-1][1] if result.equity_curve else initial_capital
    )

    # Sharpe (riskfreerate=0) et SQN restent invariants d'échelle → exploitables.
    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
    sqn = strat.analyzers.sqn.get_analysis().get("sqn")

    return {
        "bars": result.bars,
        "trades": len(pnls),
        "total_pnl": total_pnl,  # net de coûts, en devise du compte
        "gross_pnl": round(sum(t.gross_pnl for t in result.trades), 5),
        "total_costs": round(sum(t.cost for t in result.trades), 5),
        # False = les données n'avaient pas de colonnes ask : AUCUN spread n'a
        # été modélisé, et l'interface doit le dire (résultat optimiste).
        "costs_modelled": spread is not None or commission > 0,
        "spread": None if spread is None else round(spread, 6),
        "commission_per_unit": commission,
        # --- métriques de compte (rapportées au capital de départ) ---
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 5),
        "return_pct": round(total_pnl / initial_capital, 5),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "sqn": round(sqn, 4) if sqn else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "avg_trade_pnl": round(sum(pnls) / len(pnls), 5) if pnls else None,
        "best_trade": round(max(pnls), 5) if pnls else None,
        "worst_trade": round(min(pnls), 5) if pnls else None,
    }
