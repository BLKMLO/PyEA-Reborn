# CLAUDE.md — Mémoire de travail du projet PyEA

> Ce fichier est la **source de vérité du contexte projet**. Il est chargé
> au début de chaque session. Règle de maintenance : **après chaque
> changement notable, mettre à jour ce fichier** (journal de décisions
> inclus) plutôt que de compter sur la mémoire de conversation.

## Identité — règle de nommage (importante)

- **PyEA** = le logiciel / la plateforme : serveur web, brokers, risque,
  stockage, package Python `pyea/`. Toute l'identité visible (titres,
  logs, FastAPI, dashboard) dit « PyEA ».
- **Couleuvre** = uniquement le **moteur de décision** : la stratégie
  LightGBM `couleuvre_v0_1` (`pyea/strategies/strategy_couleuvre_v0_1.py`,
  classe `CouleuvreV01`). Ne jamais nommer le logiciel « Couleuvre ».

## Commandes

```bash
python run_server.py        # commande CLI principale : démarre le serveur web
python download_history.py  # EXCEPTION assumée : téléchargement ponctuel de
                            # l'historique M1 (Dukascopy) pour le backtest
pytest                      # tests (structure miroir dans tests/)
```

Config : `config.yaml` (fonctionnel, versionné) + `.env` (secrets IB,
jamais commité — modèle dans `.env.example`).

## Règles d'architecture (non négociables)

1. **Flux strict** : `MarketDataFeed → Strategy → Signal → RiskManager →
   OrderRequest → BrokerGateway`. Aucune stratégie ne parle au broker ;
   aucun ordre ne contourne le risk manager.
2. **Câblage uniquement dans `pyea/app_factory.py:create_app()`** — les
   modules ne s'instancient pas entre eux.
3. **Bus d'événements** (`pyea/core/core_events.py`) : producteurs
   (broker, stratégie, logs) et consommateurs (WebSocket, persistance)
   ne se connaissent pas. La logique métier n'importe jamais `pyea/api/`.
4. **Config centralisée** : tout passe par
   `pyea.config.config_settings.get_settings()` — aucune lecture directe
   d'`os.environ` ou du YAML ailleurs.
5. **Paper → live** = changer `broker.trading_mode` dans `config.yaml`
   (ports IB dans `.env`). Rien d'autre.

## Conventions

- Fichiers `snake_case`, classes `PascalCase`, préfixe = package :
  `strategy_*.py`, `broker_*.py`, `api_*.py`, `core_*.py`, `data_*.py`,
  `storage_*.py`.
- Nouvelle stratégie : `strategies/strategy_<nom>.py` + `@register_strategy`
  + import dans `strategies/__init__.py`. Nouveau broker : idem avec
  `brokers/broker_<nom>.py` + `@register_gateway`.
- Tests : `tests/<package>/test_<module>.py`, miroir strict du source.
- Graphiques : init uniquement dans `pyea/web/static/js/charts.js`,
  données via endpoints JSON `/api/charts/*`. Graphique de prix =
  TradingView Lightweight Charts (chandeliers, pan/zoom, historique
  paginé) ; Chart.js réservé aux futurs graphiques classiques (P&L,
  distributions).
- Libs front (Tailwind, HTMX, Lightweight Charts, Chart.js)
  **vendorisées** dans `pyea/web/static/vendor/` — jamais de CDN au
  runtime (le dashboard doit marcher sur un VPS sans internet sortant).

## Données historiques (backtest)

- Layout : `data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet` — un
  dossier par actif, un fichier par année. Supprimer une paire = supprimer
  son dossier ; supprimer une période = supprimer les fichiers d'années.
- M1 natif (bid/ask OHLC + volume, index UTC). Conversion vers M5, M15,
  M30, H1, H4, D1, W1, MN1 : `resample_history(frame, "H1")` dans
  `data_history_downloader.py` — le backtest H1 rechargera le M1 puis
  ré-échantillonnera (2 lignes de code, quelques secondes par année).
- `load_history(data_dir, symbol, start, end)` = point d'entrée de lecture.

## Notes environnement de dev (sandbox Claude)

- Réseau sortant filtré par proxy : `datafeed.dukascopy.com` **bloqué**
  (503) — le téléchargeur n'est validé que par tests unitaires ; premier
  run réel à vérifier chez l'utilisateur. Les CDN npm/jsdelivr passent.
- Captures d'écran du dashboard : Playwright + Chromium
  (`/opt/pw-browsers/chromium`) — Chromium ne voit pas le proxy, d'où la
  vendorisation des libs front (qui était de toute façon souhaitable).

## Notes environnement utilisateur (poste local, Windows)

- **Python 3.13 + `pip install -r requirements.txt`** : install parfois
  incomplète sans erreur bloquante affichée — symptôme observé chez
  l'utilisateur : `uvicorn` présent mais `click` absent
  (`ModuleNotFoundError` au lancement de `run_server.py`). Cause probable :
  `lightgbm`/`pyarrow` sans wheel précompilé pour 3.13 sous Windows,
  échec de compilation qui interrompt le reste de l'install. **Contourné**
  en installant les paquets un par un (liste dans `requirements.txt`).
  Recommandation : **Python 3.11 ou 3.12** pour une install garantie sans
  y penser ; 3.13+ reste possible mais peut demander l'install manuelle.

## Documentation

- `README.md` = présentation concise + démarrage rapide uniquement.
- La doc technique vit dans `docs/` : `architecture.md` (arborescence,
  règles, conventions, où ajouter du code), `donnees_historiques.md`
  (Dukascopy, Parquet, timeframes), `choix_techniques.md` (justifications).
- Conséquence : toute évolution d'architecture ou de convention se
  répercute dans `docs/` (et ici), pas dans le README.

## Préférences utilisateur

- Répondre et documenter en **français**.
- Après chaque modification : réfléchir systématiquement aux
  **conséquences annexes** (config, docs, tests, .gitignore, CLAUDE.md).
- Maintenir ce fichier comme mémoire persistante du projet.

## État du projet

- Échafaudage complet et fonctionnel : serveur web, REST + WebSocket,
  registres stratégie/broker, SQLAlchemy (SQLite), 108 tests verts.
- Dashboard live façon TradingView : chandeliers M1 au centre
  (**TradingView Lightweight Charts** : pan/zoom natifs, historique
  paginé via `?before=`, refresh incrémental `series.update` qui
  préserve le défilement), **légende OHLC en surimpression** (suit le
  crosshair, retombe sur la dernière bougie « vivante » hors survol,
  variation intra-bougie colorée) + état « Chargement… » au changement
  d'onglet, watchlist à droite **façon « Market Watch »** (clic = onglet,
  pastille verte = paire armée, **dernier prix + variation 24 h colorée**
  par ligne, servis par `/api/symbols` enrichi via `_demo_quote`,
  rafraîchis en place toutes les 10 s sans flicker), header en **badges
  colorés** (pill mode PAPER/LIVE, pastille de connexion broker,
  stratégie) + indicateur temps réel WS vert/rouge, sens BUY/SELL coloré
  dans les positions, **bouton Trading (vert) / Stopped (rouge)** par
  paire à côté du titre du graphique — état par symbole persisté en
  SQLite (`storage_trading_state.py`, défaut = Stopped), relu à chaque
  changement d'onglet (`GET /api/trading/{symbol}`), bascule via
  `PUT /api/trading/{symbol}`, confirmation JS si mode live,
  **badge broker du header cliquable → fenêtre de connexion broker**
  (**liste déroulante pour choisir le courtier** — Interactive Brokers ou
  MetaTrader 5 —, paramètres du broker choisi en LECTURE SEULE + note
  d'authentification propre à chaque broker + bouton Se connecter/déconnecter
  + état réel ; `GET /api/brokers`, `POST /api/broker/connect|disconnect`).
  **Aucun login/mot de passe dans PyEA** : IB s'authentifie via TWS/IB
  Gateway, MetaTrader 5 via un terminal MT5 déjà ouvert (compte démo/réel
  choisi dans le terminal). Broker actif = `broker.name` de config au
  démarrage, changeable à chaud depuis la fenêtre (déconnexion requise pour
  basculer ; le choix runtime ne réécrit pas la config).
  panneau bas Positions (fermées grisées, récentes en premier) / Logs,
  P&L total en bas à droite, **nav à 3 pages dans le header : Live |
  Backtest | Entraînement**. Rafraîchissement du seul graphique actif
  toutes `ui.chart_refresh_seconds` (config.yaml, défaut 5 s).
  **Données factices déterministes** (seed symbole+minute) servies par
  `/api/charts/price-history` et `/api/positions` — le câblage broker
  réel ne remplacera que les fonctions `_demo_*` d'`api_rest.py`.
- Téléchargeur d'historique M1 Dukascopy opérationnel côté code
  (`download_history.py` + `pyea/data/data_history_downloader.py`,
  31 instruments dans `config.yaml:history`, Parquet par symbole/année
  dans `data/history/`) — **pas encore validé contre le flux réel**
  (réseau sortant bloqué dans la sandbox de dev) : au premier lancement
  chez l'utilisateur, vérifier les prix logués (facteurs décimaux) .
- Interface de backtest opérationnelle (`/backtest`, **recentrée sur le run
  unique** depuis que l'entraînement a sa propre page) : formulaire
  (symbole/timeframe/stratégie/période, alimenté par
  `/api/backtest/datasets` qui scanne `data/history/`), exécution via
  `POST /api/backtest/run` (endpoint sync → threadpool FastAPI, ne bloque
  pas le live), résultats = cartes stats (bougies, trades, P&L, taux de
  gain, drawdown max **+ Sharpe / SQN / profit factor**), courbe d'équité
  (Chart.js) et table des trades.
  Moteur : `pyea/backtest/backtest_engine.py` — **adossé à backtrader**
  (moteur événementiel éprouvé, GPLv3, pur Python, **vendorisé dans
  `lib/backtrader/`**, zéro install). L'`BacktestEngine`/`BacktestResult`
  restent le contrat public ; en interne le **flux PyEA est préservé**
  (`Strategy → Signal → RiskManager → OrderRequest` DANS le callback par
  bougie), backtrader ne fait QUE l'exécution + la comptabilité + les
  métriques. Modèle (fidèle à l'ancien moteur maison, vérifié bougie à
  bougie — mêmes valeurs de tests) : entrée Market en **cheat-on-close**
  (décision remplie au bid_close), **barrières TP/SL = ordres Stop (SL) +
  Limit (TP) natifs OCO** au prix exact, **stop prioritaire** si les deux
  franchies dans la même bougie (natif backtrader), **clôture forcée fin de
  semaine ISO** + liquidation finale via ordres Market, courbe d'équité
  ≤ 500 points. Détails : on ne trade qu'**1 unité** nominale, le P&L
  linéaire est re-scalé par `max_position_size` (Sharpe/SQN invariants
  d'échelle) ; `Open` synthétisé = close précédent borné [low,high] (marché
  continu sans gap) ; une **bougie « fantôme »** (copie de la dernière)
  permet aux clôtures cheat-on-close du dernier bar de se réaliser ; méthodes
  async de la stratégie pontées sur une boucle asyncio dédiée (`engine.run`
  est désormais **synchrone**). Les barrières transitent par le domaine :
  `Signal.stop_loss`/`take_profit` → RiskManager → `OrderRequest`. Frames
  sans high/low (tests) : high=low=close → barrières sur le close. Sur cette
  page, Couleuvre n'a pas de modèle chargé (aucun entraînement dans un run
  de backtest simple) → 0 trade honnête ; pour la voir trader, passer par
  la page Entraînement (walk-forward).
- `RiskManager.evaluate` **v1 implémentée** (plus un squelette) : HOLD
  ignoré, EXIT → ordre inverse de la position ouverte, entrées à taille
  fixe `risk.max_position_size` refusées au-delà de
  `risk.max_open_positions`. À enrichir : perte journalière max,
  kill-switch, sizing dynamique.
- **Page Entraînement dédiée** (`/training`, `training.html` +
  `training.js`) : walk-forward à fenêtre expansive
  (`pyea/training/training_walkforward.py`, plis de test consécutifs,
  jamais de split aléatoire), exécution en **job de thread**
  (`training_jobs.py`, un seul job actif à la fois, annulable — annulation
  re-vérifiée ENTRE les phases d'un pli, pas seulement entre plis),
  progression **temps réel** via bus topic `training.progress` → WebSocket
  (+ polling `GET /api/training/jobs/{id}` en secours). Le **chargement de
  l'historique vit DANS le job** (phase « Chargement… ») : le POST répond
  immédiatement, la boucle asyncio ne gèle jamais. **Reprise après
  rechargement de page** : `GET /api/training/current-job` + ré-attachement
  automatique de l'UI (progression/annulation retrouvées). Les runs restés
  « running » après un arrêt serveur sont marqués « failed » au démarrage
  (`fail_orphan_runs`). Chaque run est
  historisé en SQLite (table `training_runs` : params, métriques OOS,
  statut) avec artefacts dans `data/models/<run>/` (metadata.json +
  `model.txt`/`features.json` par pli). Hook `Strategy.train(frame, params)`
  au contrat (défaut no-op = non entraînable). L'UI met en avant
  l'**out-of-sample** : cartes OOS, **courbe d'équité OOS**, table des plis
  avec colonne **AUC IS** (in-sample) en regard du taux de gain OOS (écart =
  surapprentissage), et **panneau « définition du modèle » en lecture
  seule** (features/barrières/horizon/seuils, servi par
  `GET /api/training/definition/{strategy}` = source unique via
  `Strategy.model_definition()`). La page backtest garde un renvoi vers
  cet onglet.
- **Spécification de Couleuvre v0.1** fournie par l'utilisateur :
  `docs/strategie_couleuvre.md` (swing intra-semaine 2-5 j, triple
  barrier ATR, features prix/tendance/momentum/vol/calendrier, **un modèle
  par actif — tranché**). Les deux **pré-requis moteur** (clôture forcée de
  fin de semaine + barrières intrabar) sont **livrés** (moteur v2). Volume
  forex Dukascopy = volume de ticks ; crypto/actions hors scope actuel.
- **Module features de Couleuvre livré** (étape 2 de la spec) :
  `pyea/strategies/strategy_couleuvre_features.py`. `compute_features(frame)`
  → 34 features vectorisées (`FEATURE_COLUMNS`, ordre figé) sur un OHLCV
  ré-échantillonné : retours log, position dans le range, gap d'ouverture,
  SMA/EMA/MACD/ADX, RSI/stochastique/ROC, ATR/vol réalisée/Bollinger/ratio
  de vol, volume relatif/spike/OBV z-score, calendrier (dow, jours avant
  vendredi, heure, session FX). **Sans fuite temporelle** (fenêtres
  strictement causales) — garantie par un test de **stabilité par préfixe**.
  Mono-symbole (un LightGBM/actif) : pas de feature classe d'actif ni
  cross-asset (DXY/VIX/macro = v2). Fenêtres = constantes de module
  (définition du modèle, pas de config). `WARMUP_BARS` = historique mini.
- **Couleuvre_v0.1 opérationnelle de bout en bout** (étapes 3-5) :
  `strategy_couleuvre_labeling.py` (triple-barrier, **label binaire
  symétrique** = 1 si barrière haute touchée avant la basse) +
  `CouleuvreV01.train/warmup/on_tick`. `train` : features causales + labels
  alignés puis `dropna` (retire chauffe ET queue sans label → sans fuite),
  fit LightGBM natif (`P(haute d'abord)`), sauvegarde `model.txt` +
  `features.json` par pli dans `data/models/<run>/fold_<i>/`, rapport
  (accuracy/AUC in-sample, balance, top features). `warmup` : le moteur
  fournit le frame → features/ATR/probas pré-calculés (exact et sans fuite,
  cf. stabilité par préfixe). `on_tick` : proba de la bougie → seuils
  (0.55/0.45) → ENTER_LONG/SHORT avec barrières TP/SL au même multiple
  d'ATR que le labeling. **Un modèle par actif, entraîné manuellement**
  (un symbole par run). **Comment tester une paire** : le walk-forward OOS
  de la page backtest EST le test ; la colonne **AUC IS** (in-sample)
  affichée en regard du taux de gain OOS par pli rend le surapprentissage
  visible. **Non-fuite prouvée** : sur bruit pur, AUC in-sample ~0,96 mais
  taux de gain OOS ~50 % (test `test_pas_de_fuite_pnl_nul_sur_bruit`).
  Validé sur historique synthétique local (Dukascopy bloqué) — taux réels
  à juger sur vraies données.
- **2026-07-19** — Install Windows/Python 3.13 en échec silencieux
  (`click` manquant alors qu'`uvicorn` est présent) : `pip install
  -r requirements.txt` s'arrête partiellement, probablement sur
  `lightgbm`/`pyarrow` sans wheel pour 3.13. Résolu par install paquet par
  paquet côté utilisateur. `requirements.txt` documente désormais la
  commande de secours et recommande Python 3.11/3.12 pour éviter le
  problème (voir « Notes environnement utilisateur »).
- **2026-07-19** — Passe de fiabilisation après premiers retours de test
  utilisateur (« impossible d'entraîner ») : chargement des données déplacé
  dans le job (plus de gel de la boucle asyncio ni de clic « sans effet »),
  reprise de job après rechargement de page (`/api/training/current-job`),
  annulation réactive, runs orphelins nettoyés au démarrage, labeling
  triple-barrier ~16× plus rapide (scan numpy par chunks, sortie prouvée
  identique), `load_history` ne lit que les années de la période, le
  téléchargeur survit à une année en échec (résumé en fin de run), erreurs
  422 lisibles côté UI, `wss://` derrière HTTPS. 80 tests verts.
- **Deux brokers enregistrés** : `InteractiveBrokersGateway` (ib_async, via
  TWS/IB Gateway) et `MetaTraderGateway` (paquet `MetaTrader5`, attache à un
  terminal MT5). **MetaTrader : connexion + lecture de compte RÉELLES**
  (`connect`/`disconnect`/`is_connected`/`get_positions`/`get_account_summary`
  via `MetaTrader5.initialize()`/`account_info()`/`positions_get()`), import
  paresseux (paquet Windows non installé en sandbox → 503 honnête « installez
  MetaTrader5 », jamais une fausse connexion) — **1er run réel à valider chez
  l'utilisateur** (comme Dukascopy). IB `connect()` reste à écrire (ib_async).
- **Squelettes restants** (NotImplementedError) : le **routage d'ordres**
  (`place_order`/`cancel_order`) et le **flux de prix** (`subscribe_market_data`)
  des DEUX brokers — aucun appelant tant que le flux live n'est pas monté dans
  `app_factory` — et `MarketDataFeed`. On ne simule surtout jamais un ordre.

## Points de vigilance (audit modularité 2026-07-18)

Le graphe d'imports est sain (aucun module métier n'importe `api/`,
dépendances uniquement vers `core`/`config`, lecture env/YAML confinée à
`config_settings.py`). Trois points à surveiller, pas à corriger :

1. `event_bus` et `web_log_buffer` sont des singletons de module, pas
   injectés par `create_app()` (incohérent avec `MarketDataFeed` qui
   reçoit son bus). Si les tests exigent un jour des bus isolés, les
   faire passer par `app_factory`.
2. ~~`/api/status` code en dur `broker_connected: False`~~ **RÉSOLU
   (2026-07-20)** : gateway instanciée dans le `lifespan`, exposée par le
   singleton `broker_runtime` ; `/api/status` lit `is_connected()` réel.
3. Le `lifespan` de `app_factory` ne monte pas encore gateway + stratégie
   + feed : c'est au premier câblage complet que le flux
   `Signal → RiskManager → OrderRequest` devra être imposé (aucun
   raccourci stratégie→broker, même « pour tester »).

## Journal de décisions

- **2026-07-20** — **Moteur de backtest maison remplacé par backtrader**
  (demande utilisateur : « mettre en place un moteur de backtest déjà
  existant, solide »). Sauvegarde préalable dans la branche
  `before_backtrade_api` (poussée). Cheminement (l'utilisateur a changé
  d'option plusieurs fois, chaque étape tranchée par un PROTOTYPE avant
  d'écrire — leçon Dukascopy « ne jamais livrer un moteur non validé ») :
  (1) **vectorbt écarté** : ré-écriture vectorielle contournerait le
  RiskManager (viole la règle #1) ET — surtout — **non vendorisable** :
  `numba`/`llvmlite` sont des binaires natifs spécifiques OS/Python
  (impossible à déposer dans `lib/` cross-plateforme), et il ré-introduit
  `scikit-learn` qu'on avait retiré exprès. (2) **backtesting.py écarté**
  (prototypé) : remplit TOUJOURS à la bougie SUIVANTE → la validation des
  barrières **crashe** (SL passé du mauvais côté du fill différé) et la
  clôture « jamais de week-end » devient impossible (fill lundi). (3)
  **backtrader retenu** : pur Python (**vendorisé dans `lib/backtrader/`**,
  zéro install, hors-ligne — répond à la contrainte utilisateur « libs dans
  le projet » et à la fragilité d'install Windows connue), et son mode
  **cheat-on-close** remplit au close de décision = modèle PyEA. GPLv3 :
  aucune obligation tant que PyEA n'est pas distribué (perso/VPS = OK) —
  consigné. (4) **Design validé bougie à bougie par prototypes** : entrée
  Market (coc) + barrières **Stop/Limit natifs OCO** au prix exact (fill
  exact, tie-break stop natif), clôture forcée week-end/finale via ordres
  Market, **bougie fantôme** (copie de la dernière) pour réaliser les
  clôtures coc du dernier bar, enregistrement des trades via `notify_trade`
  (net, robuste au transient coc). On ne trade qu'**1 unité**, P&L re-scalé
  par `max_position_size` (ratios invariants d'échelle). **Le flux PyEA est
  préservé** (Strategy→Signal→RiskManager→OrderRequest dans le callback) —
  la règle #1 n'est PAS assouplie, contrairement à ce qu'aurait imposé
  vectorbt. (5) **Conséquences traitées** : `BacktestEngine.run` devient
  **synchrone** (backtrader l'est ; méthodes async de la stratégie pontées
  sur une boucle dédiée) → appelants adaptés (`api_backtest.py`,
  `training_walkforward.py`, plus d'`asyncio.run(engine.run)`) ;
  `sys.path` préfixé de `lib/` dans `pyea/__init__.py` ; `requirements.txt`
  documente le vendoring (backtrader NON listé en pip) ;
  `docs/architecture.md` (+ `lib/`, arbo) et `docs/choix_techniques.md`
  (justification) mis à jour. (6) **Nouvelles métriques** exposées par
  l'API et la page backtest : **Sharpe** (riskfreerate=0 pour rester
  invariant d'échelle, sinon le taux dominait le rendement ~0 sur 1 unité),
  **SQN**, **profit factor**, avg/best/worst trade. Le « drawdown % » de
  backtrader **écarté** (dilué par le capital nominal → trompeur), seul le
  drawdown ABSOLU (courbe re-scalée) est gardé. (7) **Fidélité prouvée** :
  les 10 tests moteur passent avec les **mêmes valeurs** que l'ancien moteur
  (entrée au close, barrières exactes, tie-break, clôture week-end,
  liquidation) ; test anti-fuite Couleuvre toujours ~50 % OOS sur bruit
  (aucune fuite introduite) ; +1 test « métriques avancées ». Validé
  end-to-end (Couleuvre entraînée → backtest OOS : Sharpe 1.79, SQN 0.95,
  profit factor 1.10) et au navigateur (page backtest, 8 cartes, zéro
  erreur console). **108 tests verts.** Reste inchangé : gateway IB +
  feed live.

- **2026-07-20** — **MetaTrader 5 ajouté comme second broker** (demande
  utilisateur : « ajoute metatrader comme logiciel reliable à PyEA » +
  « liste déroulante pour choisir le courtier »). Décisions et conséquences :
  (1) **Nouvelle gateway** `brokers/broker_metatrader.py` (`MetaTraderGateway`,
  nom `metatrader5`, paquet officiel `MetaTrader5`) — **import paresseux**
  (dans les méthodes) : le paquet est Windows-only et absent de la sandbox,
  la gateway doit quand même s'enregistrer et l'app démarrer partout (même
  logique que le téléchargeur Dukascopy). (2) **Même modèle d'authentification
  qu'IB, tranché pour rester cohérent avec « pas de login/mdp dans PyEA »** :
  PyEA **s'attache** à un terminal MT5 déjà lancé et connecté (démo/réel choisi
  DANS le terminal) via `MetaTrader5.initialize()` — aucun identifiant saisi
  dans PyEA. Conséquence assumée : `broker_credentials.py` reste inutilisé (ni
  IB ni MT5 n'en ont besoin) ; `trading_mode` de config **ne pilote pas** le
  démo/réel de MT5 (signalé dans la fenêtre). (3) **Connexion + lecture de
  compte RÉELLES** pour MT5 (`connect`/`disconnect`/`is_connected`/
  `get_positions`/`get_account_summary`) — read-only, sûr à écrire sans test ;
  **routage d'ordres et flux de prix laissés en `NotImplementedError`** (comme
  IB : aucun appelant tant que le flux live n'est pas monté — on ne simule
  jamais un ordre). Paquet absent → **503 honnête** (« installez MetaTrader5 »),
  jamais de fausse connexion. **1er run réel à valider chez l'utilisateur.**
  (4) **Liste déroulante** de choix du broker : contrat `BrokerGateway` enrichi
  (`label`, `connection_info()`, `connection_hint()` par broker → fenêtre
  générique, plus de champs host/port codés en dur) + `list_gateways()` au
  registre ; `broker_runtime` gère le **broker actif** et sa **bascule runtime**
  (`select()`, refusée si connecté — un seul compte à la fois ; la config
  `broker.name` reste le défaut au démarrage et n'est pas réécrite). (5) **API**
  : `GET /api/broker` (singulier) → **`GET /api/brokers`** (liste + params/état
  de chacun) ; `POST /api/broker/connect` accepte `{broker}` (sélection avant
  connexion, 404 si inconnu, 409 si bascule à chaud sur connexion vivante) ;
  `/api/status.broker` = broker ACTIF réel (pas la config brute). (6) **Front**
  : `<select>` peuplé par `/api/brokers`, paramètres + note reconstruits par
  broker, un seul JS (`charts.js`). Conséquences traitées : `config.yaml`
  (brokers dispo + note MT5), `.env.example` (`MT5_TERMINAL_PATH` optionnel,
  commenté — aucun secret), `config_settings.py` (`mt5_terminal_path`),
  `docs/architecture.md` (arbo brokers + `/api/brokers`). Validé au navigateur
  (Playwright : dropdown, params/note dynamiques, 503 honnête sur MT5, badge
  header reflétant le broker actif, zéro fausse connexion). Tests : 1 test IB
  d'info remplacé par la liste + 2 tests MT5 (503 sans paquet, broker inconnu),
  **107 verts**. Reste : IB `connect()` (ib_async), routage d'ordres + feed
  live des deux brokers.

- **2026-07-20** — **Fenêtre broker : login/mdp retirés** (l'utilisateur a
  tranché après clarification). L'API Interactive Brokers **ne
  s'authentifie pas par identifiants** : c'est TWS / IB Gateway (déjà
  logué) qui gère le compte ; PyEA se connecte au socket API via
  host/port/client_id. Conséquences : la modale « Identifiants » devient
  une fenêtre de **Connexion** (host/port/client_id/mode en lecture seule +
  Se connecter/déconnecter + état) ; endpoints `GET/PUT/DELETE
  /api/broker/credentials` **supprimés**, remplacés par `GET /api/broker`
  (infos) ; `broker_credentials_set` retiré de `/api/status` ; la gateway
  IB ne lit plus `broker_credentials.password`. Le module
  `broker_credentials.py` est **conservé en code** (réservé à un futur
  broker qui, lui, aurait besoin d'identifiants) mais n'est plus câblé.
  6 tests credentials retirés, 1 test `/api/broker` ajouté, 105 verts.
  Validé au navigateur (fenêtre sans champ login/mdp, host:port affichés,
  connexion → 501 honnête).

- **2026-07-20** — **Passe « honnêteté de l'interface »** (demande
  utilisateur après usage réel : « je ne veux pas de mensonges dans mon
  interface »). Principe posé : **PyEA ne fabrique JAMAIS de données de
  COMPTE** (positions, trades, P&L, état de connexion) — elles viennent du
  broker (gateway) ou du journal SQL. Seules les données de MARCHÉ
  (graphique, prix watchlist) restent une démo, mais **étiquetée « DÉMO »**
  (badge violet dans le header, `market_data_live: false` dans
  `/api/status`). Livré : (1) **Toasts** (`static/js/toasts.js`, chargé
  partout via base.html) sur connexion broker, backtest, entraînement,
  armement de paire, erreurs. (2) **Bouton Trading désactivé si broker
  déconnecté** (grisé + title explicatif) ; `PUT /api/trading/{symbol}`
  **refuse d'armer** (409) sans broker connecté — désarmer reste toujours
  permis. **`_demo_positions` SUPPRIMÉ** (c'était la source des « faux
  trades »). (3) **État broker RÉEL** : gateway instanciée dans le
  `lifespan` et exposée via `broker_runtime` (singleton, résout le point
  de vigilance n°2) ; `/api/status.broker_connected` reflète
  `gateway.is_connected()` (déconnecté tant qu'IB n'est pas câblé, jamais
  codé en dur). `POST /api/broker/connect` tente la vraie connexion →
  **501 honnête** tant qu'IB n'est pas implémenté (aucune fausse
  connexion). (4) **Trades affichés = journal SQL** (`storage_trades.py`,
  table `trades` existante) : `record_trade`/`list_recent_trades` ;
  `/api/positions` sert positions ouvertes (gateway) + trades exécutés
  (SQL) + `broker_connected` — vide et honnête sans broker. (5) **Symboles
  non affichés non rafraîchis** : confirmé — seul `state.activeSymbol` a
  son graphique en mémoire (`state.candles` vidé au changement d'onglet)
  et rafraîchi par tick ; la watchlist ne fait qu'un fetch de prix à la
  demande (rien maintenu en mémoire par symbole). Validé au navigateur
  (bouton grisé, positions « broker déconnecté », badge DÉMO, toast de
  connexion honnête). 110 tests verts.

- **2026-07-20** — **Passe « utilisateur maladroit »** (demande
  explicite : sécuriser contre les erreurs d'usage, hors attaque
  extérieure). Méthode : chaque bêtise a d'abord été REPRODUITE (3 × 500,
  2 valeurs dangereuses acceptées, 2 crashs CLI), puis corrigée, puis
  couverte par un test (92 → 106 verts). Corrections :
  (1) **Config bornée par pydantic** (`Field(ge/gt/le)`) :
  `chart_refresh_seconds: 0` (le front aurait martelé l'API en boucle) et
  `max_position_size: -3` (ordres INVERSÉS en live !) étaient acceptés
  silencieusement — désormais refusés au démarrage ; `max_position_size`
  passé `int → float` au passage (0.5 lot légitime). (2) **Démarrages CLI
  lisibles** : YAML malformé, valeur invalide (champ + valeur reçue
  affichés via `load_settings_or_die`, partagé par les deux scripts) et
  **port déjà occupé** (pré-check socket : « PyEA tourne probablement
  déjà ») = message net, plus de traceback. (3) **`load_history`
  blindé** : fichiers parasites ignorés (`file_year` : suffixe non
  numérique — une copie `_backup.parquet` cassait TOUTE la page backtest
  via `int()`), doublons d'index dédupliqués (copie d'année sous deux
  noms = bougies doublées silencieuses), Parquet corrompu → erreur
  nommant le fichier et le remède, période inversée refusée. (4) **API** :
  erreurs de données → 400/422 avec détail (plus jamais 500) ;
  validateur pydantic « début ≤ fin » sur les requêtes backtest ET
  entraînement. (5) **Téléchargeur** : validation de TOUS les symboles et
  des années AVANT le premier octet téléchargé (une faute de frappe ne
  tue plus un run de plusieurs heures ; message listant les symboles
  supportés). Conséquence assumée : une config invalide EMPÊCHE le
  démarrage (fail-fast choisi contre le clamp silencieux — un logiciel
  de trading ne doit jamais deviner ce que l'utilisateur voulait).

- **2026-07-20** — **Saisie des identifiants broker depuis le dashboard**
  (demande utilisateur : clic sur le badge broker → fenêtre de dialogue,
  identifiants gardés « jusqu'à ce que le serveur soit éteint », étoiles si
  déjà enregistrés). Décisions : (1) **stockage EN MÉMOIRE uniquement**
  (`brokers/broker_credentials.py`, singleton de module au même statut que
  `event_bus`/`web_log_buffer`) — jamais SQLite, disque ou `.env` : c'est
  exactement la sémantique « volatile jusqu'à l'arrêt » voulue, et ça
  respecte la règle « aucun secret dans un fichier versionné ». **On a
  volontairement REFUSÉ de persister** (sinon un mot de passe traînerait sur
  le disque du VPS). (2) **Le mot de passe ne fuit JAMAIS par l'API** :
  `GET /api/broker/credentials` ne renvoie que `configured` + l'identifiant
  (utile pour reconnaître le compte) ; le front masque par des étoiles
  (placeholder) quand `configured`, et **mot de passe vide au PUT = on
  conserve l'existant** (l'utilisateur ne re-saisit pas les étoiles), tandis
  que mot de passe vide sans identifiants préalables = 422. Jamais de mot de
  passe dans les logs non plus. (3) **Badge broker rendu cliquable** dans
  `charts.js` (le header est le seul endroit qui nomme déjà le broker) +
  clé 🔑 quand configuré ; modale en Tailwind pur dans `dashboard.html`
  (zéro nouvelle lib). (4) `broker_credentials_set` ajouté à `/api/status`.
  (5) **Câblage futur préparé** : `InteractiveBrokersGateway.connect()`
  lira `broker_credentials.password` (host/port/client_id restent en
  config) — noté dans le code. Conséquences traitées : docs/architecture.md
  (arbo brokers + endpoints), pas de changement config/.gitignore (rien de
  persisté). Validé au navigateur (Playwright) : ouverture, enregistrement,
  clé sur le badge, réouverture avec étoiles + identifiant prérempli +
  bouton Effacer, zéro erreur console. 11 tests ajoutés (store + API,
  dont fuite du mot de passe vérifiée), **92 verts**.

- **2026-07-20** — Passe d'ergonomie du dashboard live, en s'inspirant des
  terminaux existants (TradingView / MetaTrader 5). Constat : la page Live
  était fonctionnelle mais « nue » face aux logiciels pros. Améliorations,
  toutes conformes à l'architecture (données via `_demo_*`, graphiques dans
  `static/js/`, libs vendorisées, français). (1) **Légende OHLC en
  surimpression** (signature TradingView) : `subscribeCrosshairMove` →
  O/H/L/C + variation intra-bougie colorée ; fige la bougie survolée
  (`state.hovering`), retombe sur la dernière bougie « vivante » après un
  `series.update`. (2) **Watchlist « Market Watch »** : `/api/symbols`
  enrichi de `last` + `change_pct` (helper `_demo_quote`, **même marche
  aléatoire déterministe** que les bougies → prix watchlist == close du
  graphique, testé) ; rendu **mis à jour en place** (structure bâtie une
  fois, seuls prix/variation/pastille changent → pas de flicker, onglet
  actif préservé), rafraîchi toutes les 10 s. (3) **Header en badges**
  (mode PAPER bleu / LIVE ambre = prudence, pastille de connexion broker,
  stratégie) au lieu d'une phrase ; indicateur WS « ● temps réel » vert /
  « ● hors ligne » rouge (aussi sur la page Entraînement). (4) **Sens
  BUY/SELL coloré** (vert/rouge) dans les positions (live) et les trades
  (backtest). (5) **États vides cohérents** : la page Backtest a désormais
  son placeholder « Lancez un backtest… » (parité avec Entraînement) au
  lieu de coquilles vides. Décisions de fond : prix de la watchlist calculé
  côté serveur dans `_demo_quote` (jamais dans le front) pour rester la
  SEULE source des données de démo (le câblage réel ne touchera que les
  `_demo_*`) ; refus d'ajouter un sélecteur de timeframe sur le live (le
  flux ne sert que du M1 démo — ce serait une feature backend, hors passe
  d'ergonomie). Validé au navigateur (Playwright) : légende au survol,
  watchlist chiffrée, badges, zéro erreur console sur les 3 pages. 1 test
  ajouté (cohérence prix watchlist/graphique), **81 verts**.

- **2026-07-19** — Fiabilisation post-retours de test (l'utilisateur n'a
  « même pas pu entraîner »). Cause racine reproduite en navigateur : le
  POST `/api/training/run` chargeait l'historique DANS la boucle asyncio
  (UI entièrement gelée pendant des secondes/minutes) et, après un
  rechargement de page en plein run, le job devenait invisible — bouton
  muet répondant « un entraînement est déjà en cours », sans progression ni
  annulation possible. Décisions : (1) le chargement des données devient la
  **première phase du job** (POST immédiat, phase « Chargement… » visible,
  période invalide/trop courte = échec de job avec message clair — le 404
  « aucun historique » reste synchrone) ; (2) **ré-attachement UI** via
  `GET /api/training/current-job` au chargement de la page ; (3) annulation
  re-vérifiée entre les phases d'un pli ; (4) `fail_orphan_runs()` au
  démarrage (un arrêt serveur laissait des runs « running » à jamais) ;
  (5) labeling triple-barrier en **chunks numpy** (~16×, test d'équivalence
  contre une réimplémentation naïve, départage stop/TP inclus) ;
  (6) `load_history` filtre les fichiers par année de la période ;
  (7) téléchargeur : une année en échec est journalisée et sautée (résumé
  final) au lieu de faire crasher tout le run de plusieurs heures ;
  (8) front : erreurs de validation 422 rendues lisibles (fini
  « [object Object] »), `wss://` si HTTPS, indicateur WS honnête (vide sur
  les pages sans WebSocket), fetch robustes aux erreurs réseau, barre de
  progression non ré-affichée par le message « done ». Validé en
  navigateur (Playwright) : feedback immédiat au clic, reprise après
  reload, annulation, run complet — zéro erreur console. 80 tests verts.

- **2026-07-18** — Scaffold initial (branche `claude/new-session-b0govl`).
  Choix : FastAPI + HTMX/Tailwind/Chart.js via CDN (zéro build front) ;
  REST pour le ponctuel, WebSocket pour le temps réel ; **ib_async**
  retenu contre `ibapi` (trop bas niveau) et `ib_insync` (non maintenu
  depuis 2024, ib_async en est le fork communautaire actif) ; SQLite +
  SQLAlchemy 2.0 (migration Postgres = changer `database_url`).
- **2026-07-18** — Renommage : le package/logiciel s'appelle **PyEA**
  (initialement nommé à tort « Couleuvre » ; Couleuvre est le moteur de
  décision, pas le logiciel). La stratégie garde `couleuvre_v0_1`.
- **2026-07-18** — Convention actée : Couleuvre_v0.1 vit dans
  `strategy_couleuvre_v0_1.py` (préfixe `strategy_` prioritaire sur le
  nom court) — validée par l'utilisateur.
- **2026-07-18** — Ce fichier `CLAUDE.md` devient la mémoire maintenue du
  projet, sur demande de l'utilisateur : le mettre à jour après chaque
  changement notable et s'y référer plutôt qu'au contexte de conversation.
- **2026-07-18** — Données historiques pour le backtest : source
  **Dukascopy** (flux public `datafeed.dukascopy.com`, gratuit, sans
  compte, M1 remontant avant 2010) retenue contre IB (exige TWS connecté
  + limites de débit) et yfinance (pas d'intraday ancien). Granularité
  **M1** stockée en **Parquet** (le backtest ré-échantillonnera H1/D1 au
  besoin). `download_history.py` = 2e commande CLI, exception assumée à
  la règle « une seule commande » (tâche ponctuelle de plusieurs heures,
  hors cycle de vie du serveur). Pièges du flux notés dans le module :
  mois 0-based dans les URLs, prix entiers ÷ 10^facteur (5 forex, 3
  paires JPY/métaux/indices), 404 = week-end/férié/hors historique.
- **2026-07-18** — Bug corrigé : `.gitignore` contenait `data/` non ancré,
  qui ignorait aussi `pyea/data/` et `tests/data/` (du code !) —
  `data_market_feed.py` n'avait jamais été commité. Motifs ancrés en
  `/data/` et `/logs/`. Leçon : ancrer à la racine tout motif visant un
  dossier de données local.
- **2026-07-18** — `resample_history()` ajouté (M1 → M5/M15/M30/H1/H4/
  D1/W1/MN1) : le backtest sur timeframe supérieur part toujours du M1
  stocké. Demande utilisateur : couvrir plusieurs timeframes.
- **2026-07-18** — Libs front passées de CDN à **vendorisées**
  (`static/vendor/` : tailwind.js, htmx.min.js 1.9.12,
  chart.umd.min.js 4.4.3). Raisons : un dashboard de trading doit
  fonctionner sans internet sortant (VPS), versions déterministes, et le
  Chromium de la sandbox ne passait pas par le proxy pour les CDN.
  Toujours zéro build front.
- **2026-07-18** — Docs restructurées à la demande de l'utilisateur : le
  README (trop technique) devient présentation + « Démarrage rapide » ;
  la doc technique part dans `docs/` (architecture, données historiques,
  choix techniques).
- **2026-07-18** — Dashboard refondu sur maquette TradingView fournie par
  l'utilisateur (graphique central, watchlist-onglets à droite avec état
  de trading, positions en bas, P&L total en bas à droite, switch
  Live/Backtest en haut à droite). Ajouts en conséquence :
  `strategy.symbols` et `ui.chart_refresh_seconds` dans config.yaml,
  endpoints `/api/symbols` et `/api/positions`, page `/backtest`
  placeholder, vendorisation de luxon + chartjs-adapter-luxon +
  chartjs-chart-financial (chandeliers). Les logs restent accessibles
  (onglet du panneau bas). Tout le factice est concentré dans les
  fonctions `_demo_*` d'`api_rest.py`, à remplacer au câblage réel.
  **Positions/trades/P&L ne sont JAMAIS simulés** : positions ouvertes =
  gateway (si connectée), trades exécutés = journal SQL (`storage_trades`),
  P&L réel — vide si broker déconnecté. Seul le marché (graphique,
  watchlist) reste démo, signalé « DÉMO ».
- **2026-07-18** — Graphique de prix migré de Chart.js+chartjs-chart-financial
  vers **TradingView Lightweight Charts 4.2.0** (vendorisé), suite à la
  demande « remonter le graphique dans le passé ». Raisonnement : le
  défilement est une capacité de la lib de graphique, pas du framework —
  React/Vite rejeté (build imposé, contraire au principe zéro-build, sans
  résoudre le besoin). Pagination `/api/charts/price-history?before=`
  (secondes epoch), historique démo borné à 3 jours (`has_more`).
  luxon + adaptateur + plugin financier supprimés du vendor ; Chart.js
  conservé pour les futurs graphiques classiques. Le logo TradingView sur
  le graphique = attribution obligatoire (licence Apache 2.0), ne pas
  l'enlever.
- **2026-07-19** — Bouton Trading/Stopped par paire (demande utilisateur).
  Décisions prises : état par symbole **persisté en SQLite** (table
  `symbol_trading_states`, un EA ne doit pas oublier ses paires armées au
  redémarrage) ; **défaut = Stopped** pour toute paire (sécurité : rien ne
  trade sans action explicite) ; `strategy.symbols` **supprimé** de
  config.yaml (redondant — l'interrupteur runtime le remplace,
  `strategy.enabled` reste le kill-switch global : le câblage réel devra
  faire un ET des deux) ; vérification de l'état serveur à chaque
  changement d'onglet ; `window.confirm` avant d'armer en mode live.
  Bug démo corrigé au passage : `_base_price` classait USDJPY/USDCHF/
  USDCAD comme indices (`startswith("US")`).
- **2026-07-19** — Interface de backtest (v1). Décisions : le moteur
  (`pyea/backtest/`) passe par le MÊME flux que le live
  (`Strategy → Signal → RiskManager → OrderRequest`, exécution simulée) —
  c'est l'occasion qui a imposé le flux strict (point de vigilance n°3) et
  motivé l'implémentation v1 de `RiskManager.evaluate`. Exécution
  volontairement simple (bid_close, pas de spread/slippage) à raffiner.
  Endpoint run en `def` sync (threadpool) pour ne pas bloquer la boucle
  asyncio. Chart.js sert enfin (courbe d'équité) — d'où sa conservation.
  Walk-forward/entraînement LightGBM : encore à venir sur cette page.
  Validation navigateur sur historique synthétique local (Dukascopy
  bloqué en sandbox) : 18 mois M1 → H1 en ~15 s, 0 trade (stratégie
  muette), zéro erreur console.
- **2026-07-19** — Infra d'entraînement (direction validée par
  l'utilisateur avant toute ligne de LightGBM). Décisions : **jobs en
  thread + progression par le bus** (Celery/Redis rejeté : EA
  mono-utilisateur local, zéro infra) ; un seul job à la fois ;
  `strategy.train()` ajouté au contrat avec défaut no-op ; artefacts par
  run dans `data/models/<run>/` (`storage.models_dir` en config) ; table
  `training_runs` pour comparer les runs ; le walk-forward teste chaque
  pli via le MÊME `BacktestEngine` que la page backtest. Le bus
  d'événements sert enfin à quelque chose (point de vigilance n°1 —
  `job_manager` est d'ailleurs un singleton de module du même statut).
  Ébauche de features utilisateur consignée et annotée dans
  `docs/strategie_couleuvre.md` — les deux évolutions moteur qui étaient
  PRÉ-REQUIS avant l'entraînement réel (clôture forcée de fin de semaine
  et barrières TP/SL intrabar / triple-barrier) sont désormais **livrées**
  (moteur v2, cf. section backtest). Validation
  navigateur : run 5 plis, 11 trames WS `training.progress`, historique
  persistant, zéro erreur console.
- **2026-07-19** — Moteur de backtest v2 : les deux pré-requis moteur de
  Couleuvre livrés (étape 1 de `docs/strategie_couleuvre.md`). Décisions :
  (1) **barrières TP/SL portées par le domaine** — `Signal.stop_loss`/
  `take_profit` (optionnels) → reportés par le RiskManager sur
  l'`OrderRequest`, testés en **intrabar** (high/low) par le moteur.
  Rationale architecture : un fill de barrière n'est PAS un ordre
  contournant le risque, c'est l'exécution d'un ordre bracket déjà validé
  à l'ouverture (règle #1 respectée) ; en live, ces champs deviendront un
  bracket IB. (2) **Convention conservatrice** : si stop ET take-profit
  sont dans la même bougie, on suppose le **stop** touché d'abord (ordre
  intrabar réel inconnu, on pénalise). (3) **Clôture de fin de semaine**
  détectée par changement de semaine **ISO** entre bougies consécutives
  (le forex Dukascopy n'a pas de bougie le week-end) — robuste aux
  frontières d'année ; garde `entry_time != timestamp` pour éviter un
  aller-retour dégénéré ; ce plafond borne aussi l'horizon 2-5 j. (4)
  Frames sans high/low (tests) : barrières retombent sur le close, donc
  neutres. 6 tests ajoutés (TP/SL long, TP short, stop prioritaire,
  bougie d'entrée exclue, clôture vendredi), 51 tests verts.
- **2026-07-19** — Module features de Couleuvre (étape 2). Décisions :
  (1) **un LightGBM par actif — tranché** (la spec laissait le choix
  ouvert) ⇒ module **mono-symbole**, aucune feature « classe d'actif »
  (inutile) ni cross-asset (DXY/S&P/VIX) ni macro (NFP/CPI), reportées en
  v2 (source externe requise). (2) **Anti-fuite** érigé en invariant
  testable : toutes les fenêtres sont causales (rolling/ewm/shift/diff
  arrière), vérifié par **stabilité par préfixe**
  (`features(frame)[:k] == features(frame[:k])`) — plus robuste qu'une
  relecture visuelle. (3) **Fenêtres = constantes de module**, pas de
  config : elles font partie de la définition du modèle `couleuvre_v0_1`
  (versionnées avec la stratégie), pas des réglages runtime. (4) Zéro
  dépendance TA externe (RSI/ATR/ADX/MACD/Bollinger/stochastique/OBV
  réimplémentés, lissage de Wilder = ewm alpha 1/n) ; `numpy` explicité
  dans requirements. (5) Emplacement `strategies/strategy_couleuvre_*`
  (préfixe = package). (6) Features **scale-invariantes** autant que
  possible (ratios, retours log, MACD/ATR normalisés par le close) pour
  rester comparables entre régimes. 10 tests, 61 verts. Prochaine étape :
  labeling triple-barrier + `CouleuvreV01.train()` (fit LightGBM).
- **2026-07-19** — Couleuvre étapes 3-5 (labeling + train + inférence,
  livrées ensemble). Décisions : (1) **label binaire symétrique** : 1 si la
  barrière haute est touchée avant la basse (barrières symétriques à
  `mult·ATR`). Un seul modèle sert les deux sens (`P(haute d'abord)` haute →
  long, basse → short) au lieu d'en entraîner deux ; barrières identiques
  au labeling et à l'exécution (aucune divergence). (2) **API LightGBM
  native** (`lgb.train`) et non le wrapper sklearn → pas de dépendance
  `scikit-learn` ; AUC in-sample calculée à la main (Mann–Whitney, numpy).
  (3) **Inférence par pré-calcul dans `warmup`** (le moteur passe désormais
  le frame à `warmup`) : plus rapide qu'un calcul incrémental et
  **exactement égal** grâce à la stabilité par préfixe des features
  (précalcul sur tout le frame == calcul sur le seul passé, la décision à
  `t` ne lit que la ligne `t`). (4) **Étapes 3 et 4 livrées ensemble** :
  sans `on_tick`, le walk-forward ne produit aucun trade → rien à valider.
  (5) **Non-fuite prouvée par un test adverse** : entraîné puis backtesté
  OOS sur bruit pur, AUC in-sample ~0,96 mais taux de gain OOS ~50 %
  (`test_pas_de_fuite_pnl_nul_sur_bruit`) — démonstration vivante que seul
  l'OOS juge. (6) **UI** : colonne « AUC IS » par pli en regard du taux de
  gain OOS (l'écart = surapprentissage) — réponse à « comment voir si
  l'entraînement est bon sur la paire » (pas de bouton séparé : le
  walk-forward EST le test). (7) **Labeling dans `strategies/`** (et non
  `training/`) pour éviter un cycle d'import strategies→training, et parce
  que les multiples de barrière font partie de la définition du modèle.
  (8) **Robustesse** : volume à variance nulle → `vol_spike` = 0 (jamais
  NaN, sinon une colonne tout-NaN ferait sauter tout l'entraînement au
  `dropna`) — bug trouvé sur les données synthétiques locales. Validé de
  bout en bout dans le navigateur (EURUSD H1, 3 plis, table OOS + AUC IS,
  zéro erreur console). 12 tests ajoutés, **73 verts**. `lightgbm` doit
  être installé (déjà dans requirements). Reste : gateway IB + feed réels.
- **2026-07-19** — Refonte ergonomique : **entraînement sur sa propre page**
  (`/training`), nav header à 3 entrées (Live | Backtest | Entraînement),
  demandée par l'utilisateur. Décisions : (1) **3 pages séparées** plutôt
  qu'onglets — la page backtest mélangeait deux métiers (run unique vs
  entraînement/validation) ; chaque page a désormais un seul métier, ses
  propres sélecteurs, son propre JS (`backtest.js` scindé → `training.js`).
  (2) **Ajouts page Entraînement** : la **courbe d'équité OOS**
  (`oos_equity_curve` était calculée mais jamais affichée) et un **panneau
  « définition du modèle » en lecture seule**. (3) **Définition servie par
  l'API** (`GET /api/training/definition/{strategy}` →
  `Strategy.model_definition()`, défaut None) plutôt que codée en dur dans
  le template : source unique = les constantes réelles, zéro dérive. (4)
  **Refusé de rendre barrières/seuils tunables dans l'UI** (l'utilisateur a
  suivi) : ce sont la *définition* de `couleuvre_v0_1` (cohérence
  labeling/exécution), et les rendre réglables inciterait à optimiser sur
  l'OOS ; une future `couleuvre_v0_2` versionnée serait la voie propre.
  Conséquence testée : commentaire trompeur corrigé dans
  `test_entrainement_complet` (0 trade = historique sous
  `MIN_TRAIN_SAMPLES`, pas « stratégie muette »). Validé dans le navigateur
  (backtest recentré + entraînement complet, définition affichée, équité
  OOS, zéro erreur console). 2 tests ajoutés, **75 verts**. Reste : gateway
  IB + feed réels (câblage live).
