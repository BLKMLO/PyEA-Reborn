"""Persistance des entraînements walk-forward (table ``training_runs``).

Chaque run garde ses paramètres, ses métriques out-of-sample et le chemin
de ses artefacts (``data/models/``) — sans historique comparable, on ne
peut pas savoir si un modèle progresse.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import TrainingRun

# Départage stable quand deux runs partagent le MÊME created_at : l'horloge
# Windows a une résolution de ~15 ms, deux insertions rapides (tests, retries)
# tombent dans le même tick et un ORDER BY created_at seul devient
# indéterminé (ordre arbitraire → test flaky, mauvais « dernier run » en
# production). ``rowid`` = ordre d'insertion SQLite (spécifique SQLite, comme
# ``_add_missing_columns`` — à revoir lors d'une migration Postgres).
_ROWID_DESC = text("rowid DESC")

#: Symbole sentinelle des runs POOLÉS (modèle unique multi-actifs) : la
#: colonne ``symbol`` reste non nulle, un run mutualisé y est enregistré
#: sous ``ALL`` — ``latest_completed_run(..., "ALL")`` le retrouve tel quel.
POOLED_RUN_SYMBOL = "ALL"


def _as_utc_iso(moment: datetime) -> str:
    """ISO 8601 avec fuseau EXPLICITE (``...+00:00``).

    SQLite ne stocke pas le fuseau : SQLAlchemy relit un datetime NAÏF même
    pour une colonne ``DateTime(timezone=True)``. Sérialisé tel quel, le
    navigateur l'interprétait en heure LOCALE — un trade de 23 h 30 UTC
    s'affichait au lendemain pour un utilisateur en UTC+2. On réattache donc
    l'UTC dans lequel la valeur a été écrite (cf. ``_utcnow`` des modèles).
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat()


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
            select(TrainingRun)
            .order_by(TrainingRun.created_at.desc(), _ROWID_DESC)
            .limit(limit)
        ).all()
        return [
            {
                "id": run.id,
                "created_at": _as_utc_iso(run.created_at),
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


def latest_completed_run(strategy_name: str, symbol: str) -> dict[str, Any] | None:
    """Dernier run RÉUSSI (``completed``) pour une paire donnée, ou ``None``.

    Sert à sélectionner le modèle de l'inférence live : un modèle par actif,
    le plus récent qui soit allé au bout de son walk-forward. Les runs
    ``running``/``failed``/``cancelled`` sont ignorés (pas d'artefacts fiables).
    """
    with get_session() as session:
        run = session.scalars(
            select(TrainingRun)
            .where(
                TrainingRun.strategy_name == strategy_name,
                TrainingRun.symbol == symbol,
                TrainingRun.status == "completed",
            )
            .order_by(TrainingRun.created_at.desc(), _ROWID_DESC)
            .limit(1)
        ).first()
        if run is None:
            return None
        return {
            "id": run.id,
            "symbol": run.symbol,
            "timeframe": run.timeframe,
            "folds": run.folds,
            "artifacts_path": run.artifacts_path,
        }


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


def delete_run(run_id: str) -> str | None:
    """Supprime la ligne d'un run terminé. Retourne son statut :

    - ``None`` si le run est inconnu (l'API répond 404) ;
    - ``"running"`` si le run est encore en cours — la ligne N'EST PAS
      supprimée (son job peut encore écrire ses artefacts ; l'API répond
      409 et l'utilisateur annule d'abord l'entraînement) ;
    - le statut du run supprimé sinon.

    La suppression des artefacts disque (``data/models/<run_id>``) relève de
    la couche API, qui connaît ``models_dir``.
    """
    with get_session() as session:
        run = session.get(TrainingRun, run_id)
        if run is None:
            return None
        status = run.status
        if status != "running":
            session.delete(run)
            session.commit()
        return status


def make_run_id(strategy_name: str) -> str:
    """Identifiant lisible et trié chronologiquement : <strategie>-<horodatage>."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{strategy_name[:12]}-{stamp}"
