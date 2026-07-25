"""Équité de RÉFÉRENCE du jour, pour la limite de perte journalière.

La règle « ne plus rien ouvrir après X % de perte sur la journée » a besoin
d'un point de comparaison : l'équité du compte au moment où PyEA a commencé
à trader ce jour-là. Ce repère est **persisté** — sinon un redémarrage du
serveur en milieu de journée repartirait d'une équité déjà entamée, et la
limite laisserait reperdre X % supplémentaires. C'est exactement le genre de
« remise à zéro » silencieuse qu'un logiciel de trading ne doit jamais faire.

Le repère est posé UNE fois par journée UTC, à la première lecture, avec
l'équité réellement rapportée par le broker. Rien n'est estimé par PyEA.
"""

from __future__ import annotations

from datetime import date

from pyea.storage.storage_database import get_session
from pyea.storage.storage_models import DailyEquity


def day_start_equity(day: date, current_equity: float) -> float:
    """Équité de référence de ``day``, posée à ``current_equity`` si absente.

    Retourne toujours la valeur STOCKÉE : le premier appel de la journée fixe
    le repère, les suivants le relisent tel quel (même après un redémarrage).
    """
    with get_session() as session:
        row = session.get(DailyEquity, day)
        if row is None:
            row = DailyEquity(day=day, start_equity=current_equity)
            session.add(row)
            session.commit()
        return row.start_equity
