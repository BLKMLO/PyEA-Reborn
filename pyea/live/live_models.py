"""Sélection du modèle Couleuvre à charger en live, par actif.

Pour trader un symbole en live, on charge le modèle du **dernier run
d'entraînement RÉUSSI** le concernant, et dans ce run, le modèle du
**dernier pli** — celui entraîné sur la plus grande fenêtre expansive (le
plus de données). Deux sources, par priorité : le run du symbole (modèle
par actif, couleuvre_v0_1) puis, à défaut, le run **poolé ``ALL``** (modèle
unique multi-actifs, couleuvre_v0_2 — le même artefact sert alors tous les
symboles). Le walk-forward valide (métriques OOS honnêtes) mais entraîne un
modèle par pli sur des tranches croissantes ; le dernier pli est donc le
modèle « le plus mûr » disponible sans étape de ré-entraînement final
dédiée (piste d'amélioration future, notée).

Retourne aussi le **timeframe** du run : l'inférence live doit agréger les
ticks dans la MÊME granularité que l'entraînement (cohérence features).

Aucun run réussi / aucun artefact → ``None`` : la stratégie reste alors
muette en live (honnête — jamais de trade sur un modèle absent).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyea.core.core_logging import get_logger
from pyea.storage.storage_training_runs import POOLED_RUN_SYMBOL, latest_completed_run

logger = get_logger(__name__)


@dataclass(frozen=True)
class LiveModel:
    """Modèle sélectionné pour l'inférence live d'un symbole."""

    symbol: str
    timeframe: str
    model_path: Path
    run_id: str
    fold: int


def resolve_live_model(strategy_name: str, symbol: str) -> LiveModel | None:
    """Modèle live d'un symbole (dernier run réussi, dernier pli disponible).

    Deux sources, par priorité : le run du symbole lui-même (modèle par
    actif, v0_1), puis le run POOLÉ ``ALL`` (modèle unique multi-actifs,
    v0_2) — le même modèle sert alors tous les symboles.
    """
    run = latest_completed_run(strategy_name, symbol)
    if run is None:
        run = latest_completed_run(strategy_name, POOLED_RUN_SYMBOL)
    if run is None or not run.get("artifacts_path"):
        return None
    artifacts = Path(run["artifacts_path"])
    folds = int(run.get("folds") or 0)
    # On parcourt du dernier pli (plus de données) vers le premier : un pli a pu
    # ne pas produire de modèle (jeu trop court → train « trained: False »).
    for fold in range(folds, 0, -1):
        model_path = artifacts / f"fold_{fold}" / "model.txt"
        if model_path.is_file():
            return LiveModel(
                symbol=symbol,
                timeframe=run["timeframe"],
                model_path=model_path,
                run_id=run["id"],
                fold=fold,
            )
    logger.warning(
        "Run %s réussi mais aucun modèle trouvé pour %s (artefacts absents ?).",
        run["id"], symbol,
    )
    return None
