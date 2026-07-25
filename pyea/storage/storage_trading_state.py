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


#: Cache mémoire des états armés. ``is_trading_enabled`` est appelé sur CHAQUE
#: tick (MetaTrader en produit ~4/s par symbole, sur 31 paires) : une session
#: SQLite par appel, c'est une centaine de requêtes bloquantes par seconde dans
#: la boucle asyncio. La base reste la source de vérité — le cache n'est qu'un
#: miroir, rempli à la première lecture et invalidé à chaque écriture.
_cache: dict[str, bool] | None = None


def invalidate_cache() -> None:
    """Vide le cache (à appeler si la base est modifiée hors de ce module)."""
    global _cache
    _cache = None


def _states() -> dict[str, bool]:
    global _cache
    if _cache is None:
        with get_session() as session:
            rows = session.scalars(select(SymbolTradingState)).all()
            _cache = {row.symbol: row.enabled for row in rows}
    return _cache


def get_trading_states() -> dict[str, bool]:
    """Tous les états connus : {symbole: armé ?}."""
    return dict(_states())


def is_trading_enabled(symbol: str) -> bool:
    return _states().get(symbol, False)


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
        stored = state.enabled
    # La base a tranché : le cache reflète ce qui est RÉELLEMENT persisté.
    _states()[symbol] = stored
    return stored
