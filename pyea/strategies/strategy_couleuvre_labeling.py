"""Labeling triple-barrier de Couleuvre_v0.1.

Pour chaque bougie *t*, on place trois barrières à partir de son close :
- **haute** : ``close_t + mult · ATR_t`` (un long y gagnerait) ;
- **basse** : ``close_t − mult · ATR_t`` (un long y perdrait) ;
- **verticale** (temps) : horizon max de ``MAX_HOLD_DAYS`` jours calendaires
  (cohérent avec le swing intra-semaine 2-5 j ; à l'exécution, la clôture
  de fin de semaine du moteur borne aussi le maintien).

Le label est **binaire, symétrique** : ``1`` si la barrière HAUTE est
touchée avant la basse (événement « long gagnant » = « short perdant »),
``0`` sinon. Un même modèle sert donc les deux sens : ``P(haute d'abord)``
élevée → long, faible → short. Si l'horizon expire sans toucher de
barrière, on étiquette par le signe du retour sur l'horizon.

⚠ Le label REGARDE VERS L'AVENIR — c'est sa nature (c'est la cible). Il
n'est défini que pour les bougies disposant d'une fenêtre avant complète ;
les features, elles, restent strictement causales (cf.
``strategy_couleuvre_features``). L'alignement features(t) ↔ label(t) sans
fuite se fait côté ``train`` (les deux partagent l'index, on ``dropna``).

Les multiples/horizon sont des constantes de module : ils font partie de la
DÉFINITION du modèle ``couleuvre_v0_1`` (barrières identiques au labeling et
à l'exécution), pas des réglages runtime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pyea.strategies.strategy_couleuvre_features import atr_series

#: Distance des barrières TP/SL, en multiples d'ATR (symétrique). Doit
#: rester la même à l'entraînement et à l'inférence (cf. CouleuvreV01).
BARRIER_ATR_MULT = 1.5
#: Barrière verticale (horizon max de maintien), en jours calendaires.
MAX_HOLD_DAYS = 5

_NS_PER_DAY = 86_400 * 1_000_000_000


def _hlc(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    def pick(name: str, fallback: pd.Series | None = None) -> pd.Series:
        for col in (f"bid_{name}", name):
            if col in frame.columns:
                return frame[col].astype(float)
        if fallback is not None:
            return fallback  # high/low absents (frame dégradé) → close
        raise ValueError(f"Frame sans colonne '{name}' ni 'bid_{name}'.")

    close = pick("close")
    high = pick("high", close)
    low = pick("low", close)
    return high.to_numpy(), low.to_numpy(), close.to_numpy()


def triple_barrier_labels(
    frame: pd.DataFrame,
    atr_mult: float = BARRIER_ATR_MULT,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> pd.DataFrame:
    """Étiquette chaque bougie par la première barrière touchée.

    Retourne un DataFrame aligné sur ``frame`` :
    - ``label`` : 1 (haute d'abord) / 0 (basse d'abord ou retour négatif à
      l'horizon) ; NaN si ATR indisponible ou aucune bougie avant ;
    - ``barrier`` : ``"tp"`` / ``"sl"`` / ``"time"`` (diagnostic) ;
    - ``atr`` : ATR au moment t (pour info).
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("triple_barrier_labels attend un DatetimeIndex.")

    high, low, close = _hlc(frame)
    atr = atr_series(frame).to_numpy()
    ts = frame.index.asi8  # int64 nanosecondes UTC
    horizon = max_hold_days * _NS_PER_DAY
    n = len(frame)

    labels = np.full(n, np.nan)
    barriers = np.empty(n, dtype=object)

    for t in range(n):
        atr_t = atr[t]
        if not np.isfinite(atr_t) or atr_t <= 0:
            continue
        upper = close[t] + atr_mult * atr_t
        lower = close[t] - atr_mult * atr_t
        # Barrière verticale : dernière bougie dans (t, t + horizon].
        end = np.searchsorted(ts, ts[t] + horizon, side="right") - 1
        if end <= t:
            continue  # pas de fenêtre avant → label indéfini
        label, barrier = None, None
        for j in range(t + 1, end + 1):
            up_hit = high[j] >= upper
            down_hit = low[j] <= lower
            if up_hit and down_hit:
                # Les deux dans la même bougie : on tranche par le close.
                label, barrier = (1, "tp") if close[j] >= close[t] else (0, "sl")
                break
            if up_hit:
                label, barrier = 1, "tp"
                break
            if down_hit:
                label, barrier = 0, "sl"
                break
        if label is None:  # horizon atteint sans barrière → signe du retour
            label = 1 if close[end] >= close[t] else 0
            barrier = "time"
        labels[t] = label
        barriers[t] = barrier

    return pd.DataFrame(
        {"label": labels, "barrier": barriers, "atr": atr}, index=frame.index
    )
