"""Features de Couleuvre_v0.1 — calcul vectorisé, sans fuite temporelle.

Ce module transforme un DataFrame OHLCV (M1 déjà ré-échantillonné vers le
timeframe de travail, cf. ``resample_history``) en une matrice de features
pour LightGBM. Il est utilisé aux DEUX bouts du cycle :

- entraînement : ``CouleuvreV01.train`` calcule les features sur tout
  l'historique du pli, puis les aligne avec les labels triple-barrier ;
- inférence : ``on_tick`` recalcule les features sur une fenêtre glissante
  d'historique récent et lit la dernière ligne.

**Garantie anti-fuite (leakage)** : toute feature à la bougie *t* n'utilise
que des données ≤ *t* (rolling/ewm/shift/diff strictement causaux — jamais
de fenêtre centrée ni de ``shift`` négatif). La feature au close de *t* est
donc connue au moment de décider à ce close ; seul le LABEL regarde vers
l'avenir. Propriété vérifiée par un test de stabilité par préfixe :
``compute_features(frame)[:k] == compute_features(frame[:k])``.

**Un modèle par actif** (choix projet) : ce module est volontairement
mono-symbole — aucune feature « classe d'actif » (inutile si chaque paire a
son LightGBM) ni feature cross-asset (corrélation DXY/S&P, VIX), qui
exigeraient une source de données externe et sont reportées en v2. Idem
pour la proximité d'événements macro (NFP/CPI). Cf.
``docs/strategie_couleuvre.md``.

Les fenêtres sont des constantes de module : elles font partie de la
DÉFINITION du modèle ``couleuvre_v0_1`` (versionnées avec la stratégie),
pas des réglages runtime — donc pas dans ``config.yaml``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --- Fenêtres (part de la définition du modèle couleuvre_v0_1) -------------
RETURN_WINDOWS = (1, 3, 5, 10, 20)
SMA_WINDOWS = (10, 20, 50)
EMA_WINDOWS = (12, 26)
RSI_WINDOWS = (7, 14)
ROC_WINDOWS = (5, 10)
RANGE_WINDOW = 20
ATR_PERIOD = 14
ADX_PERIOD = 14
STOCH_PERIOD = 14
STOCH_SMOOTH = 3
BB_WINDOW = 20
RV_WINDOW = 20
VOL_RATIO_SHORT = 10
VOL_RATIO_LONG = 50
VOLUME_WINDOW = 20
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

#: Historique minimum recommandé avant la première ligne pleinement valide
#: (la plus longue fenêtre = 50 ; les lissages de Wilder se stabilisent
#: au-delà). En deçà, les features contiennent des NaN — à ``dropna``.
WARMUP_BARS = 60

#: Colonnes de sortie, ordre canonique et figé (train et inférence DOIVENT
#: partager exactement cette liste et cet ordre).
FEATURE_COLUMNS: list[str] = (
    [f"ret_log_{n}" for n in RETURN_WINDOWS]
    + ["range_pos_20", "gap_open"]
    + [f"sma_dev_{n}" for n in SMA_WINDOWS]
    + ["sma_slope_20"]
    + [f"ema_dev_{n}" for n in EMA_WINDOWS]
    + ["macd", "macd_signal", "macd_hist", "adx_14"]
    + [f"rsi_{n}" for n in RSI_WINDOWS]
    + ["stoch_k_14", "stoch_d_14"]
    + [f"roc_{n}" for n in ROC_WINDOWS]
    + ["atr_norm_14", "realized_vol_20", "bb_width_20", "vol_ratio_10_50"]
    + ["vol_rel_20", "vol_spike_20", "obv_z_20"]
    + ["dow", "days_to_friday", "hour_utc", "session"]
)


def compute_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calcule toutes les features de Couleuvre sur un frame OHLCV.

    Entrée : DataFrame indexé par un ``DatetimeIndex`` (UTC de préférence),
    avec des colonnes ``bid_open/high/low/close`` (préférées) ou
    ``open/high/low/close``, plus ``volume``. Les colonnes manquantes de
    high/low/open retombent sur le close (frames de test dégradés) —
    fonctionnel mais les features associées deviennent neutres.

    Sortie : DataFrame aligné sur l'index d'entrée, colonnes =
    ``FEATURE_COLUMNS`` (ordre figé), NaN pendant la période de chauffe,
    ``±inf`` remplacés par NaN.
    """
    open_, high, low, close, volume = _ohlcv(frame)
    feats: dict[str, pd.Series] = {}

    # --- Prix et retours ---------------------------------------------------
    log_close = np.log(close)
    for n in RETURN_WINDOWS:
        feats[f"ret_log_{n}"] = log_close.diff(n)
    low_r = low.rolling(RANGE_WINDOW).min()
    high_r = high.rolling(RANGE_WINDOW).max()
    feats["range_pos_20"] = (close - low_r) / (high_r - low_r)
    feats["gap_open"] = np.log(open_ / close.shift(1))

    # --- Tendance ----------------------------------------------------------
    for n in SMA_WINDOWS:
        sma = close.rolling(n).mean()
        feats[f"sma_dev_{n}"] = close / sma - 1.0
    sma20 = close.rolling(20).mean()
    feats["sma_slope_20"] = (sma20 - sma20.shift(5)) / sma20
    for n in EMA_WINDOWS:
        ema = close.ewm(span=n, adjust=False, min_periods=n).mean()
        feats[f"ema_dev_{n}"] = close / ema - 1.0
    macd, macd_signal, macd_hist = _macd(close)
    feats["macd"] = macd / close
    feats["macd_signal"] = macd_signal / close
    feats["macd_hist"] = macd_hist / close
    feats["adx_14"] = _adx(high, low, close, ADX_PERIOD)

    # --- Momentum ----------------------------------------------------------
    for n in RSI_WINDOWS:
        feats[f"rsi_{n}"] = _rsi(close, n)
    stoch_k, stoch_d = _stochastic(high, low, close, STOCH_PERIOD, STOCH_SMOOTH)
    feats["stoch_k_14"] = stoch_k
    feats["stoch_d_14"] = stoch_d
    for n in ROC_WINDOWS:
        feats[f"roc_{n}"] = close.pct_change(n, fill_method=None) * 100.0

    # --- Volatilité --------------------------------------------------------
    feats["atr_norm_14"] = _atr(high, low, close, ATR_PERIOD) / close
    ret1 = log_close.diff(1)
    feats["realized_vol_20"] = ret1.rolling(RV_WINDOW).std()
    feats["bb_width_20"] = _bollinger_width(close, BB_WINDOW)
    rv_short = ret1.rolling(VOL_RATIO_SHORT).std()
    rv_long = ret1.rolling(VOL_RATIO_LONG).std()
    feats["vol_ratio_10_50"] = rv_short / rv_long

    # --- Volume (⚠ Dukascopy forex = volume de ticks, pas réel) ------------
    vol_mean = volume.rolling(VOLUME_WINDOW).mean()
    vol_std = volume.rolling(VOLUME_WINDOW).std()
    feats["vol_rel_20"] = volume / vol_mean
    # Volume à variance nulle (certains flux de test/indices) → spike = 0,
    # jamais NaN : sinon une colonne entièrement NaN ferait tout sauter au
    # dropna de l'entraînement. Les NaN de chauffe (std indéfinie) restent.
    spike = (volume - vol_mean) / vol_std
    feats["vol_spike_20"] = spike.where(vol_std != 0, 0.0)
    feats["obv_z_20"] = _obv_zscore(close, volume, VOLUME_WINDOW)

    # --- Calendrier / saisonnalité ----------------------------------------
    calendar = _calendar_features(frame.index)
    feats.update(calendar)

    result = pd.DataFrame(feats, index=frame.index)
    result = result.replace([np.inf, -np.inf], np.nan)
    return result[FEATURE_COLUMNS]  # ordre canonique + garde-fou complétude


def atr_series(frame: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """ATR de Wilder **brut** (non normalisé) aligné sur ``frame``.

    Source de vérité de l'ATR pour tout Couleuvre : le labeling
    triple-barrier (placement des barrières sur l'historique) ET
    l'inférence (dimensionnement des barrières TP/SL à l'entrée) l'utilisent
    — mêmes valeurs des deux côtés, aucune divergence train/exécution.
    """
    _, high, low, close, _ = _ohlcv(frame)
    return _atr(high, low, close, period)


# --------------------------------------------------------------------------
# Sélection des colonnes OHLCV (bid_* préféré, fallback open/high/low/close)
# --------------------------------------------------------------------------
def _ohlcv(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    def pick(name: str) -> pd.Series | None:
        for col in (f"bid_{name}", name):
            if col in frame.columns:
                return frame[col].astype(float)
        return None

    close = pick("close")
    if close is None:
        raise ValueError("Frame sans colonne 'close' ni 'bid_close'.")
    open_ = pick("open")
    high = pick("high")
    low = pick("low")
    open_ = close if open_ is None else open_
    high = close if high is None else high
    low = close if low is None else low
    if "volume" in frame.columns:
        volume = frame["volume"].astype(float)
    else:
        volume = pd.Series(0.0, index=frame.index)
    return open_, high, low, close, volume


# --------------------------------------------------------------------------
# Indicateurs — tous causaux (aucune fuite temporelle)
# --------------------------------------------------------------------------
def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Lissage de Wilder = EMA récursive d'alpha 1/period (causale).
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)
    atr = _atr(high, low, close, period)
    alpha = 1 / period
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=MACD_FAST, adjust=False, min_periods=MACD_FAST).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False, min_periods=MACD_SLOW).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=MACD_SIGNAL, adjust=False, min_periods=MACD_SIGNAL).mean()
    return macd, signal, macd - signal


def _stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int, smooth: int
) -> tuple[pd.Series, pd.Series]:
    low_n = low.rolling(period).min()
    high_n = high.rolling(period).max()
    k = 100.0 * (close - low_n) / (high_n - low_n)
    d = k.rolling(smooth).mean()
    return k, d


def _bollinger_width(close: pd.Series, window: int) -> pd.Series:
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    return (4.0 * std) / sma  # (upper - lower) / mid, bandes ± 2σ


def _obv_zscore(close: pd.Series, volume: pd.Series, window: int) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    obv = (direction * volume).cumsum()
    # OBV brut = non stationnaire → z-score glissant (borné, comparable).
    return (obv - obv.rolling(window).mean()) / obv.rolling(window).std()


def _calendar_features(index: pd.Index) -> dict[str, pd.Series]:
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("compute_features attend un DatetimeIndex.")
    dow = pd.Series(index.dayofweek, index=index, dtype=float)
    hour = pd.Series(index.hour, index=index, dtype=float)
    # Sessions FX par heure UTC : 0 Asie, 1 Europe, 2 US, 3 creux.
    session = pd.Series(
        np.select(
            [hour < 7, hour < 13, hour < 21],
            [0.0, 1.0, 2.0],
            default=3.0,
        ),
        index=index,
    )
    return {
        "dow": dow,
        "days_to_friday": (4.0 - dow).clip(lower=0.0),  # vendredi (4) → 0
        "hour_utc": hour,
        "session": session,
    }
