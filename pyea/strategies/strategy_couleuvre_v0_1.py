"""Couleuvre_v0.1 — stratégie de swing intra-semaine basée sur LightGBM.

Pipeline (cf. ``docs/strategie_couleuvre.md``) :

1. **train** : features causales (``strategy_couleuvre_features``) + labels
   triple-barrier (``strategy_couleuvre_labeling``), alignés puis ``dropna``
   (le ``dropna`` retire la chauffe des features ET la queue sans fenêtre
   avant des labels → aucune fuite). Fit d'un classifieur binaire LightGBM
   ``P(barrière haute touchée avant la basse)``.
2. **warmup** : sur un frame donné (le moteur le fournit), pré-calcule les
   features, l'ATR et — si un modèle est chargé — les probabilités
   vectorisées (rapide et exactement égal à un calcul incrémental, la
   garantie de stabilité par préfixe des features le prouve).
3. **on_tick** : lit la proba de la bougie courante ; au-dessus du seuil
   long → ENTER_LONG, en dessous du seuil short → ENTER_SHORT, sinon rien.
   Les barrières TP/SL du signal sont dimensionnées au MÊME multiple d'ATR
   que le labeling (cohérence train/exécution) et exécutées en intrabar par
   le moteur.

**Un modèle par actif** : chaque paire est entraînée séparément (via la
page backtest → section Entraînement). Le walk-forward out-of-sample de
cette page EST le test de qualité par paire (métriques OOS honnêtes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from pyea.core.core_domain import Signal, SignalAction, TickData
from pyea.core.core_logging import get_logger
from pyea.live.live_candles import CandleAggregator, Candle
from pyea.strategies.strategy_base import Strategy
from pyea.strategies.strategy_couleuvre_features import (
    FEATURE_COLUMNS,
    WARMUP_BARS,
    atr_series,
    compute_features,
)
from pyea.strategies.strategy_couleuvre_labeling import (
    BARRIER_ATR_MULT,
    MAX_HOLD_DAYS,
    triple_barrier_labels,
)
from pyea.strategies.strategy_registry import register_strategy

logger = get_logger(__name__)

# Seuils de décision autour de 0.5 (marge symétrique) — au-delà = long,
# en deçà = short, entre les deux = pas de conviction.
ENTER_LONG_THRESHOLD = 0.55
ENTER_SHORT_THRESHOLD = 0.45
MIN_TRAIN_SAMPLES = 100

#: Nombre de bougies conservées dans le tampon d'inférence live (fenêtre
#: glissante pour recalculer les features causales). > WARMUP_BARS avec de la
#: marge ; borne mémoire/coût de calcul (recalcul vectorisé à chaque bougie).
_LIVE_BUFFER_BARS = 400

# Hyperparamètres LightGBM volontairement prudents (petits historiques,
# risque de surapprentissage) — l'honnêteté vient du walk-forward OOS.
_LGBM_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": 42,
}
_NUM_BOOST_ROUND = 300


@register_strategy
class CouleuvreV01(Strategy):
    name = "couleuvre_v0_1"
    version = "0.1.0"

    def __init__(self) -> None:
        self._model: lgb.Booster | None = None
        # --- inférence par pré-calcul (backtest / walk-forward) ---
        self._proba: pd.Series | None = None
        self._atr: pd.Series | None = None
        # --- inférence live (agrégation tick→bougie + fenêtre glissante) ---
        self._live = False
        self._timeframe: str | None = None
        self._aggregator: CandleAggregator | None = None
        self._buffer: pd.DataFrame | None = None

    # ------------------------------------------------------------------ train
    async def train(
        self, frame: pd.DataFrame, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        x, y = _build_dataset(frame)
        n_samples = len(y)
        if n_samples < MIN_TRAIN_SAMPLES or y.nunique() < 2:
            self._model = None
            logger.warning(
                "Couleuvre.train : jeu insuffisant (%d échantillons, %d classe(s)) "
                "— modèle non entraîné.", n_samples, y.nunique(),
            )
            return {
                "trained": False,
                "n_samples": n_samples,
                "reason": "jeu trop court ou une seule classe",
            }

        dataset = lgb.Dataset(x, label=y, feature_name=FEATURE_COLUMNS)
        self._model = lgb.train(_LGBM_PARAMS, dataset, num_boost_round=_NUM_BOOST_ROUND)

        scores = self._model.predict(x)
        report: dict[str, Any] = {
            "trained": True,
            "n_samples": n_samples,
            "n_features": len(FEATURE_COLUMNS),
            "label_balance": round(float(y.mean()), 4),  # part de « haute d'abord »
            "train_accuracy": round(float(((scores >= 0.5).astype(int) == y).mean()), 4),
            "train_auc": _auc(y.to_numpy(), scores),  # in-sample (optimiste)
            "top_features": _top_features(self._model, 8),
        }
        model_dir = params.get("model_dir")
        if model_dir:
            report["model_path"] = self._save(Path(model_dir), params)
        return report

    # ----------------------------------------------------------------- warmup
    async def warmup(self, params: dict[str, Any]) -> None:
        """Prépare l'inférence.

        DEUX modes, exclusifs :

        - **backtest / walk-forward** (défaut) : ``params["frame"]`` = tout
          l'historique du pli → on pré-calcule ATR et probas par bougie
          (``on_tick`` fera un simple lookup par timestamp). Exact et rapide
          (stabilité par préfixe des features).
        - **live** (``params["live"] = True``) : ticks continus → on met en
          place l'agrégateur tick→bougie du ``timeframe`` du modèle et un
          tampon glissant amorcé par ``frame`` (historique récent de chauffe).
          ``on_tick`` recalcule alors les features à chaque bougie close.
        """
        model_path = params.get("model_path")
        if model_path and self._model is None:
            self._model = lgb.Booster(model_file=str(model_path))

        if params.get("live"):
            await self._warmup_live(params)
            return

        # --- Mode backtest : pré-calcul vectorisé ---
        self._live = False
        frame = params.get("frame")
        if frame is None or frame.empty:
            self._proba = None
            self._atr = None
            return

        self._atr = atr_series(frame)
        if self._model is None:
            self._proba = None
            return
        features = compute_features(frame)
        valid = features.dropna()
        proba = pd.Series(np.nan, index=features.index, dtype=float)
        if not valid.empty:
            proba.loc[valid.index] = self._model.predict(valid[FEATURE_COLUMNS])
        self._proba = proba

    async def _warmup_live(self, params: dict[str, Any]) -> None:
        """Mise en place de l'inférence live (agrégateur + tampon de chauffe)."""
        self._live = True
        self._proba = None  # non utilisé en live
        self._atr = None
        self._timeframe = params.get("timeframe") or "H1"
        self._aggregator = CandleAggregator(self._timeframe)
        frame = params.get("frame")
        if frame is not None and not frame.empty:
            buffer = _canonical_ohlcv(frame)
            self._buffer = buffer.iloc[-_LIVE_BUFFER_BARS:] if len(buffer) > _LIVE_BUFFER_BARS else buffer
        else:
            self._buffer = None
        if self._model is None:
            logger.info(
                "Couleuvre live : aucun modèle chargé pour %s → muette (honnête).",
                params.get("symbol", "?"),
            )

    # ---------------------------------------------------------------- on_tick
    async def on_tick(self, tick: TickData) -> Signal | None:
        if self._live:
            return await self._on_tick_live(tick)
        # --- Mode backtest : lookup de la proba pré-calculée par bougie ---
        if self._model is None or self._proba is None or self._atr is None:
            return None
        try:
            proba = self._proba.at[tick.timestamp]
            atr = self._atr.at[tick.timestamp]
        except KeyError:
            return None
        if not np.isfinite(proba) or not np.isfinite(atr) or atr <= 0:
            return None
        return self._decide(tick.symbol, float(proba), tick.price, float(atr))

    async def _on_tick_live(self, tick: TickData) -> Signal | None:
        """Agrège le tick ; à chaque bougie CLOSE, recalcule et décide."""
        if self._model is None or self._aggregator is None:
            return None
        candle = self._aggregator.add(tick.price, tick.volume, tick.timestamp)
        if candle is None:  # bougie encore en cours de formation
            return None
        self._append_candle(candle)
        buffer = self._buffer
        if buffer is None or len(buffer) < WARMUP_BARS:
            return None  # pas encore assez d'historique pour des features valides

        features = compute_features(buffer)
        last = features.iloc[[-1]]  # dernière bougie close = décision causale
        if last[FEATURE_COLUMNS].isna().any(axis=None):
            return None
        atr_val = float(atr_series(buffer).iloc[-1])
        if not np.isfinite(atr_val) or atr_val <= 0:
            return None
        proba = float(self._model.predict(last[FEATURE_COLUMNS])[0])
        return self._decide(tick.symbol, proba, float(candle.close), atr_val)

    def _decide(
        self, symbol: str, proba: float, price: float, atr: float
    ) -> Signal | None:
        """Traduit proba + ATR en signal (mêmes seuils/barrières train et live)."""
        offset = BARRIER_ATR_MULT * atr
        if proba >= ENTER_LONG_THRESHOLD:
            return Signal(
                strategy_name=self.name, symbol=symbol,
                action=SignalAction.ENTER_LONG, confidence=proba,
                stop_loss=price - offset, take_profit=price + offset,
            )
        if proba <= ENTER_SHORT_THRESHOLD:
            return Signal(
                strategy_name=self.name, symbol=symbol,
                action=SignalAction.ENTER_SHORT, confidence=proba,
                stop_loss=price + offset, take_profit=price - offset,
            )
        return None

    def _append_candle(self, candle: Candle) -> None:
        """Ajoute une bougie close au tampon glissant (dédup + trim)."""
        row = pd.DataFrame(
            {
                "bid_open": [candle.open],
                "bid_high": [candle.high],
                "bid_low": [candle.low],
                "bid_close": [candle.close],
                "volume": [candle.volume],
            },
            index=[candle.start],
        )
        if self._buffer is None or self._buffer.empty:
            self._buffer = row
        else:
            self._buffer = pd.concat([self._buffer, row])
        self._buffer = self._buffer[~self._buffer.index.duplicated(keep="last")]
        if len(self._buffer) > _LIVE_BUFFER_BARS:
            self._buffer = self._buffer.iloc[-_LIVE_BUFFER_BARS:]

    async def shutdown(self) -> None:
        # Libère les buffers d'inférence (le modèle reste en mémoire).
        self._proba = None
        self._atr = None
        self._buffer = None
        self._aggregator = None

    def model_definition(self) -> dict[str, Any]:
        """Constantes figées de couleuvre_v0_1 (source unique pour l'UI)."""
        return {
            "n_features": len(FEATURE_COLUMNS),
            "barrier_atr_mult": BARRIER_ATR_MULT,
            "max_hold_days": MAX_HOLD_DAYS,
            "enter_long_threshold": ENTER_LONG_THRESHOLD,
            "enter_short_threshold": ENTER_SHORT_THRESHOLD,
            "objective": "binaire — P(barrière haute touchée avant la basse)",
        }

    # ------------------------------------------------------------- persistance
    def _save(self, model_dir: Path, params: dict[str, Any]) -> str:
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "model.txt"
        assert self._model is not None
        self._model.save_model(str(model_path))
        (model_dir / "features.json").write_text(
            json.dumps(
                {
                    "version": self.version,
                    "feature_columns": FEATURE_COLUMNS,
                    "barrier_atr_mult": BARRIER_ATR_MULT,
                    "enter_long_threshold": ENTER_LONG_THRESHOLD,
                    "enter_short_threshold": ENTER_SHORT_THRESHOLD,
                    "params": params,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return str(model_path)


def _canonical_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Extrait un OHLCV canonique ``bid_*`` + ``volume`` pour le tampon live.

    Accepte les colonnes ``bid_*`` (préférées) ou nues ``open/high/low/close``
    (comme ``compute_features``) ; open/high/low absents retombent sur close.
    """
    def pick(name: str) -> pd.Series | None:
        for col in (f"bid_{name}", name):
            if col in frame.columns:
                return frame[col].astype(float)
        return None

    close = pick("close")
    if close is None:
        raise ValueError("Frame de chauffe live sans colonne 'close' ni 'bid_close'.")
    open_ = pick("open")
    high = pick("high")
    low = pick("low")
    volume = frame["volume"].astype(float) if "volume" in frame.columns else pd.Series(0.0, index=frame.index)
    return pd.DataFrame(
        {
            "bid_open": close if open_ is None else open_,
            "bid_high": close if high is None else high,
            "bid_low": close if low is None else low,
            "bid_close": close,
            "volume": volume,
        }
    )


def _build_dataset(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Features causales + labels triple-barrier, alignés et nettoyés."""
    features = compute_features(frame)
    labels = triple_barrier_labels(frame)["label"]
    joined = features.copy()
    joined["__label__"] = labels
    joined = joined.dropna()  # retire chauffe features + queue sans label
    return joined[FEATURE_COLUMNS], joined["__label__"].astype(int)


def _auc(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    """AUC ROC par la statistique de Mann–Whitney (sans dépendance sklearn)."""
    n_pos = int((y_true == 1).sum())
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = scores.argsort()
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    auc = (ranks[y_true == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return round(float(auc), 4)


def _top_features(model: lgb.Booster, k: int) -> list[tuple[str, int]]:
    importances = model.feature_importance(importance_type="gain")
    names = model.feature_name()
    ranked = sorted(zip(names, importances), key=lambda kv: kv[1], reverse=True)
    return [(name, int(round(gain))) for name, gain in ranked[:k]]
