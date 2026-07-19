"""Routes REST de l'entraînement walk-forward (/api/training/*).

Le run part en job d'arrière-plan (thread) et retourne immédiatement un
``job_id`` : la progression arrive en temps réel par le WebSocket (topic
``training.progress``) et reste interrogeable par polling sur
``GET /api/training/jobs/{id}``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger
from pyea.data.data_history_downloader import load_history, resample_history
from pyea.risk.risk_manager import RiskManager
from pyea.storage.storage_training_runs import create_run, finish_run, list_runs, make_run_id
from pyea.strategies.strategy_registry import get_strategy
from pyea.training import job_manager, run_walkforward

logger = get_logger(__name__)
router = APIRouter(prefix="/api/training", tags=["training"])


class TrainingRunRequest(BaseModel):
    symbol: str
    timeframe: str = "H1"
    strategy: str = "couleuvre_v0_1"
    folds: int = Field(default=4, ge=1, le=20)
    start: date | None = None
    end: date | None = None


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
    # Seule vérification synchrone sur les données : le symbole a un
    # historique local (erreur immédiate et actionnable, sans rien charger).
    data_dir = Path(settings.history_data_dir)
    if not list((data_dir / request.symbol).glob(f"{request.symbol}_m1_*.parquet")):
        raise HTTPException(
            status_code=404,
            detail=f"Aucun historique pour {request.symbol} dans {data_dir} — "
                   "lancer `python download_history.py` d'abord.",
        )

    run_id = make_run_id(request.strategy)
    params = request.model_dump(mode="json")
    create_run(run_id, request.strategy, request.symbol, request.timeframe,
               request.folds, params)
    artifacts_dir = Path(settings.models_dir) / run_id
    risk_manager = RiskManager(settings)
    loop = asyncio.get_running_loop()

    def target(progress, cancelled) -> dict[str, Any]:
        try:
            progress({"phase": "load", "message": "Chargement de l'historique…"})
            start = pd.Timestamp(request.start, tz="UTC") if request.start else None
            end = pd.Timestamp(request.end, tz="UTC") if request.end else None
            frame = load_history(data_dir, request.symbol, start, end)
            frame = resample_history(frame, request.timeframe)
            if len(frame) < request.folds * 20:
                raise ValueError(
                    f"Historique trop court ({len(frame)} bougies) pour "
                    f"{request.folds} plis."
                )
            report = run_walkforward(
                strategy_factory=strategy_cls,
                risk_manager=risk_manager,
                symbol=request.symbol,
                frame=frame,
                timeframe=request.timeframe,
                n_folds=request.folds,
                artifacts_dir=artifacts_dir,
                progress=progress,
                cancelled=cancelled,
            )
        except Exception:
            finish_run(run_id, "failed")
            raise
        status = "cancelled" if report["cancelled"] else "completed"
        finish_run(run_id, status, report["oos_stats"], str(artifacts_dir))
        return {"run_id": run_id, **report}

    job = job_manager.start(target, loop)
    logger.info("Entraînement %s lancé (job %s) : %s %s, %d plis.",
                run_id, job.id, request.symbol, request.timeframe, request.folds)
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


@router.get("/definition/{strategy}")
async def get_definition(strategy: str) -> dict[str, Any]:
    """Paramètres figés du modèle (lecture seule, page Entraînement)."""
    try:
        strategy_cls = get_strategy(strategy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"strategy": strategy, "definition": strategy_cls().model_definition()}
