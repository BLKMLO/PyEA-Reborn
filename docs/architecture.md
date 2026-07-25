# Architecture de PyEA

> Doc de référence : arborescence, rôle de chaque module, règles et
> conventions. À lire avant d'ajouter du code. Pour les choix techniques
> (pourquoi FastAPI, ib_async…), voir [choix_techniques.md](choix_techniques.md).

## Arborescence

```
PyEA-Reborn/
├── run_server.py                          # Point d'entrée CLI principal : démarre le serveur web.
├── download_history.py                    # CLI ponctuel (exception) : historique M1 pour le backtest.
├── config.yaml                            # Paramètres fonctionnels versionnés (stratégie, risque, storage).
├── .env.example                           # Modèle des secrets (.env réel jamais commité).
├── requirements.txt
│
├── pyea/                                  # Package applicatif.
│   ├── app_factory.py                     # create_app() : SEUL endroit où les modules sont câblés.
│   │
│   ├── config/
│   │   └── config_settings.py             # Settings unique = .env (secrets) + config.yaml (fonctionnel).
│   │
│   ├── core/                              # Briques transverses, sans dépendance métier.
│   │   ├── core_domain.py                 # Types partagés : TickData, Signal, OrderRequest, Position.
│   │   ├── core_logging.py                # Logging fichier + console + tampon mémoire pour le web.
│   │   └── core_events.py                 # Bus pub/sub asynchrone (ticks, signaux, statut, logs).
│   │
│   ├── data/
│   │   ├── data_market_feed.py            # Ingestion : ticks broker → bus d'événements (agnostique du broker).
│   │   └── data_history_downloader.py     # Historique M1 Dukascopy → Parquet (+ load/resample).
│   │
│   ├── live/
│   │   ├── live_engine.py                 # Flux strict en temps réel : bus (ticks) → Strategy →
│   │   │                                  # Signal → RiskManager → OrderRequest → BrokerGateway.
│   │   │                                  # Ne trade que si armé + connecté + kill-switch ON.
│   │   │                                  # Tient le registre des ORDRES EN VOL (un ordre non
│   │   │                                  # tranché bloque son symbole) et journalise les
│   │   │                                  # ExecutionReport remontés par la gateway.
│   │   ├── live_candles.py                # Agrégateur tick→bougie (OHLCV) aligné sur le timeframe.
│   │   ├── live_models.py                 # Sélection du modèle live par actif (dernier run réussi).
│   │   └── live_runtime.py                # Singleton : assemble feed + moteur, warmup par symbole
│   │                                      # (modèle + historique), démarré/arrêté à la connexion broker.
│   │
│   ├── strategies/
│   │   ├── strategy_base.py               # Contrat abstrait Strategy (warmup / on_tick / shutdown / train).
│   │   ├── strategy_registry.py           # Registre plugin : @register_strategy, lookup par nom.
│   │   ├── strategy_couleuvre_features.py # 34 features causales (sans fuite) + ATR brut.
│   │   ├── strategy_couleuvre_labeling.py # Labeling triple-barrier (label binaire symétrique).
│   │   └── strategy_couleuvre_v0_1.py     # Couleuvre_v0.1 : train (LightGBM) / warmup / on_tick.
│   │
│   ├── risk/
│   │   └── risk_manager.py                # Seul module qui transforme un Signal en OrderRequest :
│   │                                      # taille fixe, plafonds de positions (par symbole ET
│   │                                      # sur le compte), perte journalière max (garde LIVE,
│   │                                      # non modélisée en backtest — cf. son docstring).
│   │
│   ├── backtest/
│   │   └── backtest_engine.py             # Coûts : spread MESURÉ dans les données (ask - bid)
│   │                                      # + commission ; un aller-retour paie un spread.
│   │                                      # Rejoue l'historique via le flux complet
│   │                                      # Strategy → RiskManager → backtrader (exécution +
│   │                                      # métriques : Sharpe/SQN/profit factor). Barrières
│   │                                      # TP/SL (Stop/Limit OCO), clôture fin de semaine.
│   │
│   ├── training/
│   │   ├── training_walkforward.py        # Découpe walk-forward + orchestration train/test.
│   │   └── training_jobs.py               # Jobs en thread, progression → bus → WebSocket.
│   │
│   ├── brokers/
│   │   ├── broker_gateway.py              # Contrat générique BrokerGateway + registre (+ list_gateways).
│   │   ├── broker_credentials.py          # Store login/mdp en mémoire — réservé à un futur broker (ni IB ni MT5 n'en ont besoin).
│   │   ├── broker_runtime.py              # Broker actif + état de connexion RÉEL + bascule runtime (singleton, lu par l'API).
│   │   ├── broker_interactive_brokers.py  # Interactive Brokers (ib_async, via TWS/IB Gateway) : connexion, lecture de compte, ordres (bracket) + flux de prix (à valider live).
│   │   └── broker_metatrader.py           # MetaTrader 5 (paquet MetaTrader5, attache à un terminal MT5) : connexion, lecture de compte, ordres (DEAL + SL/TP natifs) + flux de prix (scrutation, à valider live).
│   │
│   ├── storage/
│   │   ├── storage_models.py              # Modèles SQLAlchemy (signals, trades, états, runs).
│   │   ├── storage_database.py            # Moteur/sessions ; SQLite → Postgres via database_url.
│   │   ├── storage_trading_state.py       # Interrupteur Trading/Stopped par symbole (persisté).
│   │   ├── storage_trades.py              # Journal SQL des trades exécutés (affichage réel, jamais simulé).
│   │   ├── storage_daily_equity.py        # Équité de référence du jour (limite de perte journalière),
│   │   │                                  # persistée : un redémarrage ne remet pas le compteur à zéro.
│   │   ├── storage_trading_state.py       # Interrupteur Trading/Stopped par paire (cache mémoire :
│   │   │                                  # lu à chaque tick, la base reste la source de vérité).
│   │   └── storage_training_runs.py       # Historique des entraînements (métriques OOS, artefacts).
│   │
│   ├── api/
│   │   ├── api_pages.py                   # Pages HTML : / (live), /backtest, /training (Jinja2 + HTMX).
│   │   ├── api_rest.py                    # REST : status, brokers (liste + connect/disconnect), symbols,
│   │   │                                  # trading, account (équité/marge du broker), positions, logs, charts.
│   │   ├── api_backtest.py                # REST : /api/backtest/datasets et /api/backtest/run.
│   │   ├── api_training.py                # REST : /api/training/run, current-job, jobs/{id}, runs, definition/{strategy}.
│   │   └── api_websocket.py               # WebSocket /ws : relais du bus vers les navigateurs.
│   │
│   └── web/
│       ├── templates/                     # base.html (header + nav Live/Backtest/Entraînement),
│       │                                  # dashboard.html, backtest.html (run unique), training.html.
│       └── static/
│           ├── js/charts.js               # Logique du dashboard live (graphique, watchlist, positions).
│           ├── js/toasts.js               # Notifications toast (feedback des actions), chargé partout.
│           ├── js/backtest.js             # Page backtest : formulaire, équité, trades (run unique).
│           ├── js/training.js             # Page entraînement : walk-forward, équité OOS, plis, définition.
│           └── vendor/                    # Tailwind, HTMX, Lightweight Charts (chandeliers),
│                                          # Chart.js (futurs graphiques P&L) — local, pas de CDN.
│
├── lib/                                   # Dépendances Python PURES vendorisées (zéro install) :
│   └── backtrader/                        # Moteur de backtest (GPLv3). pyea/__init__.py préfixe
│                                          # lib/ dans sys.path avant tout `import backtrader`.
├── docs/                                  # Cette documentation.
└── tests/                                 # Structure miroir de pyea/ (un dossier par package).
```

## Règles d'architecture

1. **Flux strict** : `MarketDataFeed → Strategy → Signal → RiskManager →
   OrderRequest → BrokerGateway`, et retour par `ExecutionReport →
   LiveTradingEngine → journal SQL`. Aucune stratégie ne parle au broker ;
   aucun ordre ne contourne le risk manager. Ce flux est imposé À LA FOIS
   en backtest (`backtest_engine.py`) et en live (`live/live_engine.py`) —
   le moteur live consomme les ticks du bus et applique la même chaîne, en
   ne tradant que les paires armées avec broker connecté et kill-switch ON.
   Le chemin RETOUR est aussi important que l'aller : `place_order` ne fait
   que SOUMETTRE ; c'est la gateway qui rapporte ce que le broker a fait
   (`set_execution_callback`), ce qui libère l'ordre en vol et inscrit le
   trade au journal. Un broker qui ne sait pas rapporter ses exécutions
   n'alimente aucun trade — PyEA n'en invente jamais.
2. **Le bus d'événements découple tout** : broker et stratégie publient ; le
   WebSocket et la persistance consomment. On ne déclare et ne relaie que des
   topics ayant un producteur RÉEL — un topic relayé sans producteur donne
   l'illusion d'un flux temps réel inexistant. FastAPI ne
   s'infiltre jamais dans la logique de trading. Les abonnés sont **isolés
   les uns des autres** : une erreur d'abonné est journalisée, jamais
   propagée au producteur (sinon une exception de stratégie tuerait la
   boucle de flux de prix du broker, en silence).
3. **Paper → live** = changer `broker.trading_mode` dans `config.yaml`
   (le port IB correspondant est lu dans `.env`). Rien d'autre.
4. **`app_factory.create_app()` est le seul lieu de câblage** : les modules
   ne s'instancient pas entre eux.
5. **Config centralisée** : tout passe par `get_settings()` — aucune
   lecture directe d'`os.environ` ou du YAML ailleurs.
6. **L'interface ne ment pas** : les données de COMPTE (positions, trades,
   P&L, état de connexion) viennent TOUJOURS du broker ou du journal SQL,
   jamais d'une simulation — vides si le broker est déconnecté. Seules les
   données de MARCHÉ peuvent être une démo tant que le flux réel n'est pas
   branché, et l'UI l'affiche explicitement (« DÉMO »).

## Scripts front : un seul scope global

Les pages chargent des `<script>` CLASSIQUES (pas des modules ES) : **tous
partagent le même scope lexical global**. Deux règles en découlent.

1. **`static/js/ui.js` est le socle partagé** (formats, préférences
   localStorage, cartes de stats, tables triables, export CSV, bandeau d'état,
   horloge UTC, raccourcis). Il est enfermé dans une **IIFE** et n'expose que
   `window.PyEA` — aucun de ses noms internes ne doit atteindre le scope
   global.
2. **Les scripts de page destructurent ce qu'ils utilisent** :
   `const { statCard, num2 } = window.PyEA;`. Ils ne redéfinissent jamais un
   helper déjà fourni par le socle.

Pourquoi c'est une règle et pas un style : un `const` de page et une
`function` du socle portant le même nom lèvent une `SyntaxError`
(« Identifier 'x' has already been declared ») qui empêche le script de page
de s'exécuter **entièrement** — la page est morte, pas dégradée. Et
`node --check` ne le détecte PAS (il analyse chaque fichier isolément) : seul
un chargement navigateur le révèle. Après toute modification du front,
vérifier les trois pages au navigateur, console ouverte.

## Conventions de nommage

- **Fichiers/dossiers Python** : `snake_case`. **Classes** : `PascalCase`.
- **Préfixe = package** : `strategy_*.py`, `broker_*.py`, `api_*.py`,
  `core_*.py`, `data_*.py`, `storage_*.py`. On sait où vit un fichier rien
  qu'à son nom (et inversement).
- **Brokers** : le contrat générique est `broker_gateway.py` ; chaque
  implémentation est `broker_<nom>.py` (`broker_interactive_brokers.py`
  aujourd'hui, `broker_<suivant>.py` demain).
- **Stratégies** : le contrat est `strategy_base.py` ; chaque implémentation
  est `strategy_<nom>.py` — Couleuvre_v0.1 vit donc dans
  `strategy_couleuvre_v0_1.py` (le préfixe l'aligne sur la convention
  globale du projet).
- **Tests** : `tests/<package>/test_<module>.py`, en miroir strict du source.

## Où ajouter du code sans rien casser

| Besoin | Où | Ce qu'il ne faut PAS toucher |
|---|---|---|
| Nouvelle stratégie | `strategies/strategy_<nom>.py` + `@register_strategy` + import dans `strategies/__init__.py` | Moteur, API, brokers |
| Nouveau broker | `brokers/broker_<nom>.py` + `@register_gateway` + import dans `brokers/__init__.py` | Stratégies, risque, API |
| Nouvel endpoint REST | `api/api_rest.py` (ou nouveau routeur `api_*.py` inclus dans `app_factory.py`) | Modules métier |
| Nouveau graphique | Endpoint JSON dans `api_rest.py` + init dans `static/js/charts.js` + canvas dans le template | — |
| Nouvelle table | `storage/storage_models.py` | Le reste du storage |
| Nouveau paramètre | `config.yaml` (fonctionnel) ou `.env.example` (secret) + champ dans `config_settings.py` | Lectures directes d'env ailleurs — interdites |

## Tests

```bash
pytest
```

La structure de `tests/` est le miroir strict de `pyea/` : un test de
`pyea/data/data_history_downloader.py` vit dans
`tests/data/test_data_history_downloader.py`.
