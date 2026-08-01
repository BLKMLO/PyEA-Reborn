# LLM.md — Mémoire de travail du projet PyEA (unifiée)

> **Ce fichier est la source de vérité unique du contexte projet, pour TOUTES
> les IA** (Claude, Kimi, etc.). `CLAUDE.md` et `AGENTS.md` ne sont plus que
> des redirections vers ce fichier — ne pas y écrire, pour éviter que les
> contextes divergent. Règle de maintenance : **après chaque changement
> notable, mettre à jour ce fichier** (journal de décisions inclus) plutôt que
> de compter sur la mémoire de conversation.

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
- **⚠ Tests sensibles à l'environnement** : certains tests lisent le vrai
  `config.yaml` (instruments attendus : XAUUSD, US500…) et supposent le paquet
  `MetaTrader5` absent (attendent 503, pas 502). Sur le poste utilisateur
  (config allégée, MT5 installé), 4 tests d'API échouent pour ces raisons
  d'environnement — pas des bugs de code. À isoler un jour.

## Données historiques (backtest)

- Layout : `data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet` (M1 natif
  bid/ask OHLC + volume, UTC). Source Dukascopy (mois 0-based dans les URLs,
  prix ÷10^facteur, 404 = week-end/férié).
- `load_history(data_dir, symbol, start, end)` = lecture (blindé : fichiers
  parasites ignorés, doublons dédupliqués). `resample_history(frame, "H1")` =
  conversion (M1→…→MN1).
- Téléchargeur : **concurrence basse** (défaut 3, `--concurrency`) — au-delà,
  Dukascopy répond **429** ; celles-ci sont traitées avec un backoff LONG
  (`Retry-After` honoré, sinon 5 s × tentative, jusqu'à 5 retries). Le backoff
  court 1-2-4 s des autres erreurs est inadapté à une limite de débit.

## État du projet (au 30 juillet 2026)

**Complet de bout en bout, 174 tests verts** (+1 échec environnemental connu
sur le poste Windows : MT5 installé, cf. Environnements). Reste à valider chez
l'utilisateur : premier run réel Dukascopy, connexion TWS/MT5, flux live réel,
et les 3 pages au navigateur (sélecteur d'unité de temps + suppression de
runs — pas de navigateur outillé ici).

- **Dashboard** : 3 pages (Live | Backtest | Entraînement). Live : chandeliers
  Lightweight Charts (pan/zoom, `?before=`, légende OHLC crosshair), **unité
  de temps M1→D1 agrégée côté client** (le serveur ne sert que du M1 ;
  plancher UTC partagé, choix mémorisé `live:timeframe`, marqueurs de trades
  re-planchés sur le bucket), watchlist Market Watch (prix/variation,
  pastille = paire armée), bouton Trading/Stopped par paire (SQLite, défaut
  Stopped, 409 si broker déconnecté), header en badges (mode, broker cliquable
  → fenêtre de connexion, stratégie), panneau compte (`GET /api/account` :
  équité/marge + perte du jour vs plafond).
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
  forcée fin de semaine ISO + liquidation finale, bougie fantôme, **capital de
  départ réel** (`backtest.initial_capital`, défaut 10 000 — la taille réelle
  `max_position_size` est tradée, plus de re-scaling post-hoc ; la courbe
  d'équité est la **valeur du compte**), `engine.run` synchrone (param
  `model_path` → transmis à `warmup`, `warmup` reçoit aussi `"symbol"`).
  Stats = métriques de compte : `initial_capital`, `final_equity`,
  `return_pct`, `max_drawdown` (absolu) + `max_drawdown_pct`, P&L net, taux
  de gain, profit factor, trade moyen, Sharpe. **La page backtest charge le
  modèle**
  (dernier run réussi de la paire, dernier pli — règle `resolve_live_model`
  partagée avec le live) ; sans run → `model: null` + bandeau « stratégie
  muette », mismatch de timeframe = bandeau ambre.
  **Coûts modélisés** : spread MESURÉ (médiane `ask_close − bid_close`),
  COMM_FIXED par côté, commission optionnelle `costs.commission_per_unit` ;
  stats NETTES + `gross_pnl`/`total_costs` ; sans colonnes ask →
  `costs_modelled: false` + bandeau ambre « OPTIMISTE ».
- **Entraînement** (`/training`) : walk-forward à fenêtre expansive (jamais de
  split aléatoire), job de thread unique annulable, progression WS
  `training.progress` + reprise après reload (`/api/training/current-job`),
  `fail_orphan_runs` au démarrage, historisé SQLite (`training_runs`) +
  artefacts `data/models/<run>/fold_<i>/`. **Mode poolé (v0_2)** :
  `TrainingRunRequest.symbols` (liste, ou omis = tous les actifs ayant un
  historique) ; `run_walkforward_pooled` (split sur la plage commune, UN
  modèle par pli, un backtest OOS par actif, agrégats honnêtes + ventilation
  `oos_by_symbol`, Sharpe/SQN = None en poolé) ; run enregistré sous la
  sentinelle **`POOLED_RUN_SYMBOL = "ALL"`** ; 400 si stratégie non
  `pooled` avec plusieurs symboles ; stratégie par défaut `couleuvre_v0_2`. **Suppression d'un run** :
  `DELETE /api/training/runs/{id}` (ligne SQL + `rmtree` des artefacts, garde
  anti-chemin bidouillé ; 404 inconnu, 409 « running ») + bouton ✕ dans le
  tableau des runs (confirm JS ; live/backtest retombent sur le run précédent
  via `resolve_live_model`, sans cache). **Agrégation honnête** : profit
  factor ET win rate OOS recalculés sur TOUS les trades (jamais de moyenne de
  ratios par pli) ; Sharpe/SQN par pli seulement ; colonnes **AUC IS vs AUC
  OOS par pli** (l'écart mesure directement le surapprentissage — AUC OOS via
  le hook `Strategy.oos_auc(frame, test_index)`, None pour une stratégie sans
  modèle ; pas d'agrégat inter-plis, comme Sharpe/SQN) ; chauffe OOS récupérée
  (`OOS_CONTEXT_BARS=300` bougies de contexte, jamais rejouées). Définition du
  modèle servie par
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
    **early stopping** (patience 30) sur la queue du bloc (`_VALIDATION_FRACTION
    = 0,15`, causale — jamais dans l'OOS) : sans elle, les 300 arbres
    mémorisaient le bruit (AUC IS 0,94 / OOS 0,52 mesuré). `n_trees` retenu au
    rapport. `model.txt` + `features.json` par pli, AUC IS calculée à la main.
  - `on_tick` : proba → seuils 0.55/0.45 → ENTER_LONG/SHORT avec mêmes
    barrières ATR. Non-fuite prouvée : sur bruit, AUC IS ~0,96 / OOS ~50 %.
- **Couleuvre v0.2** (spec : `docs/strategie_couleuvre.md` — **modèle UNIQUE
  mutualisé multi-actifs**, décision « un modèle par actif » révoquée le
  2026-07-31 ; v0_1 figée conservée) :
  - `strategy_couleuvre_v0_2.py` (`CouleuvreV02`) : réutilise features (34,
    sans échelle) et labeling de v0_1 par import ; **`train`/`oos_auc`
    acceptent `dict[str, DataFrame]` ou un DataFrame seul** ; features+labels
    calculés PAR symbole avant concaténation ; 35e feature **`symbol`
    catégorielle native** (codes figés via `pd.Categorical.from_codes`,
    catégories persistées `features.json["symbol_categories"]`, inconnu →
    code −1). Piège corrigé : index dupliqués entre actifs → `_concat_sorted`
    (tri positionnel stable, jamais `.loc` sur étiquettes dupliquées).
  - **Seuils plus sélectifs 0.60/0.40**, H4 recommandé, barrières inchangées
    (1,5×ATR/5 j) ; early stopping causal par symbole (queue 15 % de chacun).
  - `resolve_live_model` : run du symbole d'abord, **repli sur le run `ALL`**
    — le même modèle sert tous les symboles en live comme en backtest.
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
  `live_runtime`, `job_manager`) non injectés — cohérent tant que les tests
  n'exigent pas des bus isolés.
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

- **2026-07-31 — Modèle unique multi-actifs (`couleuvre_v0_2`) + backtest
  « compte » (3 demandes utilisateur)**. **201 tests verts** (+27), seul
  échec = l'environnemental connu (MT5 installé → 502 vs 503 attendu). À
  vérifier côté utilisateur : les 3 pages au navigateur (pas de node/navigateur
  outillé ici) et un vrai run poolé sur données réelles.
  (1) **Backtest capital réel** : `backtest.initial_capital` (défaut 10 000) ;
  `BacktestEngine` trade la taille réelle (re-scaling post-hoc et
  `_NOMINAL_CASH` supprimés), courbe d'équité = **valeur du compte** ;
  stats += `initial_capital`/`final_equity`/`return_pct`/`max_drawdown_pct`
  (`_max_drawdown` → tuple) ; `warmup` reçoit `"symbol"`. UI : cartes
  Capital initial/final, Rendement %, P&L net, DD max (%+montant), Taux de
  gain, PF, Trades, Trade moyen, Sharpe, Coûts, Bougies (SQN retiré de la
  page, grille 6 colonnes).
  (2) **`couleuvre_v0_2`** : UN LightGBM mutualisé (feature `symbol`
  catégorielle, catégories persistées, `from_codes`), `train`/`oos_auc`
  acceptent dict ou DataFrame, seuils **0.60/0.40**, H4 conseillé, v0_1
  figée. Piège : index dupliqués inter-actifs → `_concat_sorted` (jamais
  `.loc` sur étiquettes dupliquées = explosion cartésienne).
  (3) **Walk-forward poolé** : `run_walkforward_pooled` (plage commune,
  actif disjoint écarté, UN modèle/pli, backtest OOS par actif, equity =
  somme des P&L, Sharpe/SQN=None en poolé, `oos_by_symbol`) ; API
  `symbols` (omis = tous) ; sentinelle `POOLED_RUN_SYMBOL="ALL"` ;
  `resolve_live_model` : run du symbole puis **repli `ALL`** ; UI
  entraînement : symbole désactivé « Tous les actifs » pour stratégie
  `pooled`, défaut v0_2/H4, colonne « TOUS », bloc « Par actif ».
  Docs à jour (`strategie_couleuvre.md` §v0.2, architecture, choix_techniques).
- **2026-07-30** — **Trois ajouts interface** (travail repris en cours de
  route) : (1) **Sélecteur d'unité de temps M1→D1** sur la page Live :
  le serveur ne sert que du M1, l'agrégation est faite côté client dans
  `charts.js` (`CHART_TIMEFRAMES`, plancher UTC — même règle que le resample
  serveur ; `state.m1Candles` = brut, `state.candles` = vue agrégée, refresh
  incrémental par bucket, fetch dimensionné à ~180 bougies affichées plafonné
  à 15 j de M1, marqueurs de trades re-planchés sur le bucket — un horodatage
  à la seconde ne tombe jamais sur une bougie, Lightweight Charts exige
  l'égalité). Choix mémorisé (`live:timeframe`). (2) **Suppression d'un run
  d'entraînement** : `delete_run` (storage, refuse « running ») +
  `DELETE /api/training/runs/{id}` (404/409, `rmtree` des artefacts gardé
  sous `models_dir`) + bouton ✕ par ligne du tableau des runs (confirm,
  délégation d'événement). 2 tests ajoutés. (3) **Bandeau backtest « modèle
  abstinent »** : modèle chargé mais 0 trade (early stopping → probas jamais
  au-delà des seuils) expliqué en ambre, pour ne pas lire la courbe plate
  comme un bug.
- **2026-07-26 (soir)** — **Trois corrections demandées par l'utilisateur** :
  (1) **Early stopping** dans `CouleuvreV01.train` (patience 30 sur la queue
  causale du bloc, 15 %) — effet MESURÉ sur EURUSD H1 réel : arrêt à **2
  arbres**, AUC IS 0,94 → 0,65, AUC OOS 0,52 → 0,49, signaux 63,7 % → **0 %**
  des bougies. Verdict honnête : il n'y a PAS d'edge exploitable dans ces
  features sur cette paire/timeframe ; l'early stopping ne fait que le révéler
  (le modèle s'abstient au lieu de perdre le spread). Le travail d'edge est du
  ressort d'une `couleuvre_v0_2` (timeframe, seuils, features, labels).
  (2) **Téléchargeur 429** : concurrence 8 → 3 (`--concurrency`), 429 traités
  avec backoff long (`Retry-After` honoré, sinon 5 s × tentative, 5 retries).
  (3) **Page backtest qui restait plate** : elle ne chargeait aucun modèle →
  `engine.run` accepte `model_path` et l'endpoint résout le dernier run réussi
  de la paire (`resolve_live_model`, même règle que le live) ; bandeau modèle
  dans l'UI (run/pli/timeframe, ambre si mismatch de timeframe ou modèle
  absent). `test_api_backtest` : base isolée (sinon les runs réels de la
  machine rendaient le test « stratégie muette » dépendant de
  l'environnement) ; le test d'équivalence live/backtest utilise désormais un
  frame à composante prévisible (sinus) — sur bruit pur, l'early stopping
  laisse le modèle sans conviction (plus aucun signal à comparer).
  Au passage, test flaky débusqué : `latest_completed_run` triait par
  `created_at` seul — or l'horloge Windows (~15 ms) donne le même tick à deux
  insertions rapides → départage `rowid DESC` ajouté (idem `list_runs`).
- **2026-07-26** — **LLM.md devient la mémoire unique pour toutes les IA**
  (demande utilisateur, pour éviter que les contextes se chevauchent) :
  reprise du condensé unifié (228 lignes, commit `ec624df`), `CLAUDE.md` et
  `AGENTS.md` réduits à des redirections. Le journal détaillé d'origine
  (95 Ko) reste accessible dans l'historique git (`git show 3352cf6:CLAUDE.md`)
  mais n'est plus maintenu — ce fichier est désormais le seul à jour. Poste
  réaligné sur `origin/main` (modifs locales à `config.yaml`,
  `download_history.py`, `requirements.txt` écartées). Constat environnement :
  4 tests d'API échouent sur le poste Windows pour raisons externes (config
  locale allégée + paquet MetaTrader5 présent) — cf. section Environnements.
- **2026-07-26** — **AUC OOS affichée par pli** (suite du diagnostic du jour) :
  nouveau hook `Strategy.oos_auc(frame, test_index)` (défaut None), implémenté
  par Couleuvre (features/labels sur chauffe+test, restreints au bloc de test),
  câblé dans `run_walkforward` (champ `WalkForwardFold.oos_auc` → metadata.json)
  et colonne « AUC OOS » sur la page Entraînement en regard de l'AUC IS. Pas
  d'agrégat inter-plis (même règle que Sharpe/SQN). 4 tests ajoutés.
  **Rejets RiskManager passés en DEBUG** : avec `max_positions_per_symbol=1`
  et une stratégie qui signale sur > 60 % des bougies, chaque bougie d'une
  position ouverte produisait une ligne INFO (~81 % des signaux rejetés, des
  milliers de lignes par backtest). Rejets = comportement voulu (pas de bug,
  aucun effet sur le P&L : le premier signal après chaque clôture est bien
  exécuté) — la perte journalière, elle, reste en WARNING. Vérification
  navigateur de la page Entraînement à faire côté utilisateur (pas de
  navigateur outillé sur le poste).
- **2026-07-26** — **Diagnostic « résultats OOS médiocres » tranché** (demande
  utilisateur). Reproduction exacte du pli 1 du run 204826 (13 526 échantillons,
  AUC IS 0,9391 — pipeline cohérent, PAS de divergence train/backtest), puis
  mesure de ce que l'UI ne calcule pas : **AUC OOS = 0,518** sur EURUSD H1
  2024-H1. Conclusion : (1) l'edge OOS existe mais est MINUSCULE (signaux
  0,55/0,45 : 52,9 % / 51,3 % de réussite réelle contre 50,5 % de base) ;
  (2) les probas OOS sont DÉCALIBRÉES par le surapprentissage (étalées
  0,15–0,92 alors que la réalité reste 0,47–0,54 ; le dernier décile fait même
  moins bien que le 9e) → les seuils déclenchent sur **63,7 % des bougies**,
  d'où ~500 trades/pli et un coût de spread (~0,7–1,3 pt de win rate requis
  pour breakeven) qui dépasse l'edge ; (3) l'AUC IS 0,94 / accuracy 86 % n'est
  que mémorisation (LightGBM sur 13 k échantillons). Pistes hiérarchisées :
  ajouter AUC OOS au rapport d'entraînement (diagnostic immédiat), early
  stopping sur queue de train (coupe le surapprentissage), recul des seuils ou
  calibration (Platt/isotonique), timeframe H4 (moins de trades, spread
  relativement plus petit). Toute modif des seuils/barrières = `couleuvre_v0_2`
  (règle de versionnement).
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
  ib_async retenu) ; renommage PyEA ; le fichier de contexte IA devient la
  mémoire du projet (désormais `LLM.md`) ; Dukascopy M1 Parquet ; bug
  `.gitignore` (`data/` non ancré → ancrer `/data/`) ; vendorisation front ;
  dashboard façon TradingView ; Lightweight Charts.
