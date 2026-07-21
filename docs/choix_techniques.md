# Justification des choix techniques

Les décisions datées vivent dans le journal de `CLAUDE.md` ; ce document
développe le raisonnement pour celles qui structurent le projet.

- **REST + WebSocket** (plutôt que du polling ou du SSE) : REST couvre les
  actions ponctuelles (config, historique) avec HTMX sans build front ;
  le WebSocket porte le flux continu (prix, signaux, statut) nécessaire à
  la mise à jour live des graphiques — SSE aurait suffi pour du
  descendant pur, mais le WS laisse la porte ouverte aux commandes
  temps réel depuis le dashboard.

- **`ib_async` plutôt que `ibapi` natif ou `ib_insync`** : `ibapi` impose
  de gérer soi-même threading, callbacks et reconnexions (beaucoup de code
  fragile) ; `ib_insync` n'est plus maintenu depuis le décès de son
  auteur (2024) ; `ib_async` en est le fork communautaire maintenu, avec
  la même API asyncio de haut niveau — le meilleur rapport
  simplicité/maintenance aujourd'hui.

- **SQLite + SQLAlchemy** : zéro infra au départ ; la migration Postgres
  se réduit à changer `storage.database_url`.

- **HTMX + Tailwind + Chart.js, vendorisés en local** : aucun build front,
  dashboard et formulaires triviaux à écrire ; les libs sont servies
  depuis `static/vendor/` (pas de CDN au runtime — un dashboard de
  trading sur VPS doit fonctionner sans internet sortant, avec des
  versions déterministes). Tailwind ne gère que le style et n'interfère
  pas avec Chart.js, dont l'initialisation est centralisée dans
  `static/js/charts.js` et alimentée par `/api/charts/*`.

- **TradingView Lightweight Charts pour le graphique de prix** (plutôt
  qu'un plugin chandeliers pour Chart.js, ou une réécriture React) :
  pan/zoom natifs, chargement paresseux de l'historique par pagination
  (`/api/charts/price-history?before=`), mise à jour incrémentale
  (`series.update`) qui préserve la position de défilement pendant le
  refresh périodique. Le besoin était une capacité de la *librairie de
  graphique*, pas du framework : React/Vite aurait imposé un build sans
  résoudre le défilement. Chart.js reste vendorisé pour les futurs
  graphiques classiques (courbe de P&L, distributions). Le logo
  TradingView affiché sur le graphique est l'attribution requise par la
  licence Apache 2.0 — ne pas le retirer.

- **Entraînement : jobs en thread + progression via le bus d'événements**
  (plutôt que Celery/RQ/Redis, ou une requête HTTP bloquante) : un
  walk-forward LightGBM durera des minutes — il tourne dans un thread
  dédié (`training_jobs.py`), publie sa progression sur le bus (topic
  `training.progress`) que le WebSocket relaie déjà, et reste
  interrogeable/annulable par REST (`/api/training/jobs/{id}`). Une file
  distribuée n'apporterait rien à un EA mono-utilisateur local et
  casserait le principe « zéro infra ». Un seul job à la fois (un
  entraînement sature déjà un cœur). Chaque run est historisé en SQLite
  (`training_runs`) avec ses métriques out-of-sample et ses artefacts
  (`data/models/<run>/`) — sans historique comparable, impossible de
  savoir si un modèle progresse.

- **Couleuvre : LightGBM natif, label binaire symétrique, features
  pré-calculées** (cf. `docs/strategie_couleuvre.md`) :
  - *API Booster native* (`lgb.train`) plutôt que le wrapper scikit-learn
    (`LGBMClassifier`) : évite d'ajouter `scikit-learn` aux dépendances
    pour un simple `fit`/`predict` ; l'AUC in-sample est calculée à la main
    (Mann–Whitney, `numpy`).
  - *Un seul modèle binaire pour les deux sens* : le label triple-barrier
    est `1` si la barrière haute est touchée avant la basse (barrières
    symétriques à `mult·ATR`). `P(haute d'abord)` élevée → long, faible →
    short : un modèle sert les deux, au lieu d'en entraîner deux. Les
    barrières du labeling et de l'exécution partagent le même multiple
    d'ATR (aucune divergence train/exécution).
  - *Features pré-calculées dans `warmup`* (le moteur fournit le frame),
    puis lues ligne à ligne dans `on_tick`. C'est plus rapide qu'un calcul
    incrémental ET **exactement identique** : la stabilité par préfixe des
    features (testée) garantit que `features(t)` ne dépend jamais du futur,
    donc précalcul sur tout le frame == calcul sur le seul passé. La
    décision à la bougie `t` ne lit que la ligne `t`.
  - *In-sample = optimiste, seul l'OOS juge* : sur du **bruit pur**, le
    modèle atteint ~0,96 d'AUC in-sample mais ~50 % de taux de gain OOS
    (test de non-régression `test_pas_de_fuite_pnl_nul_sur_bruit`). D'où la
    colonne « AUC IS » affichée en regard du taux de gain OOS par pli :
    l'écart entre les deux est le diagnostic direct de surapprentissage.
  - *Un modèle par actif, entraîné manuellement* : chaque paire se forme
    via la page **Entraînement** (`/training`, un symbole par run) ; le
    walk-forward out-of-sample de cette page EST le test de qualité de la
    paire.

- **Moteur de backtest : backtrader (vendorisé), pas de moteur maison** :
  l'exécution simulée et les métriques ne sont plus recalculées à la main
  mais déléguées à **backtrader** (moteur événementiel éprouvé, GPLv3, pur
  Python). Décisions :
  - *backtrader plutôt que vectorbt ou backtesting.py* : `vectorbt` impose
    `numba`/`llvmlite` (binaires natifs, **non vendorisables** cross-OS) et
    ré-introduit `scikit-learn` ; `backtesting.py` remplit toujours à la
    bougie *suivante* (crash de validation des barrières, clôture week-end
    ingérable). backtrader est **pur Python** (donc vendorisable) et son
    mode *cheat-on-close* remplit au close de la bougie de décision —
    exactement le modèle PyEA.
  - *Vendorisé dans `lib/backtrader/`* (zéro `pip install`, hors-ligne) :
    répond à la fragilité d'install Windows connue et à l'esprit « VPS sans
    internet ». Possible car pur Python ; les libs à extension native
    (lightgbm/pyarrow/numba) restent, elles, en pip. GPLv3 : aucune
    obligation tant que PyEA n'est pas **distribué** (usage perso/VPS = OK),
    l'imposerait en cas de distribution publique.
  - *Le flux PyEA est préservé* : on garde `Strategy → Signal → RiskManager
    → OrderRequest` DANS le callback par bougie ; backtrader ne fait que
    l'exécution + la comptabilité. Entrée Market (cheat-on-close), barrières
    = Stop (SL) + Limit (TP) natifs **OCO** au prix exact, tie-break = stop
    (natif), clôture forcée fin de semaine + liquidation finale via ordres
    Market. On ne trade qu'1 unité nominale, le P&L linéaire est re-scalé par
    `max_position_size` (Sharpe/SQN invariants d'échelle). **Fidélité
    vérifiée bougie à bougie** : les valeurs des tests moteur sont identiques
    à l'ancien moteur maison ; gain net = analyzers standard (Sharpe, SQN,
    profit factor) et une exécution éprouvée.

- **Dukascopy comme source d'historique** (plutôt qu'IB ou yfinance) :
  flux public gratuit sans compte, M1 remontant avant 2010 sur le forex ;
  IB exige TWS connecté et impose des limites de débit sévères sur
  l'historique ; yfinance n'a pas d'intraday ancien. Détails :
  [donnees_historiques.md](donnees_historiques.md).
