# LLM.md — Mémoire de travail du projet PyEA (unifié)

> Condensé de `CLAUDE.md` (au 25 juillet 2026). **Ce fichier est la source de
> vérité du contexte projet** : après chaque changement notable, le mettre à
> jour (journal de décisions inclus) plutôt que compter sur la conversation.

## Identité — règle de nommage (importante)

- **PyEA** = le logiciel / la plateforme (serveur web, brokers, risque,
  stockage, package `pyea/`). Toute l'identité visible dit « PyEA ».
- **Couleuvre** = uniquement le **moteur de décision** : stratégie LightGBM
  `couleuvre_v0_1` (`pyea/strategies/strategy_couleuvre_v0_1.py`, classe
  `CouleuvreV01`). Ne jamais nommer le logiciel « Couleuvre ».

## Commandes

```bash
python run_server.py        # serveur web (commande CLI principale)
python download_history.py  # exception assumée : historique M1 Dukascopy
pytest                      # tests (miroir strict du source dans tests/)
```

Config : `config.yaml` (versionné, **prime sur `.env`** — pydantic-settings) +
`.env` (secrets, jamais commité ; modèle `.env.example`).

## Règles d'architecture (non négociables)

1. **Flux strict** : `MarketDataFeed → Strategy → Signal → RiskManager →
   OrderRequest → BrokerGateway`, retour par `ExecutionReport →
   LiveTradingEngine → journal SQL`. Aucune stratégie ne parle au broker ;
   aucun ordre ne contourne le risk manager ; aucun trade sans compte rendu réel.
2. **Câblage uniquement dans `pyea/app_factory.py:create_app()`**.
3. **Bus d'événements** (`core/core_events.py`) : producteurs et consommateurs
   ne se connaissent pas ; le métier n'importe jamais `pyea/api/`. Relais
   WebSocket désabonnés à l'arrêt (`unwire_event_bus`, sinon empilement).
   Abonnés isolés (une erreur ne remonte pas au producteur).
4. **Config centralisée** : tout par `config.config_settings.get_settings()`.
5. **Paper → live** = `broker.trading_mode` dans config.yaml (ports dans `.env`).

## Conventions

- `snake_case` / `PascalCase`, préfixe = package (`strategy_*`, `broker_*`,
  `api_*`, `core_*`, `data_*`, `storage_*`, `live_*`).
- Nouvelle stratégie : `strategy_<nom>.py` + `@register_strategy` + import dans
  `__init__.py`. Nouveau broker : idem avec `@register_gateway`.
- Tests : `tests/<package>/test_<module>.py`, miroir strict.
- **Front zéro-build, un seul scope global** : `<script>` classiques (pas de
  modules ES). `static/js/ui.js` = socle enfermé dans une **IIFE**, n'exposant
  que `window.PyEA` ; les scripts de page déstructurent. Un `const` de page
  homonyme d'une `function` du socle = `SyntaxError` → page ENTIÈREMENT morte,
  et `node --check` ne le voit pas → **toujours vérifier les 3 pages au
  navigateur, console ouverte**. Le `statCard` du socle prend un objet
  d'options (`{colored: true}`).
- Libs front (Tailwind, HTMX, Lightweight Charts, Chart.js) **vendorisées**
  dans `static/vendor/` — jamais de CDN au runtime (VPS sans internet).
- Graphique de prix = TradingView Lightweight Charts (logo = attribution
  obligatoire, ne pas l'enlever) ; Chart.js pour les graphiques classiques.
- Reconnexion WebSocket partagée : `static/js/websocket.js` (backoff 1→30 s).

## Préférences utilisateur

- Répondre et documenter en **français**.
- Après chaque modification : réfléchir aux **conséquences annexes** (config,
  docs, tests, .gitignore, ce fichier).
- **Honnêteté absolue de l'interface** : PyEA ne fabrique JAMAIS de données de
  compte (positions, trades, P&L, connexion) — broker ou journal SQL, sinon
  vide/tirets. Seules les données de MARCHÉ restent démo, **étiquetées « DÉMO »**.
  Jamais de fausse connexion, faux ticket, faux fill, chiffre inventé.
- Config invalide = refus de démarrer (fail-fast, pas de clamp silencieux).

## Environnements

- **Poste utilisateur (Windows)** : recommander **Python 3.11/3.12** — 3.13 a
  cassé `pip install -r requirements.txt` en silence (`lightgbm`/`pyarrow` sans
  wheel). `MetaTrader5` est marqué `sys_platform == "win32"`. backtrader est
  **vendorisé dans `lib/`** (hors pip, ne pas l'y ajouter).
- **Sandbox de dev** : Dukascopy bloqué (503) → téléchargeur validé par tests
  seulement, premier run réel à vérifier chez l'utilisateur.

## Données historiques (backtest)

- Layout : `data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet` (M1 natif
  bid/ask OHLC + volume, UTC). Source Dukascopy (mois 0-based dans les URLs,
  prix ÷10^facteur, 404 = week-end/férié).
- `load_history(data_dir, symbol, start, end)` = lecture (blindé : fichiers
  parasites ignorés, doublons dédupliqués). `resample_history(frame, "H1")` =
  conversion (M1→…→MN1).

## État du projet (au 25 juillet 2026)

**Complet de bout en bout, ~172 tests verts.** Reste à valider chez
l'utilisateur : premier run réel Dukascopy, connexion TWS/MT5, flux live réel.

- **Dashboard** : 3 pages (Live | Backtest | Entraînement). Live : chandeliers
  M1 Lightweight Charts (pan/zoom, `?before=`, légende OHLC crosshair),
  watchlist Market Watch (prix/variation, pastille = paire armée), bouton
  Trading/Stopped par paire (SQLite, défaut Stopped, 409 si broker déconnecté),
  header en badges (mode, broker cliquable → fenêtre de connexion, stratégie),
  panneau compte (`GET /api/account` : équité/marge + perte du jour vs plafond).
- **Brokers — deux gateways COMPLÈTES** (connexion, compte, routage, flux,
  comptes rendus) :
  - **IB** (`ib_async`, import paresseux) : `connectAsync` sans login/mdp (TWS
    authentifie) ; `place_order` = bracket natif (parent Market + Limit TP +
    Stop SL, OCA côté TWS, `transmit=True` sur le dernier) ; push `reqMktData` ;
    forex/métaux 6 lettres via `Forex()` (indices = erreur claire, symbole sauté).
  - **MT5** (`MetaTrader5`, attach à un terminal ouvert) : `TRADE_ACTION_DEAL`
    avec SL/TP natifs, filling IOC/FOK déduit, `magic` PyEA ; flux par
    **scrutation** 0,25 s (dédup `time_msc`, mid bid/ask, IPC déporté dans un
    exécuteur — tous les appels bloquants passent par `_call`) ; exécutions
    relues via `history_deals_get` toutes les 2 s (dédup ticket).
  - `is_connected()` synchrone mémorisé 2 s. Déconnecté → `ConnectionError`
    explicite, jamais de faux id/tick. Bascule de broker à chaud via la fenêtre
    (déconnexion requise, config non réécrite).
- **Flux live** (`pyea/live/`) : `MarketDataFeed` (agnostique, relaie sur bus
  `market.tick`), `LiveTradingEngine` (consommateur du bus, flux strict, une
  stratégie par symbole, gating : kill-switch `strategy.enabled` + paire armée
  + broker connecté, **registre d'ordres en vol** par symbole —
  `INFLIGHT_TIMEOUT_SECONDS=60`), `LiveRuntime` (démarré à la connexion broker).
  Chemin retour : `ExecutionReport` + `set_execution_callback` ; P&L calculé
  par le broker (sorties), colonne `pnl` au journal, `/api/positions` distingue
  latent/réalisé.
- **Inférence live Couleuvre** : `CandleAggregator` (ticks → bougies alignées
  `Timestamp.floor`, émises au passage de bucket, M1→D1), `resolve_live_model`
  (dernier run `completed` → modèle du dernier pli ; aucun modèle → paire
  muette), `warmup(live=True)` (tampon glissant 400 bougies + historique).
  Équivalence live/backtest ~99,5 % (résiduel = indicateurs récursifs, assumé).
- **Backtest** : adossé à **backtrader vendorisé** (`lib/backtrader/`, GPLv3
  OK usage perso). Modèle : entrée Market cheat-on-close, barrières TP/SL =
  Stop+Limit OCO au prix exact, stop prioritaire si les deux franchies, clôture
  forcée fin de semaine ISO + liquidation finale, bougie fantôme, 1 unité
  nominale re-scalée par `max_position_size`, `engine.run` synchrone.
  **Coûts modélisés** : spread MESURÉ (médiane `ask_close − bid_close`),
  COMM_FIXED par côté, commission optionnelle `costs.commission_per_unit` ;
  stats NETTES + `gross_pnl`/`total_costs` ; sans colonnes ask →
  `costs_modelled: false` + bandeau ambre « OPTIMISTE ».
- **Entraînement** (`/training`) : walk-forward à fenêtre expansive (jamais de
  split aléatoire), job de thread unique annulable, progression WS
  `training.progress` + reprise après reload (`/api/training/current-job`),
  `fail_orphan_runs` au démarrage, historisé SQLite (`training_runs`) +
  artefacts `data/models/<run>/fold_<i>/`. **Agrégation honnête** : profit
  factor ET win rate OOS recalculés sur TOUS les trades (jamais de moyenne de
  ratios par pli) ; Sharpe/SQN par pli seulement ; colonne AUC IS vs taux OOS
  (écart = surapprentissage) ; chauffe OOS récupérée (`OOS_CONTEXT_BARS=300`
  bougies de contexte, jamais rejouées). Définition du modèle servie par
  `GET /api/training/definition/{strategy}` (barrières/seuils NON tunables dans
  l'UI — c'est la définition de `couleuvre_v0_1` ; une évolution = `v0_2`).
- **Couleuvre v0.1** (spec : `docs/strategie_couleuvre.md` — swing intra-semaine
  2-5 j, un LightGBM par actif) :
  - `strategy_couleuvre_features.py` : 34 features causales (`FEATURE_COLUMNS`,
    ordre figé, fenêtres = constantes de module), anti-fuite prouvée par
    **stabilité par préfixe**, zéro dépendance TA (Wilder = ewm α=1/n).
  - `strategy_couleuvre_labeling.py` : triple-barrier ATR, label binaire
    symétrique (1 = haute d'abord), **barrière basse prioritaire en départage
    intrabar** (aligné sur le moteur), fenêtre avant incomplète → NaN. Horizon
    via `_horizon_ticks()` (⚠ pandas 3 : index en microsecondes, `asi8` n'est
    PAS en ns — le bug rendait la barrière verticale 1000× trop longue).
  - `train` : features + labels + `dropna`, `lgb.train` natif (pas de sklearn),
    `model.txt` + `features.json` par pli, AUC IS calculée à la main.
  - `on_tick` : proba → seuils 0.55/0.45 → ENTER_LONG/SHORT avec mêmes
    barrières ATR. Non-fuite prouvée : sur bruit, AUC IS ~0,96 / OOS ~50 %.
- **RiskManager.evaluate v2** : HOLD ignoré, EXIT jamais bloqué, entrées à
  taille fixe sous 3 limites : `max_positions_per_symbol`, `max_open_positions`
  (exposition globale — les deux séparés), `max_daily_loss_pct` (perte
  journalière, repère UTC persisté dans `daily_equity` ; **garde LIVE
  uniquement**, non modélisée en backtest — assumé).
- **SQLite** : micro-migration `_add_missing_columns()` (ALTER TABLE des
  colonnes nullable manquantes, idempotent). Cache mémoire pour
  `is_trading_enabled` (pas de SELECT par tick).

## Points de vigilance

- Singletons de module (`event_bus`, `web_log_buffer`, `broker_runtime`,
  `live_runtime`, `job_manager`) non injectés — cohérent tant que les tests n'exigent pas des bus isolés.
- `broker_credentials.py` volontairement sans appelant (réserve future).
- Le téléchargeur annule les tâches restantes d'une année en échec
  (try/finally) ; ne pas réintroduire de lancement en masse sans annulation.
- Coroutines planifiées depuis callbacks synchrones ib_async : retenir la
  référence (le GC annulait les ticks en plein vol — corrigé).
- Horodatages SQLite : sérialisés avec fuseau (UTC) — ne pas revenir à des
  timestamps naïfs relus en heure locale.
- `_demo_quote` mémorisé (`_demo_closes`, LRU par minute) — valeurs identiques,
  ne pas dé-optimiser.

## Journal de décisions (condensé, antichronologique)

- **2026-07-25** — Refonte front partagée : `ui.js` en IIFE/`window.PyEA`,
  `training.js`/`backtest.js` migrés (helpers dupliqués supprimés), bandeau
  d'état sur les 3 pages, `rememberForm`. Leçon : `node --check` ne voit pas
  les collisions de scope → vérifier au navigateur. 172 tests verts.
- **2026-07-25** — Spread mesuré + commission dans le backtest (le trou « un
  aller-retour gratuit » colmaté : colonnes `ask_*` enfin lues). Mesuré : 1 pip
  de spread ≈ −11 % de P&L brut ; PF 3,37 → 2,88 sur synthétique.
- **2026-07-25** — Audit 20 points (suite) : chauffe OOS récupérée,
  `unwire_event_bus`, cache `is_trading_enabled`, annulation téléchargeur,
  panneau compte `/api/account`, reconnexion WebSocket, priorité YAML > .env
  documentée, code mort supprimé (`SignalRecord`, `TOPIC_LOG`/`TOPIC_EA_STATUS`,
  `ib_account_id`), `_demo_quote` mémorisé, `MetaTrader5` décommenté (marqueur
  win32).
- **2026-07-25** — Audit (8 premiers points) : ordres dupliqués (registre en
  vol), fills journalisés (chemin retour `ExecutionReport`), IPC MT5 déportés,
  bus isolé, perte journalière max implémentée, `max_open_positions` scindé,
  départage intrabar aligné, labels de queue supprimés. Bugs découverts :
  `TickData` non importé (IB), coroutines GC, **`asi8` pandas 3 en µs**
  (horizon 1000× trop long), timestamps SQLite sans fuseau.
- **2026-07-21** — Inférence live Couleuvre (étape 5 : agrégateur + sélection
  modèle par actif, équivalence ~99,5 %). Flux live COMPLET.
- **2026-07-21** — MT5 COMPLET (étape 4) puis IB COMPLET (étape 3 : bracket
  natif, push). Étapes 1-2 : `connect()` IB + backbone `pyea/live/`.
- **2026-07-21** — Win rate OOS agrégé honnêtement ; profit factor OOS
  persisté + micro-migration SQLite ; métriques backtrader sur la page
  Entraînement.
- **2026-07-20** — Moteur maison remplacé par **backtrader vendorisé**
  (vectorbt écarté : non vendorisable ; backtesting.py écarté : fill à la
  bougie suivante casse les barrières). Fidélité prouvée bougie à bougie.
- **2026-07-20** — MT5 ajouté (2e broker) + liste déroulante ; login/mdp
  retirés (TWS/terminal authentifient) ; passe « honnêteté de l'interface »
  (badge DÉMO, `_demo_positions` supprimé, bouton Trading grisé déconnecté) ;
  passe « utilisateur maladroit » (config bornée pydantic, CLI lisible,
  `load_history` blindé, erreurs 400/422 au lieu de 500).
- **2026-07-19** — Fiabilisation entraînement (chargement dans le job, reprise
  UI, labeling numpy ~16×) ; Couleuvre étapes 2-5 (features, labeling, train,
  inférence, non-fuite prouvée sur bruit) ; moteur v2 (barrières intrabar, stop
  prioritaire, clôture week-end ISO) ; page Entraînement dédiée ; bouton
  Trading/Stopped par paire.
- **2026-07-18** — Scaffold (FastAPI, HTMX/Tailwind, SQLite/SQLAlchemy 2.0,
  ib_async retenu) ; renommage PyEA ; CLAUDE.md devient la mémoire ; Dukascopy
  M1 Parquet ; bug `.gitignore` (`data/` non ancré → ancrer `/data/`) ;
  vendorisation front ; dashboard façon TradingView ; Lightweight Charts.
