"""Lecture/écriture de l'interrupteur de trading par symbole.

Source de vérité du bouton Trading/Stopped du dashboard. Règles :
- une paire inconnue de la table est ARRÊTÉE (défaut sûr : rien ne trade
  tant que l'utilisateur n'a pas explicitement armé la paire) ;
- l'état survit aux redémarrages (SQLite).

Le futur câblage réel (stratégie/feed) lira ces états pour décider quels
symboles alimenter — combiné au kill-switch global ``strategy.enabled``.
"""

from __future__ import annotations

from sqlalchemy import select

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import SymbolTradingState


def get_trading_states() -> dict[str, bool]:
    """Tous les états connus : {symbole: armé ?}."""
    with get_session() as session:
        rows = session.scalars(select(SymbolTradingState)).all()
        return {row.symbol: row.enabled for row in rows}


def is_trading_enabled(symbol: str) -> bool:
    with get_session() as session:
        state = session.get(SymbolTradingState, symbol)
        return bool(state and state.enabled)


def set_trading_enabled(symbol: str, enabled: bool) -> bool:
    """Arme ou arrête une paire ; retourne l'état effectivement stocké."""
    with get_session() as session:
        state = session.get(SymbolTradingState, symbol)
        if state is None:
            state = SymbolTradingState(symbol=symbol, enabled=enabled)
            session.add(state)
        else:
            state.enabled = enabled
        session.commit()
        return state.enabled
