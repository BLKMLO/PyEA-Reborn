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
│   │   ├── data_market_feed.py            # Ingestion : ticks broker → bus d'événements.
│   │   └── data_history_downloader.py     # Historique M1 Dukascopy → Parquet (+ load/resample).
│   │
│   ├── strategies/
│   │   ├── strategy_base.py               # Contrat abstrait Strategy (warmup / on_tick / shutdown / train).
│   │   ├── strategy_registry.py           # Registre plugin : @register_strategy, lookup par nom.
│   │   ├── strategy_couleuvre_features.py # 34 features causales (sans fuite) + ATR brut.
│   │   ├── strategy_couleuvre_labeling.py # Labeling triple-barrier (label binaire symétrique).
│   │   └── strategy_couleuvre_v0_1.py     # Couleuvre_v0.1 : train (LightGBM) / warmup / on_tick.
│   │
│   ├── risk/
│   │   └── risk_manager.py                # Seul module qui transforme un Signal en OrderRequest
│   │                                      # (v1 : taille fixe + plafond de positions).
│   │
│   ├── backtest/
│   │   └── backtest_engine.py             # Rejoue l'historique via le flux complet
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
│   │   ├── broker_interactive_brokers.py  # Interactive Brokers (ib_async, via TWS/IB Gateway).
│   │   └── broker_metatrader.py           # MetaTrader 5 (paquet MetaTrader5, attache à un terminal MT5).
│   │
│   ├── storage/
│   │   ├── storage_models.py              # Modèles SQLAlchemy (signals, trades, états, runs).
│   │   ├── storage_database.py            # Moteur/sessions ; SQLite → Postgres via database_url.
│   │   ├── storage_trading_state.py       # Interrupteur Trading/Stopped par symbole (persisté).
│   │   ├── storage_trades.py              # Journal SQL des trades exécutés (affichage réel, jamais simulé).
│   │   └── storage_training_runs.py       # Historique des entraînements (métriques OOS, artefacts).
│   │
│   ├── api/
│   │   ├── api_pages.py                   # Pages HTML : / (live), /backtest, /training (Jinja2 + HTMX).
│   │   ├── api_rest.py                    # REST : status, brokers (liste + connect/disconnect), symbols, trading, positions, logs, charts.
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
   OrderRequest → BrokerGateway`. Aucune stratégie ne parle au broker ;
   aucun ordre ne contourne le risk manager.
2. **Le bus d'événements découple tout** : broker, stratégie et logs
   publient ; le WebSocket et la persistance consomment. FastAPI ne
   s'infiltre jamais dans la logique de trading.
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
