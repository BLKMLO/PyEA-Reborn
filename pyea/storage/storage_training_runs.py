"""Persistance des entraînements walk-forward (table ``training_runs``).

Chaque run garde ses paramètres, ses métriques out-of-sample et le chemin
de ses artefacts (``data/models/``) — sans historique comparable, on ne
peut pas savoir si un modèle progresse.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import TrainingRun


def create_run(
    run_id: str,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    folds: int,
    params: dict[str, Any],
) -> None:
    with get_session() as session:
        session.add(
            TrainingRun(
                id=run_id,
                strategy_name=strategy_name,
                symbol=symbol,
                timeframe=timeframe,
                folds=folds,
                params_json=json.dumps(params, default=str),
                status="running",
            )
        )
        session.commit()


def finish_run(
    run_id: str,
    status: str,
    oos_stats: dict[str, Any] | None = None,
    artifacts_path: str | None = None,
) -> None:
    with get_session() as session:
        run = session.get(TrainingRun, run_id)
        if run is None:
            return
        run.status = status
        if oos_stats:
            run.oos_trades = oos_stats.get("trades")
            run.oos_pnl = oos_stats.get("total_pnl")
            run.oos_win_rate = oos_stats.get("win_rate")
            run.oos_max_drawdown = oos_stats.get("max_drawdown")
            run.oos_profit_factor = oos_stats.get("profit_factor")
        run.artifacts_path = artifacts_path
        session.commit()


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Runs les plus récents d'abord, prêts à être sérialisés en JSON."""
    with get_session() as session:
        rows = session.scalars(
            select(TrainingRun).order_by(TrainingRun.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": run.id,
                "created_at": run.created_at.isoformat(),
                "strategy": run.strategy_name,
                "symbol": run.symbol,
                "timeframe": run.timeframe,
                "folds": run.folds,
                "status": run.status,
                "params": json.loads(run.params_json),
                "oos_trades": run.oos_trades,
                "oos_pnl": run.oos_pnl,
                "oos_win_rate": run.oos_win_rate,
                "oos_max_drawdown": run.oos_max_drawdown,
                "oos_profit_factor": run.oos_profit_factor,
                "artifacts_path": run.artifacts_path,
            }
            for run in rows
        ]


def fail_orphan_runs() -> int:
    """Marque « failed » les runs restés « running » (serveur arrêté en plein
    entraînement : le thread meurt avec le processus, la ligne ne serait
    jamais mise à jour et resterait « running » pour toujours dans
    l'historique). À appeler au démarrage. Retourne le nombre de runs marqués."""
    with get_session() as session:
        orphans = session.scalars(
            select(TrainingRun).where(TrainingRun.status == "running")
        ).all()
        for run in orphans:
            run.status = "failed"
        session.commit()
        return len(orphans)


def make_run_id(strategy_name: str) -> str:
    """Identifiant lisible et trié chronologiquement : <strategie>-<horodatage>."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{strategy_name[:12]}-{stamp}"
