"""Walk-forward : la SEULE validation honnête d'une stratégie de trading.

Principe (fenêtre expansive, ordre temporel strict, jamais de split
aléatoire) : la seconde moitié de l'historique est découpée en ``n_folds``
blocs de test consécutifs ; le pli i s'entraîne sur TOUT ce qui précède
son bloc de test, puis est backtesté sur ce bloc (out-of-sample).

La métrique qui compte est l'agrégat OUT-OF-SAMPLE (concaténation des
blocs de test) — les métriques in-sample ne servent qu'à diagnostiquer le
surapprentissage.

Le test de chaque pli passe par le MÊME moteur que la page backtest
(``BacktestEngine``) : flux Strategy → Signal → RiskManager → OrderRequest.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from pyea.backtest import BacktestEngine
from pyea.core.core_logging import get_logger
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy

logger = get_logger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


@dataclass
class WalkForwardFold:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    train_report: dict[str, Any] | None = None  # Retour de strategy.train()
    test_stats: dict[str, Any] = field(default_factory=dict)


def split_walkforward(
    frame: pd.DataFrame, n_folds: int, initial_train_fraction: float = 0.5
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Découpe en plis (train expansif, test consécutifs).

    Les blocs de test partagent équitablement la fin de l'historique
    (après ``initial_train_fraction``) ; le train du pli i = tout ce qui
    précède son bloc de test.
    """
    if n_folds < 1:
        raise ValueError("n_folds doit être ≥ 1.")
    first_test_index = int(len(frame) * initial_train_fraction)
    test_span = (len(frame) - first_test_index) // n_folds
    if first_test_index == 0 or test_span == 0:
        raise ValueError(
            f"Historique trop court ({len(frame)} bougies) pour {n_folds} plis."
        )
    folds = []
    for i in range(n_folds):
        test_start = first_test_index + i * test_span
        test_end = test_start + test_span if i < n_folds - 1 else len(frame)
        folds.append((frame.iloc[:test_start], frame.iloc[test_start:test_end]))
    return folds


def run_walkforward(
    strategy_factory: Callable[[], Strategy],
    risk_manager: RiskManager,
    symbol: str,
    frame: pd.DataFrame,
    timeframe: str,
    n_folds: int,
    artifacts_dir: Path,
    progress: ProgressCallback,
    cancelled: CancelCheck,
) -> dict[str, Any]:
    """Exécute le walk-forward complet ; conçu pour tourner dans un thread.

    Retourne le rapport final : plis, stats out-of-sample agrégées,
    courbe d'équité OOS concaténée. Écrit ``metadata.json`` (et, plus
    tard, les modèles retournés par ``strategy.train``) dans
    ``artifacts_dir``.
    """
    folds_frames = split_walkforward(frame, n_folds)
    folds: list[WalkForwardFold] = []
    oos_equity: list[dict[str, Any]] = []
    oos_offset = 0.0

    for i, (train_frame, test_frame) in enumerate(folds_frames):
        if cancelled():
            logger.info("Walk-forward annulé au pli %d/%d.", i + 1, n_folds)
            return _report(symbol, timeframe, folds, oos_equity, cancelled=True)

        fold = WalkForwardFold(
            index=i + 1,
            train_start=train_frame.index[0].isoformat(),
            train_end=train_frame.index[-1].isoformat(),
            test_start=test_frame.index[0].isoformat(),
            test_end=test_frame.index[-1].isoformat(),
            train_bars=len(train_frame),
            test_bars=len(test_frame),
        )

        progress({"fold": i + 1, "total": n_folds, "phase": "train",
                  "message": f"Pli {i + 1}/{n_folds} : entraînement…"})
        strategy = strategy_factory()
        # Chaque pli sauvegarde son modèle (model.txt + features.json) dans
        # un sous-dossier — artefacts inspectables, un modèle par actif/pli.
        fold.train_report = asyncio.run(
            strategy.train(
                train_frame,
                {"fold": i + 1, "model_dir": str(artifacts_dir / f"fold_{i + 1}")},
            )
        )

        # Re-vérifié entre les phases : un pli peut durer des minutes, ne pas
        # attendre le pli suivant pour honorer une annulation.
        if cancelled():
            logger.info("Walk-forward annulé après l'entraînement du pli %d/%d.", i + 1, n_folds)
            folds.append(fold)
            return _report(symbol, timeframe, folds, oos_equity, cancelled=True)

        progress({"fold": i + 1, "total": n_folds, "phase": "test",
                  "message": f"Pli {i + 1}/{n_folds} : backtest out-of-sample…"})
        engine = BacktestEngine(strategy, risk_manager)
        result = engine.run(symbol, test_frame, timeframe)
        fold.test_stats = result.stats

        # Courbe OOS concaténée : chaque pli repart du cumul précédent.
        for timestamp, value in result.equity_curve:
            oos_equity.append(
                {"time": timestamp.isoformat(), "equity": round(oos_offset + value, 5)}
            )
        oos_offset += result.stats["total_pnl"]
        folds.append(fold)

    report = _report(symbol, timeframe, folds, oos_equity, cancelled=False)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "metadata.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report


def _report(
    symbol: str,
    timeframe: str,
    folds: list[WalkForwardFold],
    oos_equity: list[dict[str, Any]],
    cancelled: bool,
) -> dict[str, Any]:
    trades = sum(fold.test_stats.get("trades", 0) for fold in folds)
    total_pnl = round(sum(fold.test_stats.get("total_pnl", 0.0) for fold in folds), 5)
    win_rates = [
        fold.test_stats["win_rate"]
        for fold in folds
        if fold.test_stats.get("win_rate") is not None
    ]
    equity_values = [point["equity"] for point in oos_equity]
    max_drawdown, peak = 0.0, float("-inf")
    for value in equity_values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "cancelled": cancelled,
        "folds": [vars(fold) for fold in folds],
        "oos_stats": {
            "trades": trades,
            "total_pnl": total_pnl,
            "win_rate": round(sum(win_rates) / len(win_rates), 4) if win_rates else None,
            "max_drawdown": round(max_drawdown, 5),
        },
        "oos_equity_curve": oos_equity[:2000],
    }
