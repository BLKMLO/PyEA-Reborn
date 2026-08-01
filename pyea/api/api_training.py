"""Routes REST de l'entraînement walk-forward (/api/training/*).

Le run part en job d'arrière-plan (thread) et retourne immédiatement un
``job_id`` : la progression arrive en temps réel par le WebSocket (topic
``training.progress``) et reste interrogeable par polling sur
``GET /api/training/jobs/{id}``.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger
from pyea.data.data_history_downloader import load_history, resample_history
from pyea.risk.risk_manager import RiskManager
from pyea.storage.storage_training_runs import (
    POOLED_RUN_SYMBOL, create_run, delete_run, finish_run, list_runs, make_run_id,
)
from pyea.strategies.strategy_registry import get_strategy
from pyea.training import job_manager, run_walkforward, run_walkforward_pooled

logger = get_logger(__name__)
router = APIRouter(prefix="/api/training", tags=["training"])


class TrainingRunRequest(BaseModel):
    # ``symbols`` = liste d'actifs (poolé si plusieurs) ; ``None`` = TOUS
    # les actifs ayant un historique local — le cas voulu pour un modèle
    # unique (couleuvre_v0_2).
    symbols: list[str] | None = None
    timeframe: str = "H1"
    strategy: str = "couleuvre_v0_2"
    folds: int = Field(default=4, ge=1, le=20)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _periode_coherente(self) -> "TrainingRunRequest":
        if self.start and self.end and self.start > self.end:
            raise ValueError(
                f"Période invalide : début ({self.start}) postérieur à la fin ({self.end})."
            )
        return self


def _symbols_with_history(data_dir: Path) -> list[str]:
    """Actifs ayant au moins un fichier d'historique M1 local."""
    if not data_dir.is_dir():
        return []
    return sorted(
        symbol_dir.name
        for symbol_dir in data_dir.iterdir()
        if symbol_dir.is_dir()
        and list(symbol_dir.glob(f"{symbol_dir.name}_m1_*.parquet"))
    )


@router.post("/run")
async def start_training(request: TrainingRunRequest) -> dict[str, Any]:
    """Valide les paramètres puis lance le job — qui commence par CHARGER
    les données. Le chargement (des secondes, voire des minutes de M1 sur
    disque lent) vit dans le thread du job, pas dans cette requête : le
    POST répond immédiatement avec un ``job_id``, la phase « chargement »
    est une progression visible, et un rechargement de page en plein
    chargement retrouve le run via /current-job. Une période invalide ou
    trop courte fait échouer le job avec un message clair."""
    settings = get_settings()
    if job_manager.has_running_job():
        raise HTTPException(status_code=409, detail="Un entraînement est déjà en cours.")

    try:
        strategy_cls = get_strategy(request.strategy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Cibles : la liste demandée, ou TOUS les actifs ayant un historique.
    # Seule vérification synchrone sur les données : l'historique local existe
    # (erreur immédiate et actionnable, sans rien charger).
    data_dir = Path(settings.history_data_dir)
    symbols = request.symbols or _symbols_with_history(data_dir)
    if not symbols:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun historique dans {data_dir} — "
                   "lancer `python download_history.py` d'abord.",
        )
    missing = [
        s for s in symbols
        if not list((data_dir / s).glob(f"{s}_m1_*.parquet"))
    ]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun historique pour {', '.join(missing)} dans {data_dir} — "
                   "lancer `python download_history.py` d'abord.",
        )
    pooled = len(symbols) > 1
    if pooled and not strategy_cls().model_definition().get("pooled"):
        raise HTTPException(
            status_code=400,
            detail=f"{request.strategy} s'entraîne par actif : passer un seul "
                   "symbole, ou choisir une stratégie mutualisée (couleuvre_v0_2).",
        )

    run_id = make_run_id(request.strategy)
    params = request.model_dump(mode="json")
    params["symbols"] = symbols  # liste RÉSOLUE (les actifs effectivement visés)
    # Un run poolé est enregistré sous la sentinelle ALL : le modèle est unique,
    # il n'appartient à aucun actif en particulier.
    run_symbol = POOLED_RUN_SYMBOL if pooled else symbols[0]
    create_run(run_id, request.strategy, run_symbol, request.timeframe,
               request.folds, params)
    artifacts_dir = Path(settings.models_dir) / run_id
    risk_manager = RiskManager(settings)
    loop = asyncio.get_running_loop()

    def target(progress, cancelled) -> dict[str, Any]:
        try:
            start = pd.Timestamp(request.start, tz="UTC") if request.start else None
            end = pd.Timestamp(request.end, tz="UTC") if request.end else None
            frames: dict[str, pd.DataFrame] = {}
            for symbol in symbols:
                progress({"phase": "load",
                          "message": f"Chargement de l'historique {symbol}…"})
                frame = load_history(data_dir, symbol, start, end)
                frame = resample_history(frame, request.timeframe)
                if len(frame) >= request.folds * 20:
                    frames[symbol] = frame
                else:
                    logger.warning(
                        "Entraînement : %s écarté (historique trop court : %d "
                        "bougies pour %d plis).", symbol, len(frame), request.folds,
                    )
            if not frames or (pooled and len(frames) < 2):
                raise ValueError(
                    "Historique trop court pour "
                    f"{request.folds} plis sur les actifs demandés."
                )
            if pooled:
                report = run_walkforward_pooled(
                    strategy_factory=strategy_cls,
                    risk_manager=risk_manager,
                    frames=frames,
                    timeframe=request.timeframe,
                    n_folds=request.folds,
                    artifacts_dir=artifacts_dir,
                    progress=progress,
                    cancelled=cancelled,
                    commission_per_unit=settings.costs_commission_per_unit,
                    initial_capital=settings.backtest_initial_capital,
                )
            else:
                report = run_walkforward(
                    strategy_factory=strategy_cls,
                    risk_manager=risk_manager,
                    symbol=symbols[0],
                    frame=frames[symbols[0]],
                    timeframe=request.timeframe,
                    n_folds=request.folds,
                    artifacts_dir=artifacts_dir,
                    progress=progress,
                    cancelled=cancelled,
                    commission_per_unit=settings.costs_commission_per_unit,
                    initial_capital=settings.backtest_initial_capital,
                )
        except Exception:
            finish_run(run_id, "failed")
            raise
        status = "cancelled" if report["cancelled"] else "completed"
        finish_run(run_id, status, report["oos_stats"], str(artifacts_dir))
        return {"run_id": run_id, **report}

    job = job_manager.start(target, loop)
    logger.info("Entraînement %s lancé (job %s) : %s %s, %d plis.",
                run_id, job.id, ",".join(symbols), request.timeframe, request.folds)
    return {"job_id": job.id, "run_id": run_id}


@router.get("/current-job")
async def get_current_job() -> dict[str, Any]:
    """Le job en cours, ou ``{"job": null}``. Permet à la page Entraînement
    de se ré-attacher à un run (progression + annulation) après un
    rechargement ou une navigation — sans quoi un run en cours devenait
    invisible et impossible à annuler."""
    job = job_manager.current()
    return {"job": job.to_dict() if job else None}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job inconnu : {job_id}")
    return job.to_dict()


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict[str, Any]:
    if not job_manager.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job introuvable ou déjà terminé.")
    return {"job_id": job_id, "cancelling": True}


@router.get("/runs")
async def get_runs(limit: int = 50) -> dict[str, Any]:
    """Historique des entraînements (récents d'abord), pour comparaison."""
    return {"runs": list_runs(limit)}


@router.delete("/runs/{run_id}")
async def delete_training_run(run_id: str) -> dict[str, Any]:
    """Supprime un run : ligne SQL + artefacts disque (``models_dir/<run_id>``).

    404 si le run est inconnu ; 409 s'il est encore « running » (son job peut
    encore écrire — annuler l'entraînement d'abord). Supprimer le dernier run
    réussi d'une paire fait automatiquement retomber live/backtest sur le run
    précédent (``resolve_live_model`` re-requête à chaque appel, aucun cache).
    """
    status = delete_run(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Run inconnu : {run_id}")
    if status == "running":
        raise HTTPException(
            status_code=409,
            detail="Run en cours : annuler l'entraînement d'abord.",
        )
    # Garde de sécurité : le run_id vient de l'URL et artifacts_path est
    # relu de la base — on ne rmtree QUE sous models_dir (un chemin
    # bidouillé, ex. « .. », ne doit jamais effacer autre chose).
    base = Path(get_settings().models_dir).resolve()
    run_dir = (base / run_id).resolve()
    if run_dir != base and base in run_dir.parents and run_dir.exists():
        shutil.rmtree(run_dir)
    elif run_dir.exists():
        logger.warning("Artefacts du run %s hors de models_dir (%s) : non supprimés.",
                       run_id, run_dir)
    logger.info("Run %s (%s) supprimé.", run_id, status)
    return {"run_id": run_id, "deleted": True}


@router.get("/definition/{strategy}")
async def get_definition(strategy: str) -> dict[str, Any]:
    """Paramètres figés du modèle (lecture seule, page Entraînement)."""
    try:
        strategy_cls = get_strategy(strategy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"strategy": strategy, "definition": strategy_cls().model_definition()}
