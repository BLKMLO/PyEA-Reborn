"""Routes des pages HTML (templates Jinja2 + HTMX).

La variable de contexte ``page`` alimente le sélecteur Live/Backtest du
header (base.html).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "web" / "templates"

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {"page": "live"})


@router.get("/backtest", response_class=HTMLResponse)
async def backtest(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "backtest.html", {"page": "backtest"})


@router.get("/training", response_class=HTMLResponse)
async def training(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "training.html", {"page": "training"})
