"""Journal SQL des trades exécutés chez le broker.

La liste des trades affichée au dashboard vient d'ICI (table ``trades``),
pas d'un calcul en mémoire : un trade n'y entre que lorsqu'il a réellement
été exécuté/rempli côté broker (le câblage live appellera ``record_trade``
depuis les callbacks d'exécution de la gateway). Tant qu'aucun broker
n'exécute, la table est vide — et l'affichage l'est aussi, honnêtement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import TradeRecord


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


def record_trade(
    broker_order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    fill_price: float | None,
    status: str = "FILLED",
    pnl: float | None = None,
) -> None:
    """Journalise un trade réellement exécuté chez le broker.

    ``pnl`` = résultat réalisé calculé par le BROKER (sortie de position
    uniquement) ; ``None`` sur une entrée, et jamais estimé par PyEA.
    """
    with get_session() as session:
        session.add(
            TradeRecord(
                broker_order_id=broker_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                fill_price=fill_price,
                pnl=pnl,
                status=status,
            )
        )
        session.commit()


def list_recent_trades(limit: int = 100) -> list[dict[str, Any]]:
    """Trades exécutés, plus récents d'abord, prêts à sérialiser en JSON."""
    with get_session() as session:
        rows = session.scalars(
            select(TradeRecord).order_by(TradeRecord.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "symbol": row.symbol,
                "side": row.side,
                "quantity": row.quantity,
                "fill_price": row.fill_price,
                "pnl": row.pnl,
                "status": row.status,
                "executed_at": _as_utc_iso(row.created_at),
                "broker_order_id": row.broker_order_id,
            }
            for row in rows
        ]
