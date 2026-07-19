# Couleuvre_v0.1 — Spécification de la stratégie LightGBM

> Source : ébauche fournie par l'utilisateur (2026-07-19), rédigée en
> collaboration avec une IA — **imparfaite et non figée**. Les annotations
> `⚠ PyEA` signalent les adaptations nécessaires au contexte réel du
> projet. Ce document guidera l'implémentation de
> `strategy_couleuvre_v0_1.py` (méthodes `train`, `warmup`, `on_tick`).

## Caractéristiques du système (swing intra-semaine)

- **Horizon** : swing court, 2 à 5 jours.
- **Contrainte clé** : positions fermées avant le week-end (pas de swap,
  pas de gap non maîtrisé). Fenêtre lundi → vendredi.
  - ✅ PyEA : le moteur (`backtest_engine.py`) force désormais la clôture à
    la dernière bougie de la semaine ISO (aucun portage sur le week-end) —
    ce plafond temporel borne aussi l'horizon 2-5 j intra-semaine.
- **Actifs** : l'ébauche vise « Forex, Crypto, Actions ».
  - ⚠ PyEA : le périmètre actuel est celui de la watchlist (forex,
    métaux, indices — données Dukascopy, exécution IB). Crypto/actions =
    hors scope tant qu'aucun broker/flux ne les couvre.
- **Labeling** : triple barrier (TP/SL proportionnels à l'ATR + horizon
  max en jours).
  - ✅ PyEA (exécution) : le moteur teste désormais TP/SL en INTRABAR
    (high/low de chaque bougie suivante) ; un `Signal` porte
    `stop_loss`/`take_profit`, reportés par le RiskManager sur l'ordre.
    Reste côté labeling/entraînement : produire ces labels triple-barrier
    sur l'historique pour `CouleuvreV01.train()`.
- **Validation** : walk-forward — ✅ déjà en place
  (`pyea/training/training_walkforward.py`, fenêtre expansive).
- **Modèle** : ✅ **tranché — un LightGBM par actif** (décision
  utilisateur 2026-07-19). Conséquence directe sur les features : module
  mono-symbole, pas de feature « classe d'actif » (inutile) ni cross-asset
  (v2). Le stockage par run (`data/models/<run>/`) accueillera un modèle
  par symbole.

## Features prévues

### Prix et retours
- Retours **log** sur fenêtres 1, 3, 5, 10, 20 jours.
- Position du prix dans son range récent : `(close - low_20) / (high_20 - low_20)`.
- Gaps d'ouverture (effet week-end forex/actions).

### Tendance
- SMA/EMA multi-horizons + écart au prix ; pentes de moyennes mobiles.
- MACD + histogramme ; ADX (force de tendance).

### Momentum / retournement
- RSI multi-fenêtres ; stochastique ; ROC.

### Volatilité
- **ATR** (pilier du triple-barrier) ; volatilité réalisée (rolling std) ;
  largeur des bandes de Bollinger ; ratio vol courte/longue (régime).

### Volume
- Volume relatif à sa moyenne, OBV, spikes.
- ⚠ PyEA : le « volume » Dukascopy en forex spot est un volume de ticks,
  pas un volume réel — features à pondérer en conséquence, surtout
  pertinentes pour les indices.

### Calendrier / saisonnalité
- Jour de la semaine ; « jours restants avant vendredi » (cohérent avec
  la contrainte intra-semaine) ; session active (Asie/Europe/US).
- Proximité d'événements macro (NFP, CPI, taux) — ⚠ PyEA : nécessite une
  source de calendrier économique externe, à traiter comme un
  enrichissement v2.

### Cross-asset / contexte
- Corrélation à une référence (DXY pour le forex, S&P pour indices).
- Régime de risque global (VIX…) — ⚠ PyEA : mêmes réserves de source de
  données que le calendrier macro.
- Feature catégorielle « classe d'actif » si modèle unique.

## Ordre d'implémentation proposé

1. ✅ Moteur de backtest : clôture forcée du vendredi + barrières intrabar
   (high/low) — pré-requis d'honnêteté des métriques. **Fait** (v2 du
   moteur, `Signal.stop_loss`/`take_profit`).
2. ✅ Module features (`pyea/strategies/strategy_couleuvre_features.py`) :
   calcul vectorisé sur DataFrame OHLCV ré-échantillonné, SANS fuite
   temporelle (fenêtres strictement causales). **Fait** —
   `compute_features(frame)` → 34 features (`FEATURE_COLUMNS`, ordre figé),
   `WARMUP_BARS`. Anti-fuite garantie par un test de stabilité par préfixe
   (`compute_features(frame)[:k] == compute_features(frame[:k])`).
   Mono-symbole (un LightGBM par actif) : **aucune** feature « classe
   d'actif » ni cross-asset (DXY/S&P/VIX) ni macro (NFP/CPI) — reportées
   en v2 (source de données externe).
3. Labeling triple-barrier + `CouleuvreV01.train()` (fit LightGBM,
   sauvegarde `model.txt` + features dans `data/models/<run>/`).
4. `warmup()` (chargement du modèle choisi) et `on_tick()` (features
   incrémentales + seuils de décision → Signal).
5. Walk-forward complet sur la page backtest, décisions sur les métriques
   **out-of-sample uniquement**.
