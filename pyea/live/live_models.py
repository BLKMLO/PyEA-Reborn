"""Sélection du modèle Couleuvre à charger en live, par actif.

**Un modèle par actif** (choix projet) : pour trader un symbole en live, on
charge le modèle du **dernier run d'entraînement RÉUSSI** de ce symbole, et
dans ce run, le modèle du **dernier pli** — celui entraîné sur la plus grande
fenêtre expansive (le plus de données). Le walk-forward valide (métriques OOS
honnêtes) mais entraîne un modèle par pli sur des tranches croissantes ; le
dernier pli est donc le modèle « le plus mûr » disponible sans étape de
ré-entraînement final dédiée (piste d'amélioration future, notée).

Retourne aussi le **timeframe** du run : l'inférence live doit agréger les
ticks dans la MÊME granularité que l'entraînement (cohérence features).

Aucun run réussi / aucun artefact → ``None`` : la stratégie reste alors
muette en live (honnête — jamais de trade sur un modèle absent).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyea.core.core_logging import get_logger
from pyea.storage.storage_training_runs import latest_completed_run

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
    """Modèle live d'un symbole (dernier run réussi, dernier pli disponible)."""
    run = latest_completed_run(strategy_name, symbol)
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
