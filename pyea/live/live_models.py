"""Sélection du modèle Couleuvre à charger en live, par actif.

Pour trader un symbole en live, on charge le modèle du **dernier run
d'entraînement RÉUSSI** le concernant, et dans ce run, le modèle du
**dernier pli** — celui entraîné sur la plus grande fenêtre expansive (le
plus de données). Deux sources, par priorité : le run du symbole (modèle
par actif, couleuvre_v0_1) puis, à défaut, le run **poolé ``ALL``** (modèle
unique multi-actifs, couleuvre_v0_2 — le même artefact sert alors tous les
symboles **qu'il a effectivement vus à l'entraînement**, et eux seuls). Le
walk-forward valide (métriques OOS honnêtes) mais entraîne un
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
    v0_2) — mais UNIQUEMENT si ce run a réellement été entraîné sur le
    symbole demandé (``trained_symbols``). Un modèle mutualisé ne couvre que
    les actifs qu'il a vus : le servir aux autres reviendrait à trader une
    paire sur laquelle il n'a aucune donnée.
    """
    run = latest_completed_run(strategy_name, symbol)
    if run is None:
        # Repli poolé — sous CONDITION que le run ait vraiment vu ce symbole.
        # Sans cette vérification, une paire téléchargée APRÈS l'entraînement
        # recevait le modèle mutualisé : la catégorie « symbole » lui vaut le
        # code -1 (inconnue), et le modèle émettait de vrais ordres sur un
        # actif dont il n'a jamais lu une seule bougie.
        run = latest_completed_run(strategy_name, POOLED_RUN_SYMBOL)
        if run is not None and symbol not in (run.get("trained_symbols") or []):
            logger.warning(
                "Aucun modèle %s pour %s : le run poolé %s n'a PAS été entraîné "
                "sur cet actif (actifs du run : %s) — paire non tradée.",
                strategy_name, symbol, run["id"],
                ", ".join(run.get("trained_symbols") or []) or "inconnus",
            )
            return None
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
