"""Routes REST de l'interface de backtest (/api/backtest/*).

Les endpoints d'exécution sont des ``def`` synchrones : FastAPI les fait
tourner dans son threadpool, un backtest long ne bloque donc pas la
boucle d'événements du serveur (dashboard live inclus).
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from pyea.backtest import BacktestEngine
from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger
from pyea.data.data_history_downloader import file_year, load_history, resample_history
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_registry import get_strategy, list_strategies

logger = get_logger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/datasets")
def get_datasets() -> dict[str, Any]:
    """Historique disponible localement : symboles et années couvertes.

    Lit le layout ``data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet``.
    """
    data_dir = Path(get_settings().history_data_dir)
    datasets = []
    if data_dir.is_dir():
        for symbol_dir in sorted(data_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue  # Fichier égaré à la racine du dossier d'historique.
            # file_year ignore les fichiers au suffixe non numérique
            # (copies de sauvegarde manuelles, renommages…).
            years = sorted(
                year
                for file in symbol_dir.glob(f"{symbol_dir.name}_m1_*.parquet")
                if (year := file_year(file)) is not None
            )
            if years:
                datasets.append(
                    {"symbol": symbol_dir.name, "years": years}
                )
    return {
        "datasets": datasets,
        "strategies": list_strategies(),
        "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"],
    }


class BacktestRunRequest(BaseModel):
    symbol: str
    timeframe: str = "H1"
    strategy: str = "couleuvre_v0_1"
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _periode_coherente(self) -> "BacktestRunRequest":
        if self.start and self.end and self.start > self.end:
            raise ValueError(
                f"Période invalide : début ({self.start}) postérieur à la fin ({self.end})."
            )
        return self


@router.post("/run")
def run_backtest(request: BacktestRunRequest) -> dict[str, Any]:
    """Charge l'historique, ré-échantillonne, et rejoue la stratégie."""
    settings = get_settings()
    data_dir = Path(settings.history_data_dir)

    try:
        strategy_cls = get_strategy(request.strategy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        start = pd.Timestamp(request.start, tz="UTC") if request.start else None
        end = pd.Timestamp(request.end, tz="UTC") if request.end else None
        frame = load_history(data_dir, request.symbol, start, end)
        frame = resample_history(frame, request.timeframe)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        # Parquet corrompu, période inversée, timeframe inconnu : erreurs
        # d'utilisateur → 400 avec message actionnable, jamais un 500.
        raise HTTPException(status_code=400, detail=str(exc))
    if frame.empty:
        raise HTTPException(
            status_code=400, detail="Aucune bougie sur la période demandée."
        )

    engine = BacktestEngine(strategy_cls(), RiskManager(settings))
    result = asyncio.run(engine.run(request.symbol, frame, request.timeframe))

    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "strategy": request.strategy,
        "period": {
            "start": frame.index[0].isoformat(),
            "end": frame.index[-1].isoformat(),
        },
        "stats": result.stats,
        "equity_curve": [
            {"time": timestamp.isoformat(), "equity": value}
            for timestamp, value in result.equity_curve
        ],
        "trades": [
            {
                "side": trade.side,
                "quantity": trade.quantity,
                "entry_time": trade.entry_time.isoformat(),
                "entry_price": trade.entry_price,
                "exit_time": trade.exit_time.isoformat(),
                "exit_price": trade.exit_price,
                "pnl": trade.pnl,
            }
            for trade in result.trades[:200]
        ],
    }
