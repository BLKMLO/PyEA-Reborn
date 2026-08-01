"""Couleuvre_v0.2 — UN SEUL modèle LightGBM mutualisé sur TOUS les actifs.

Différence de fond avec ``couleuvre_v0_1`` (gelée, inchangée) : au lieu
d'entraîner un LightGBM par paire, v0.2 entraîne UN modèle unique sur les
données étiquetées de tous les symboles, en ajoutant une feature
d'identité ``symbol`` (catégorielle native LightGBM). Objectif : mutualiser
la statistique (chaque paire seule a peu d'historique) tout en laissant le
modèle apprendre les spécificités de chaque actif via cette feature.

Règles de construction du jeu (anti-fuite entre actifs) :

- features et labels sont calculés **symbole par symbole** — jamais de
  concaténation des OHLCV bruts (les fenêtres glissantes ne doivent pas
  déborder d'un actif sur l'autre) ;
- la colonne ``symbol`` est ajoutée APRÈS, puis seuls les jeux étiquetés
  sont concaténés (triés par temps) ;
- l'early stopping reste causal : la validation est la queue (15 %) de
  CHAQUE symbole, les parties train/valid sont concaténées séparément.

Autres changements de définition par rapport à v0.1 (règle de
versionnement : toute évolution = nouvelle version) :

- seuils plus sélectifs : 0.60 / 0.40 (contre 0.55 / 0.45) — moins de
  trades, plus de conviction (le diagnostic v0.1 montrait des signaux sur
  > 60 % des bougies, coûts > edge) ;
- timeframe recommandé : H4 (spread relativement plus petit).

Features, labeling, barrières (1.5 × ATR14, 5 j) : réutilisés par import
des modules v0.1, sans duplication.

**Encodage de la feature catégorielle ``symbol``** : LightGBM encode une
colonne ``category`` pandas par ses CODES entiers — la cohérence
train/prédiction exige donc la MÊME liste ordonnée de catégories des deux
côtés. La liste (triée) est fixée à l'entraînement, persistée dans
``features.json`` (``symbol_categories``) et rechargée avec le modèle ;
toute prédiction reconstruit la colonne avec ``pd.Categorical(values,
categories=<liste sauvée>)``. Un symbole inconnu de l'entraînement reçoit
le code NaN (-1) — traité comme valeur manquante par LightGBM, honnête.
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

# Seuils de décision PLUS sélectifs que v0.1 (0.55/0.45) : le diagnostic
# OOS de v0.1 montrait des probas décalibrées déclenchant sur > 60 % des
# bougies — un coût de spread supérieur à l'edge. Marge symétrique élargie.
ENTER_LONG_THRESHOLD = 0.60
ENTER_SHORT_THRESHOLD = 0.40
MIN_TRAIN_SAMPLES = 100

#: Colonnes de features du modèle v0.2 : les 34 features causales v0.1
#: PLUS l'identité du symbole (catégorielle). Ordre figé train/inférence.
MODEL_FEATURE_COLUMNS: list[str] = FEATURE_COLUMNS + ["symbol"]

#: Nombre de bougies conservées dans le tampon d'inférence live (même
#: compromis mémoire/coût que v0.1).
_LIVE_BUFFER_BARS = 400

# Hyperparamètres LightGBM identiques à v0.1 (prudents, petits
# historiques) — l'honnêteté vient du walk-forward OOS.
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

#: Part de la QUEUE de chaque symbole réservée à la validation de l'early
#: stopping (causale — jamais dans l'OOS).
_VALIDATION_FRACTION = 0.15
_EARLY_STOPPING_ROUNDS = 30
#: En dessous (total toutes paires), la validation est trop pauvre → on
#: entraîne sur tout le bloc, comme v0.1.
_MIN_VALIDATION_SAMPLES = 100

#: Symbole de repli quand ``train`` reçoit un DataFrame mono-symbole sans
#: ``params["symbol"]`` (outillage générique, tests).
_DEFAULT_SYMBOL = "UNKNOWN"


@register_strategy
class CouleuvreV02(Strategy):
    name = "couleuvre_v0_2"
    version = "0.2.0"

    def __init__(self) -> None:
        self._model: lgb.Booster | None = None
        # Liste ordonnée des catégories de la feature ``symbol`` — figée à
        # l'entraînement, persistée, rechargée avec le modèle (cf. docstring
        # de module : les codes LightGBM en dépendent).
        self._symbol_categories: list[str] = []
        # --- inférence par pré-calcul (backtest / walk-forward) ---
        self._proba: pd.Series | None = None
        self._atr: pd.Series | None = None
        # --- inférence live (agrégation tick→bougie + fenêtre glissante) ---
        self._live = False
        self._timeframe: str | None = None
        self._aggregator: CandleAggregator | None = None
        self._buffer: pd.DataFrame | None = None
        self._symbol: str | None = None

    # ------------------------------------------------------------------ train
    async def train(
        self,
        frames: dict[str, pd.DataFrame] | pd.DataFrame,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Entraîne LE modèle mutualisé.

        ``frames`` : soit ``dict[str, DataFrame]`` (symbole → historique
        OHLCV, cas mutualisé), soit un ``DataFrame`` seul (mono-symbole —
        outillage générique, walk-forward actuel, tests). Dans les deux
        cas, features + labels sont calculés PAR symbole avant toute
        concaténation.
        """
        per_symbol = _normalize_frames(frames, params)
        symbols = sorted(per_symbol)
        categories = symbols  # liste ordonnée figée — clé de l'encodage

        datasets: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
        for symbol in symbols:
            x, y = _build_dataset(per_symbol[symbol], symbol, categories)
            if len(y):
                datasets[symbol] = (x, y)

        n_samples = sum(len(y) for _, y in datasets.values())
        n_classes = len({int(v) for _, y in datasets.values() for v in y.unique()})
        if n_samples < MIN_TRAIN_SAMPLES or n_classes < 2:
            self._model = None
            logger.warning(
                "CouleuvreV02.train : jeu insuffisant (%d échantillons, %d classe(s)) "
                "— modèle non entraîné.", n_samples, n_classes,
            )
            return {
                "trained": False,
                "n_samples": n_samples,
                "n_symbols": len(symbols),
                "symbols": symbols,
                "reason": "jeu trop court ou une seule classe",
            }

        # Early stopping causal : la queue (15 %) de CHAQUE symbole part en
        # validation ; les parties train et valid sont concaténées SÉPARÉMENT
        # (triées par temps) — aucune queue de validation ne précède un
        # échantillon d'entraînement du MÊME symbole.
        n_valid = sum(int(len(y) * _VALIDATION_FRACTION) for _, y in datasets.values())
        if n_valid >= _MIN_VALIDATION_SAMPLES:
            train_parts: list[pd.DataFrame] = []
            valid_parts: list[pd.DataFrame] = []
            y_train_parts: list[pd.Series] = []
            y_valid_parts: list[pd.Series] = []
            for symbol in symbols:
                if symbol not in datasets:
                    continue
                x, y = datasets[symbol]
                k = int(len(y) * _VALIDATION_FRACTION)
                split = len(y) - k if k else len(y)
                train_parts.append(x.iloc[:split])
                y_train_parts.append(y.iloc[:split])
                if k:
                    valid_parts.append(x.iloc[split:])
                    y_valid_parts.append(y.iloc[split:])
            x_train, y_train = _concat_sorted(train_parts, y_train_parts)
            x_valid, y_valid = _concat_sorted(valid_parts, y_valid_parts)
            train_set = lgb.Dataset(
                x_train, label=y_train,
                feature_name=MODEL_FEATURE_COLUMNS,
                categorical_feature=["symbol"],
            )
            valid_set = lgb.Dataset(
                x_valid, label=y_valid,
                feature_name=MODEL_FEATURE_COLUMNS,
                categorical_feature=["symbol"],
                reference=train_set,
            )
            self._model = lgb.train(
                _LGBM_PARAMS,
                train_set,
                num_boost_round=_NUM_BOOST_ROUND,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(_EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            x_all, y_all = _concat_sorted([x_train, x_valid], [y_train, y_valid])
        else:
            x_all, y_all = _concat_sorted(
                [x for x, _ in datasets.values()],
                [y for _, y in datasets.values()],
            )
            dataset = lgb.Dataset(
                x_all, label=y_all,
                feature_name=MODEL_FEATURE_COLUMNS,
                categorical_feature=["symbol"],
            )
            self._model = lgb.train(_LGBM_PARAMS, dataset, num_boost_round=_NUM_BOOST_ROUND)

        self._symbol_categories = categories
        scores = self._model.predict(x_all)
        report: dict[str, Any] = {
            "trained": True,
            "n_samples": n_samples,
            "n_symbols": len(symbols),
            "symbols": symbols,
            "n_features": len(MODEL_FEATURE_COLUMNS),
            "n_trees": self._model.num_trees(),
            "label_balance": round(float(y_all.mean()), 4),
            "train_accuracy": round(float(((scores >= 0.5).astype(int) == y_all).mean()), 4),
            "train_auc": _auc(y_all.to_numpy(), scores),  # in-sample (optimiste)
            "top_features": _top_features(self._model, 8),
        }
        model_dir = params.get("model_dir")
        if model_dir:
            report["model_path"] = self._save(Path(model_dir), params)
        return report

    # ----------------------------------------------------------------- warmup
    async def warmup(self, params: dict[str, Any]) -> None:
        """Prépare l'inférence — deux modes exclusifs, comme v0.1.

        - **backtest / walk-forward** (défaut) : ``params["frame"]`` = tout
          l'historique du pli → ATR et probas pré-calculés par bougie, en
          injectant la colonne ``symbol`` (``params["symbol"]``) avec les
          catégories de l'entraînement avant ``predict``.
        - **live** (``params["live"] = True``) : agrégateur tick→bougie +
          tampon glissant ; à chaque bougie close, recalcul des features et
          prédiction avec la même injection de ``symbol``.
        """
        model_path = params.get("model_path")
        if model_path and self._model is None:
            self._model = lgb.Booster(model_file=str(model_path))
            self._load_symbol_categories(Path(model_path))
        if not self._symbol_categories:
            # Modèle entraîné en mémoire sans catégories connues (ne devrait
            # pas arriver) ou mono-symbole : repli honnête sur le symbole courant.
            self._symbol_categories = [str(params.get("symbol") or _DEFAULT_SYMBOL)]

        if params.get("live"):
            await self._warmup_live(params)
            return

        # --- Mode backtest : pré-calcul vectorisé ---
        self._live = False
        self._symbol = str(params.get("symbol") or _DEFAULT_SYMBOL)
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
            proba.loc[valid.index] = self._model.predict(self._with_symbol(valid))
        self._proba = proba

    async def _warmup_live(self, params: dict[str, Any]) -> None:
        """Mise en place de l'inférence live (agrégateur + tampon de chauffe)."""
        self._live = True
        self._proba = None  # non utilisé en live
        self._atr = None
        self._timeframe = params.get("timeframe") or "H1"
        self._symbol = str(params.get("symbol") or _DEFAULT_SYMBOL)
        self._aggregator = CandleAggregator(self._timeframe)
        frame = params.get("frame")
        if frame is not None and not frame.empty:
            buffer = _canonical_ohlcv(frame)
            self._buffer = buffer.iloc[-_LIVE_BUFFER_BARS:] if len(buffer) > _LIVE_BUFFER_BARS else buffer
        else:
            self._buffer = None
        if self._model is None:
            logger.info(
                "CouleuvreV02 live : aucun modèle chargé pour %s → muette (honnête).",
                self._symbol,
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
        proba = float(self._model.predict(self._with_symbol(last))[0])
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

    def oos_auc(
        self,
        frames: dict[str, pd.DataFrame] | pd.DataFrame,
        test_index: dict[str, pd.DatetimeIndex] | pd.DatetimeIndex,
    ) -> float | None:
        """AUC du modèle sur le bloc de test d'un pli (skill réel).

        Accepte soit ``(DataFrame, DatetimeIndex)`` (mono-symbole, comme
        v0.1), soit ``(dict[str, DataFrame], dict[str, DatetimeIndex])``
        (mutualisé : évaluation par symbole, puis TOUTES les prédictions et
        labels sont mis en commun pour UNE SEULE AUC de Mann–Whitney).
        """
        if self._model is None:
            return None
        if isinstance(frames, dict):
            per_frame = frames
            per_index = test_index if isinstance(test_index, dict) else {}
        else:
            symbol = self._symbol or _DEFAULT_SYMBOL
            per_frame = {symbol: frames}
            per_index = {symbol: test_index} if isinstance(test_index, pd.DatetimeIndex) else {}

        y_all: list[np.ndarray] = []
        scores_all: list[np.ndarray] = []
        for symbol, frame in per_frame.items():
            idx = per_index.get(symbol)
            if idx is None:
                continue
            x, y = _build_dataset(
                frame, symbol, self._symbol_categories or [symbol]
            )
            in_test = x.index.isin(idx)
            x, y = x[in_test], y[in_test]
            if len(y) < 2:
                continue
            y_all.append(y.to_numpy())
            scores_all.append(np.asarray(self._model.predict(x), dtype=float))
        if not y_all:
            return None
        y_cat = np.concatenate(y_all)
        if len(np.unique(y_cat)) < 2:
            return None
        return _auc(y_cat, np.concatenate(scores_all))

    async def shutdown(self) -> None:
        # Libère les buffers d'inférence (le modèle reste en mémoire).
        self._proba = None
        self._atr = None
        self._buffer = None
        self._aggregator = None

    def model_definition(self) -> dict[str, Any]:
        """Constantes figées de couleuvre_v0_2 (source unique pour l'UI)."""
        return {
            "n_features": len(MODEL_FEATURE_COLUMNS),
            "pooled": True,  # un seul modèle mutualisé sur tous les actifs
            "barrier_atr_mult": BARRIER_ATR_MULT,
            "max_hold_days": MAX_HOLD_DAYS,
            "enter_long_threshold": ENTER_LONG_THRESHOLD,
            "enter_short_threshold": ENTER_SHORT_THRESHOLD,
            "recommended_timeframe": "H4",
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
                    "feature_columns": MODEL_FEATURE_COLUMNS,  # inclut "symbol"
                    "symbol_categories": self._symbol_categories,
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

    def _load_symbol_categories(self, model_path: Path) -> None:
        """Recharge la liste des catégories ``symbol`` depuis features.json.

        Sans elle, la feature catégorielle serait ré-encodée au moment de la
        prédiction avec des codes différents de l'entraînement — prédictions
        fausses en silence. Absence du fichier (artefact ancien) → liste vide,
        le repli mono-symbole de ``warmup`` prend le relais.
        """
        features_path = model_path.parent / "features.json"
        try:
            meta = json.loads(features_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        categories = meta.get("symbol_categories")
        if isinstance(categories, list) and categories:
            self._symbol_categories = [str(c) for c in categories]

    # ----------------------------------------------------------------- helpers
    def _with_symbol(self, features: pd.DataFrame) -> pd.DataFrame:
        """Injecte la colonne ``symbol`` catégorielle avant ``predict``.

        La colonne est reconstruite avec EXACTEMENT les catégories de
        l'entraînement (ordre inclus) : LightGBM encode une colonne
        ``category`` pandas par ses codes entiers, la cohérence des codes
        train/prédiction n'est garantie que si la liste est identique.
        Un symbole inconnu de l'entraînement reçoit le code -1 (= NaN,
        traité comme valeur manquante par LightGBM — honnête).
        """
        frame = features.copy()
        frame["symbol"] = _symbol_column(self._symbol or _DEFAULT_SYMBOL,
                                         self._symbol_categories, len(frame))
        return frame[MODEL_FEATURE_COLUMNS]  # ordre canonique du modèle


def _normalize_frames(
    frames: dict[str, pd.DataFrame] | pd.DataFrame, params: dict[str, Any]
) -> dict[str, pd.DataFrame]:
    """Normalise l'entrée de ``train`` en ``dict[symbole, frame]``."""
    if isinstance(frames, dict):
        return {str(symbol): frame for symbol, frame in frames.items()}
    return {str(params.get("symbol") or _DEFAULT_SYMBOL): frames}


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


def _concat_sorted(
    x_parts: list[pd.DataFrame], y_parts: list[pd.Series]
) -> tuple[pd.DataFrame, pd.Series]:
    """Concatène features et labels en GARDANT l'alignement ligne à ligne.

    En poolé, les symboles partagent le même calendrier : l'index concaténé
    contient des doublons, et un ``.loc`` sur des étiquettes dupliquées
    EXPANSERAIT les lignes (produit cartésien) — d'où un tri positionnel
    stable commun aux deux blocs, jamais un réalignement par étiquette.
    """
    x_all = pd.concat(x_parts)
    y_all = pd.concat(y_parts)
    order = np.argsort(x_all.index.values, kind="stable")
    return x_all.iloc[order], y_all.iloc[order]


def _symbol_column(symbol: str, categories: list[str], n: int) -> pd.Categorical:
    """Colonne ``symbol`` catégorielle de longueur ``n``, codes cohérents.

    ``from_codes`` (plutôt que ``pd.Categorical(values, categories=...)``)
    évite le chemin déprécié de pandas quand le symbole est absent des
    catégories : le code -1 (= NaN) est posé directement.
    """
    code = categories.index(symbol) if symbol in categories else -1
    return pd.Categorical.from_codes([code] * n, categories=categories)


def _build_dataset(
    frame: pd.DataFrame, symbol: str, categories: list[str]
) -> tuple[pd.DataFrame, pd.Series]:
    """Features causales + labels triple-barrier d'UN symbole, alignés.

    La colonne ``symbol`` (catégorielle, catégories = liste ordonnée figée)
    est ajoutée APRÈS le dropna — jamais de fenêtre glissante à cheval sur
    deux actifs, puisque le calcul est fait frame par frame.
    """
    features = compute_features(frame)
    labels = triple_barrier_labels(frame)["label"]
    joined = features.copy()
    joined["__label__"] = labels
    joined = joined.dropna()  # retire chauffe features + queue sans label
    x = joined[FEATURE_COLUMNS].copy()
    x["symbol"] = _symbol_column(symbol, categories, len(x))
    return x[MODEL_FEATURE_COLUMNS], joined["__label__"].astype(int)


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
