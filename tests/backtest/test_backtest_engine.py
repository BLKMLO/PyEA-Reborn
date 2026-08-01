"""Tests du moteur de backtest (adossé à backtrader) avec une stratégie scriptée.

Le moteur délègue l'exécution à backtrader (cheat-on-close + Stop/Limit OCO),
mais reproduit fidèlement le modèle PyEA : entrée au close de décision, barrières
au prix exact, clôture forcée de fin de semaine, liquidation finale. Les valeurs
attendues sont donc les mêmes qu'avec l'ancien moteur maison.
"""

from typing import Any

import numpy as np
import pytest
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
    return engine.run("EURUSD", _frame(closes), "H1")


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
    # La courbe d'équité existe même sans trade : plate à la VALEUR DU COMPTE
    # (le capital initial, pas 0 — c'est une courbe de valeur, pas de P&L).
    capital = result.stats["initial_capital"]
    assert all(value == capital for _, value in result.equity_curve)
    assert result.stats["final_equity"] == capital
    assert result.stats["return_pct"] == 0.0
    assert result.stats["max_drawdown_pct"] == 0.0
    # Les métriques avancées existent (None faute de trade), jamais absentes.
    for key in ("sharpe_ratio", "sqn", "profit_factor"):
        assert key in result.stats and result.stats[key] is None


def test_metriques_avancees_backtrader() -> None:
    # Un gagnant (long +0.5) puis un perdant (short -0.4) : profit factor et
    # moyennes calculés depuis les trades, clés backtrader présentes.
    result = _run(
        {0: SignalAction.ENTER_LONG, 1: SignalAction.EXIT,
         2: SignalAction.ENTER_SHORT, 3: SignalAction.EXIT},
        [1.0, 1.5, 1.5, 1.9, 1.9],
    )
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 2
    stats = result.stats
    assert stats["profit_factor"] == round((0.5 * size) / (0.4 * size), 4)
    assert stats["best_trade"] == round(0.5 * size, 5)
    assert stats["worst_trade"] == round(-0.4 * size, 5)
    assert stats["avg_trade_pnl"] == round((0.5 - 0.4) * size / 2, 5)
    # Clés fournies par les analyzers backtrader (valeur numérique ou None).
    assert "sharpe_ratio" in stats and "sqn" in stats


def test_courbe_equite_bornee() -> None:
    result = _run({}, [1.0] * 3000)
    assert len(result.equity_curve) <= 502  # MAX_EQUITY_POINTS + extrémités


# --- Capital de départ et métriques de compte ------------------------------
# Le backtest tourne sur un capital réel (backtest.initial_capital) : la
# courbe d'équité est la VALEUR DU COMPTE, et les métriques de compte
# (rendement %, drawdown %) s'y rapportent.


def test_courbe_equite_part_du_capital_initial() -> None:
    result = _run({}, [1.0, 1.1, 1.2])
    premier = result.equity_curve[0][1]
    assert premier == pytest.approx(result.stats["initial_capital"])


def test_metriques_de_compte_exactes_sur_aller_retour() -> None:
    # Capital 10 000, achat à 1.0, sortie à 1.5, taille = max_position_size :
    # capital final = 10 000 + 0.5 × taille, rendement = P&L / capital.
    capital = 10000.0
    engine = BacktestEngine(
        ScriptedStrategy({0: SignalAction.ENTER_LONG, 2: SignalAction.EXIT}),
        RiskManager(get_settings()),
        initial_capital=capital,
    )
    result = engine.run("EURUSD", _frame([1.0, 1.2, 1.5, 1.4]), "H1")
    size = get_settings().risk_max_position_size
    stats = result.stats
    assert stats["initial_capital"] == capital
    assert stats["final_equity"] == pytest.approx(capital + 0.5 * size)
    assert stats["return_pct"] == pytest.approx(round(0.5 * size / capital, 5))
    # La quantité tradée est la taille RÉELLE (plus de re-scaling post-hoc).
    assert result.trades[0].quantity == size


def test_drawdown_absolu_et_pourcentage() -> None:
    # Long à 1.0 (bougie 0), le cours monte à 2.0 puis revient à 1.0 (sortie) :
    # pic = capital + 1 × taille, creux = capital → DD = taille, en absolu et
    # en fraction du pic.
    capital = 10000.0
    engine = BacktestEngine(
        ScriptedStrategy({0: SignalAction.ENTER_LONG, 2: SignalAction.EXIT}),
        RiskManager(get_settings()),
        initial_capital=capital,
    )
    result = engine.run("EURUSD", _frame([1.0, 2.0, 1.0, 1.0]), "H1")
    size = get_settings().risk_max_position_size
    pic = capital + 1.0 * size
    assert result.stats["max_drawdown"] == pytest.approx(round(1.0 * size, 5))
    assert result.stats["max_drawdown_pct"] == pytest.approx(
        round(1.0 * size / pic, 5)
    )


def _run_barrier(strategy: BarrierStrategy, frame: pd.DataFrame):
    engine = BacktestEngine(strategy, RiskManager(get_settings()))
    return engine.run("EURUSD", frame, "H1")


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
    result = engine.run("EURUSD", frame, "D1")
    size = get_settings().risk_max_position_size
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_time == index[1]  # vendredi, pas lundi
    assert trade.exit_price == 1.2
    assert trade.pnl == round(0.2 * size, 5)


def test_contexte_de_chauffe_nest_jamais_rejoue() -> None:
    """Le contexte chauffe la stratégie mais ne produit NI trade NI statistique.

    Sans lui, les premières bougies de chaque bloc out-of-sample donnaient des
    features NaN (zéro trade possible) ; avec lui, elles doivent être évaluées
    — mais les bougies de contexte elles-mêmes restent hors du backtest.
    """
    index = pd.date_range("2024-01-01", periods=30, freq="1h", tz="UTC")
    frame = pd.DataFrame({"bid_close": np.linspace(1.0, 1.3, 30)}, index=index)
    contexte = pd.DataFrame(
        {"bid_close": np.linspace(0.7, 1.0, 40)},
        index=pd.date_range("2023-12-29", periods=40, freq="1h", tz="UTC"),
    )
    vues: list[pd.DataFrame] = []

    class _Espion(ScriptedStrategy):
        async def warmup(self, params: dict[str, Any]) -> None:
            vues.append(params["frame"])

    # Entrée à la bougie 2 du bloc évalué, sortie à la 10.
    engine = BacktestEngine(
        _Espion({2: SignalAction.ENTER_LONG, 10: SignalAction.EXIT}),
        RiskManager(get_settings()),
    )
    result = engine.run("EURUSD", frame, "H1", context=contexte)

    # La chauffe voit contexte + bloc évalué...
    assert len(vues[0]) == 70
    # ...mais le backtest ne compte que le bloc évalué.
    assert result.bars == 30
    assert all(t.entry_time >= index[0] for t in result.trades)
    assert all(ts >= index[0] for ts, _ in result.equity_curve)


# --- Coûts de transaction --------------------------------------------------
# Sans eux, un aller-retour est GRATUIT : on achetait et revendait au bid, donc
# le backtest ne payait jamais le spread. Tous les résultats étaient optimistes.

_SPREAD = 0.0002


def _frame_avec_ask(n: int = 12) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    bid = np.linspace(1.0, 1.0 + 0.01 * (n - 1), n)
    return pd.DataFrame({"bid_close": bid, "ask_close": bid + _SPREAD}, index=index)


# Deux allers-retours complets.
_ALLERS_RETOURS = {
    1: SignalAction.ENTER_LONG, 4: SignalAction.EXIT,
    6: SignalAction.ENTER_LONG, 9: SignalAction.EXIT,
}


def test_un_aller_retour_paie_exactement_un_spread() -> None:
    frame = _frame_avec_ask()
    engine = BacktestEngine(
        ScriptedStrategy(dict(_ALLERS_RETOURS)), RiskManager(get_settings())
    )
    result = engine.run("EURUSD", frame, "H1")
    size = get_settings().risk_max_position_size

    assert result.stats["trades"] == 2
    assert result.stats["spread"] == pytest.approx(_SPREAD)
    assert result.stats["costs_modelled"] is True
    # Le coût total vaut exactement 1 spread × 2 allers-retours.
    assert result.stats["total_costs"] == pytest.approx(2 * _SPREAD * size)
    # Et le P&L net est le brut diminué de ces coûts.
    assert result.stats["total_pnl"] == pytest.approx(
        result.stats["gross_pnl"] - result.stats["total_costs"]
    )
    assert all(t.cost == pytest.approx(_SPREAD * size) for t in result.trades)


def test_commission_facturee_par_cote() -> None:
    commission = 0.00005
    engine = BacktestEngine(
        ScriptedStrategy(dict(_ALLERS_RETOURS)),
        RiskManager(get_settings()),
        commission_per_unit=commission,
    )
    result = engine.run("EURUSD", _frame_avec_ask(), "H1")
    size = get_settings().risk_max_position_size
    # Par aller-retour : un spread + DEUX commissions (une à l'entrée, une à
    # la sortie).
    attendu = 2 * (_SPREAD + 2 * commission) * size
    assert result.stats["total_costs"] == pytest.approx(attendu)


def test_sans_colonne_ask_aucun_cout_invente() -> None:
    """Pas de données ask → aucun spread modélisé, et le résultat le DIT.

    Deviner un spread « plausible » serait pire que de ne rien modéliser :
    l'utilisateur doit savoir que le chiffre affiché est optimiste."""
    frame = _frame_avec_ask()[["bid_close"]]
    engine = BacktestEngine(
        ScriptedStrategy(dict(_ALLERS_RETOURS)), RiskManager(get_settings())
    )
    result = engine.run("EURUSD", frame, "H1")
    assert result.stats["spread"] is None
    assert result.stats["costs_modelled"] is False
    assert result.stats["total_costs"] == 0.0
    assert result.stats["total_pnl"] == result.stats["gross_pnl"]


def test_spread_median_ignore_les_pointes() -> None:
    """Le spread s'élargit brutalement à l'ouverture et sur les annonces : la
    médiane décrit le régime normal, la moyenne serait tirée par les pointes."""
    from pyea.backtest.backtest_engine import measure_spread

    index = pd.date_range("2024-01-01", periods=11, freq="1h", tz="UTC")
    bid = np.full(11, 1.0)
    ask = bid + _SPREAD
    ask[-1] = bid[-1] + 0.01  # pointe de spread (×50)
    frame = pd.DataFrame({"bid_close": bid, "ask_close": ask}, index=index)
    assert measure_spread(frame) == pytest.approx(_SPREAD)
