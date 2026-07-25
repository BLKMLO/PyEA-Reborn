# AGENTS.md — Contexte projet PyEA

> Synthèse de `CLAUDE.md` (au 25 juillet 2026), destinée aux agents IA qui
> lisent `AGENTS.md` (ex. Kimi Code). `CLAUDE.md` reste la mémoire détaillée
> du projet (journal de décisions complet).

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

Config : `config.yaml` (versionné, **prime sur `.env`**) + `.env` (secrets,
jamais commité ; modèle `.env.example`).

## Règles d'architecture (non négociables)

1. **Flux strict** : `MarketDataFeed → Strategy → Signal → RiskManager →
   OrderRequest → BrokerGateway`, retour par `ExecutionReport →
   LiveTradingEngine → journal SQL`. Aucune stratégie ne parle au broker ;
   aucun ordre ne contourne le risk manager ; aucun trade sans compte rendu réel.
2. **Câblage uniquement dans `pyea/app_factory.py:create_app()`**.
3. **Bus d'événements** (`core/core_events.py`) : producteurs et consommateurs
   ne se connaissent pas ; le métier n'importe jamais `pyea/api/`. Relais
   WebSocket désabonnés à l'arrêt (`unwire_event_bus`). Abonnés isolés (une
   erreur ne remonte pas au producteur).
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
  dans `static/vendor/` — jamais de CDN au runtime.
- Graphique de prix = TradingView Lightweight Charts (logo = attribution
  obligatoire) ; Chart.js pour les graphiques classiques. Reconnexion
  WebSocket partagée : `static/js/websocket.js`.

## Préférences utilisateur

- Répondre et documenter en **français**.
- Après chaque modification : réfléchir aux **conséquences annexes** (config,
  docs, tests, .gitignore, fichiers de contexte IA).
- **Honnêteté absolue de l'interface** : PyEA ne fabrique JAMAIS de données de
  compte (positions, trades, P&L, connexion) — broker ou journal SQL, sinon
  vide/tirets. Seules les données de MARCHÉ restent démo, **étiquetées « DÉMO »**.
  Jamais de fausse connexion, faux ticket, faux fill, chiffre inventé.
- Config invalide = refus de démarrer (fail-fast, pas de clamp silencieux).

## Environnements

- **Poste utilisateur (Windows)** : recommander **Python 3.11/3.12** — 3.13 a
  cassé l'install en silence (`lightgbm`/`pyarrow` sans wheel). `MetaTrader5`
  est marqué `sys_platform == "win32"`. backtrader est **vendorisé dans
  `lib/`** (hors pip, ne pas l'y ajouter).
- **Sandbox de dev** : Dukascopy bloqué (503) → téléchargeur validé par tests
  seulement, premier run réel à vérifier chez l'utilisateur.

## Données historiques (backtest)

- Layout : `data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet` (M1 natif
  bid/ask OHLC + volume, UTC). Source Dukascopy (mois 0-based dans les URLs,
  prix ÷10^facteur, 404 = week-end/férié).
- `load_history(...)` = lecture (blindée) ; `resample_history(frame, "H1")` =
  conversion (M1→…→MN1).

## État du projet (au 25 juillet 2026)

**Complet de bout en bout, ~172 tests verts.** Reste à valider chez
l'utilisateur : premier run réel Dukascopy, connexion TWS/MT5, flux live réel.

- **Dashboard** : 3 pages (Live | Backtest | Entraînement). Live : chandeliers
  M1 Lightweight Charts, watchlist Market Watch, bouton Trading/Stopped par
  paire (SQLite, défaut Stopped, 409 si broker déconnecté), header en badges
  (broker cliquable → fenêtre de connexion), panneau compte
  (`GET /api/account` : équité/marge + perte du jour vs plafond).
- **Brokers — deux gateways COMPLÈTES** (connexion, compte, routage, flux,
  comptes rendus) :
  - **IB** (`ib_async`, import paresseux) : `connectAsync` sans login/mdp ;
    `place_order` = bracket natif (Market + Limit TP + Stop SL, OCA côté TWS) ;
    push `reqMktData` ; forex/métaux 6 lettres via `Forex()`.
  - **MT5** (`MetaTrader5`, attach à un terminal ouvert) : `TRADE_ACTION_DEAL`
    avec SL/TP natifs, filling IOC/FOK, `magic` PyEA ; flux par **scrutation**
    0,25 s (dédup `time_msc`, mid, IPC déporté via `_call`) ; exécutions relues
    via `history_deals_get` toutes les 2 s.
  - `is_connected()` mémorisé 2 s. Déconnecté → `ConnectionError`, jamais de
    faux id/tick. Bascule de broker à chaud (déconnexion requise).
- **Flux live** (`pyea/live/`) : `MarketDataFeed` (bus `market.tick`),
  `LiveTradingEngine` (flux strict, une stratégie par symbole, gating :
  kill-switch + paire armée + broker connecté, **registre d'ordres en vol**,
  timeout 60 s), `LiveRuntime` (démarré à la connexion broker). Chemin retour :
  `ExecutionReport` + `set_execution_callback` ; P&L broker (sorties), colonne
  `pnl`, `/api/positions` distingue latent/réalisé.
- **Inférence live Couleuvre** : `CandleAggregator` (ticks → bougies alignées
  `Timestamp.floor`, M1→D1), `resolve_live_model` (dernier run `completed` →
  dernier pli ; aucun modèle → paire muette), `warmup(live=True)` (tampon 400
  bougies). Équivalence live/backtest ~99,5 % (résiduel = indicateurs
  récursifs, assumé).
- **Backtest** : **backtrader vendorisé** (`lib/backtrader/`, GPLv3 OK usage
  perso). Entrée Market cheat-on-close, barrières TP/SL = Stop+Limit OCO au
  prix exact, stop prioritaire, clôture forcée fin de semaine ISO, bougie
  fantôme, 1 unité nominale re-scalée, `engine.run` synchrone. **Coûts
  modélisés** : spread MESURÉ (médiane `ask_close − bid_close`), COMM_FIXED
  par côté, commission `costs.commission_per_unit` ; stats NETTES ; sans
  colonnes ask → `costs_modelled: false` + bandeau « OPTIMISTE ».
- **Entraînement** (`/training`) : walk-forward à fenêtre expansive (jamais de
  split aléatoire), job de thread unique annulable, progression WS, reprise
  après reload, `fail_orphan_runs`, historisé SQLite + artefacts
  `data/models/<run>/fold_<i>/`. **Agrégation honnête** : profit factor ET win
  rate OOS sur TOUS les trades (jamais de moyenne de ratios par pli) ;
  Sharpe/SQN par pli seulement ; AUC IS vs taux OOS (= surapprentissage) ;
  chauffe OOS récupérée (`OOS_CONTEXT_BARS=300`, jamais rejouée). Barrières/
  seuils NON tunables dans l'UI (définition de `couleuvre_v0_1` ; évolution =
  `v0_2`).
- **Couleuvre v0.1** (spec : `docs/strategie_couleuvre.md` — swing 2-5 j, un
  LightGBM par actif) :
  - Features : 34 features causales (ordre figé, constantes de module),
    anti-fuite prouvée par **stabilité par préfixe**, zéro dépendance TA.
  - Labeling : triple-barrier ATR, label binaire symétrique (1 = haute
    d'abord), **basse prioritaire en départage intrabar**, fenêtre incomplète →
    NaN. Horizon via `_horizon_ticks()` (⚠ pandas 3 : `asi8` en microsecondes,
    PAS en ns).
  - `train` : `lgb.train` natif (pas de sklearn), `model.txt` +
    `features.json` par pli. `on_tick` : seuils 0.55/0.45, mêmes barrières ATR.
    Non-fuite prouvée : sur bruit, AUC IS ~0,96 / OOS ~50 %.
- **RiskManager.evaluate v2** : HOLD ignoré, EXIT jamais bloqué, entrées à
  taille fixe sous 3 limites : `max_positions_per_symbol`, `max_open_positions`,
  `max_daily_loss_pct` (repère UTC persisté dans `daily_equity` ; **garde LIVE
  uniquement**, non modélisée en backtest — assumé).
- **SQLite** : micro-migration `_add_missing_columns()` (idempotente). Cache
  mémoire pour `is_trading_enabled` (pas de SELECT par tick).

## Points de vigilance

- Singletons de module (`event_bus`, `web_log_buffer`, `broker_runtime`,
  `live_runtime`, `job_manager`) non injectés.
- `broker_credentials.py` volontairement sans appelant (réserve future).
- Téléchargeur : annuler les tâches restantes d'une année en échec.
- Callbacks synchrones ib_async : retenir les coroutines planifiées (le GC les
  annulait en plein vol).
- Horodatages SQLite : toujours avec fuseau (UTC), jamais naïfs.
- `_demo_quote` mémorisé (`_demo_closes`, LRU par minute) — ne pas dé-optimiser.
