"""Moteur de backtest : rejoue l'historique Parquet à travers le flux
complet Strategy → Signal → RiskManager → OrderRequest (exécution simulée).
"""

from pyea.backtest.backtest_engine import BacktestEngine, BacktestResult

__all__ = ["BacktestEngine", "BacktestResult"]
