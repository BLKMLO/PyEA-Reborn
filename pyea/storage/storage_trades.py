"""Journal SQL des trades exécutés chez le broker.

La liste des trades affichée au dashboard vient d'ICI (table ``trades``),
pas d'un calcul en mémoire : un trade n'y entre que lorsqu'il a réellement
été exécuté/rempli côté broker (le câblage live appellera ``record_trade``
depuis les callbacks d'exécution de la gateway). Tant qu'aucun broker
n'exécute, la table est vide — et l'affichage l'est aussi, honnêtement.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import TradeRecord


def record_trade(
    broker_order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    fill_price: float | None,
    status: str = "FILLED",
) -> None:
    """Journalise un trade réellement exécuté chez le broker."""
    with get_session() as session:
        session.add(
            TradeRecord(
                broker_order_id=broker_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                fill_price=fill_price,
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
                "status": row.status,
                "executed_at": row.created_at.isoformat(),
                "broker_order_id": row.broker_order_id,
            }
            for row in rows
        ]
