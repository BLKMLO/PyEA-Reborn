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
  registres stratégie/broker, SQLAlchemy (SQLite), 21 tests verts.
- Dashboard live façon TradingView : chandeliers M1 au centre
  (**TradingView Lightweight Charts** : pan/zoom natifs, historique
  paginé via `?before=`, refresh incrémental `series.update` qui
  préserve le défilement), watchlist à droite (clic = onglet, pastille
  verte = paire armée), **bouton Trading (vert) / Stopped (rouge)** par
  paire à côté du titre du graphique — état par symbole persisté en
  SQLite (`storage_trading_state.py`, défaut = Stopped), relu à chaque
  changement d'onglet (`GET /api/trading/{symbol}`), bascule via
  `PUT /api/trading/{symbol}`, confirmation JS si mode live,
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
  gain, drawdown max), courbe d'équité (Chart.js) et table des trades.
  Moteur : `pyea/backtest/backtest_engine.py` — rejoue bougie par bougie
  via le flux complet `Strategy → Signal → RiskManager → OrderRequest`
  (exécution simulée, **modèle v2** : décision prise au bid_close, pas de
  spread ni slippage, **barrières TP/SL testées en intrabar** high/low sur
  chaque bougie suivante — stop supposé prioritaire si les deux sont dans
  la même bougie —, **clôture forcée à la dernière bougie de la semaine
  ISO** (jamais de portage week-end), liquidation en fin de période,
  courbe d'équité ≤ 500 points). Les barrières transitent par le domaine :
  `Signal.stop_loss`/`take_profit` → RiskManager → `OrderRequest`. Frames
  sans high/low (tests) : barrières neutralisées sur le close. Sur cette
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
  (`training_jobs.py`, un seul job actif à la fois, annulable),
  progression **temps réel** via bus topic `training.progress` → WebSocket
  (+ polling `GET /api/training/jobs/{id}` en secours). Chaque run est
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
- **Squelettes vides** (NotImplementedError) à développer plus tard :
  `InteractiveBrokersGateway` (appels ib_async réels), `MarketDataFeed`.

## Points de vigilance (audit modularité 2026-07-18)

Le graphe d'imports est sain (aucun module métier n'importe `api/`,
dépendances uniquement vers `core`/`config`, lecture env/YAML confinée à
`config_settings.py`). Trois points à surveiller, pas à corriger :

1. `event_bus` et `web_log_buffer` sont des singletons de module, pas
   injectés par `create_app()` (incohérent avec `MarketDataFeed` qui
   reçoit son bus). Si les tests exigent un jour des bus isolés, les
   faire passer par `app_factory`.
2. `/api/status` code en dur `broker_connected: False` — le vrai câblage
   devra exposer la gateway via `app.state`, jamais par import direct
   dans `api_rest`.
3. Le `lifespan` de `app_factory` ne monte pas encore gateway + stratégie
   + feed : c'est au premier câblage complet que le flux
   `Signal → RiskManager → OrderRequest` devra être imposé (aucun
   raccourci stratégie→broker, même « pour tester »).

## Journal de décisions

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
