# Couleuvre — Spécification des stratégies LightGBM

> **2026-07-31 — v0.2 : la décision « un modèle par actif » est RÉVOQUÉE**
> (demande utilisateur). `couleuvre_v0_2` entraîne **UN modèle unique
> mutualisé sur tous les actifs** — voir la [section v0.2](#couleuvre_v02--modèle-unique-multi-actifs).
> La section v0.1 ci-dessous reste la référence de la version figée.

# Couleuvre_v0.1 — Spécification de la stratégie LightGBM (figée)

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
  utilisateur 2026-07-19, **révoquée le 2026-07-31 → v0.2 mutualisée**).
  Conséquence directe sur les features : module
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
3. ✅ Labeling triple-barrier (`strategy_couleuvre_labeling.py`) +
   `CouleuvreV01.train()` : **fait**. Label binaire symétrique
   (1 = barrière haute d'abord), fit LightGBM (`P(haute d'abord)`),
   sauvegarde `model.txt` + `features.json` par pli dans
   `data/models/<run>/fold_<i>/`.
4. ✅ `warmup()` (frame fourni par le moteur → features/ATR/probas
   pré-calculés ; charge un `model.txt` en live) et `on_tick()` (proba de la
   bougie → seuils `ENTER_LONG_THRESHOLD`/`ENTER_SHORT_THRESHOLD`, barrières
   TP/SL dimensionnées au même multiple d'ATR que le labeling) : **fait**.
   Livré avec l'étape 3 car sans inférence le walk-forward ne testerait
   rien.
5. ✅ Walk-forward complet sur la page **Entraînement** (`/training`) —
   **opérationnel** : décider
   sur les métriques **out-of-sample uniquement**. La colonne « AUC IS »
   (in-sample) affichée en regard du taux de gain OOS par pli rend l'écart
   de généralisation (surapprentissage) visible d'un coup d'œil.

## Comment vérifier qu'un modèle est bon sur une paire

Il n'y a pas de « bouton test » séparé : **le walk-forward de la page
Entraînement EST le test**. Pour une paire :

1. Page **Entraînement** (`/training`) → choisir le symbole, le timeframe,
   le nombre de plis, lancer.
2. Lire les **métriques out-of-sample** (trades, P&L, taux de gain,
   drawdown) — jamais l'in-sample, toujours optimiste.
3. Comparer, par pli, **AUC IS** (skill in-sample, proche de 1 = le modèle
   mémorise) au **taux de gain OOS** : un grand écart = surapprentissage,
   des valeurs cohérentes = apprentissage sain. Un modèle qui « trade
   beaucoup mais gagne ~50 % en OOS » ne capte rien d'exploitable.
4. L'historique des runs (table du bas) permet de comparer plusieurs
   configurations sur la même paire.

⚠ Validé jusqu'ici sur historique **synthétique local** (Dukascopy bloqué
en sandbox) : le pipeline est correct et sans fuite (prouvé sur bruit pur),
mais les taux de gain réels ne se jugeront que sur de vraies données.

---

# Couleuvre_v0.2 — Modèle unique multi-actifs

> Ajout du 2026-07-31 (`pyea/strategies/strategy_couleuvre_v0_2.py`,
> classe `CouleuvreV02`). La règle de versionnement du projet est respectée :
> v0.1 est **gelée**, toute évolution de définition vit ici.

## Pourquoi (diagnostic v0.1)

Le walk-forward v0.1 sur EURUSD H1 a tranché honnêtement : **pas d'edge
exploitable** avec un modèle par actif — AUC IS 0,94 / AUC OOS 0,52,
probas décalibrées déclenchant sur > 60 % des bougies, coût du spread
supérieur au micro-edge. Trois leviers retenus, par ordre d'impact :

1. **Mutualiser les données** : chaque actif seul a peu d'historique ; le
   pooling multi-actifs multiplie les échantillons d'entraînement et donne
   au modèle de quoi apprendre des régimes plutôt que du bruit.
2. **Timeframe plus long** : H4 conseillé — moins de trades, spread
   relativement plus petit face à l'amplitude visée.
3. **Seuils plus sélectifs** : 0.60 / 0.40 (contre 0.55 / 0.45) — moins de
   trades, de meilleure conviction, coûts proportionnellement plus faibles.

## Changements de définition vs v0.1

- **UN LightGBM pour tous les actifs** : features + labels calculés
  **symbole par symbole** (jamais de concaténation des OHLCV bruts — les
  fenêtres glissantes ne doivent pas déborder d'un actif sur l'autre),
  puis concaténation des seuls jeux étiquetés. Les 34 features v0.1 sont
  réutilisées telles quelles (déjà sans échelle : returns, déviations
  relatives, ratios).
- **Feature d'identité `symbol`** (35e feature) : catégorielle native
  LightGBM. Les codes entiers dépendent de la liste ordonnée des
  catégories — elle est figée à l'entraînement, persistée dans
  `features.json` (`symbol_categories`) et reconstruite à l'identique à
  chaque prédiction (`pd.Categorical.from_codes` ; symbole inconnu → code
  −1 = valeur manquante).
- **Early stopping causal** : la queue (15 %) de CHAQUE symbole part en
  validation ; train et validation sont concaténés séparément (tri
  positionnel stable — un `.loc` sur les timestamps dupliqués entre actifs
  expanserait les lignes).
- **Entraînement** : page Entraînement, `symbols` omis = tous les actifs
  ayant un historique ; run enregistré sous la sentinelle **`ALL`** ;
  `resolve_live_model` sert le modèle poolé à tous les symboles (repli
  après le run propre au symbole). Le walk-forward poolé découpe sur la
  plage commune, entraîne UN modèle par pli et évalue chaque actif
  séparément, avec agrégation honnête (PF / taux de gain sur TOUS les
  trades, jamais moyennés ; Sharpe/SQN par pli laissés à None — non
  fusionnables entre actifs) et ventilation par actif (`oos_by_symbol`).
- Inchangé : barrières 1,5 × ATR14, horizon 5 j, clôture avant week-end,
  coûts mesurés (spread médian + commission).

## Comment juger v0.2

Même discipline que v0.1 : **décider sur l'out-of-sample uniquement** —
AUC OOS par pli face à l'AUC IS, profit factor et P&L OOS nets de coûts,
et désormais la **ventilation par actif** pour repérer les actifs qui
portent (ou plombent) l'agrégat.
