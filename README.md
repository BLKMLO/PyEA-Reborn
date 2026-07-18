# PyEA-Reborn — EA « Couleuvre »

Expert Advisor de trading algorithmique. Cœur logique en Python 3.11+,
pilotage intégral via une interface web (FastAPI + HTMX + Tailwind +
Chart.js), exécution prévue chez Interactive Brokers (paper trading
d'abord). Ce dépôt contient l'**échafaudage** : les contrats, le câblage
et le dashboard sont en place ; la logique de trading (LightGBM, signaux,
risk management) sera développée dans les modules déjà prévus pour elle.

## Démarrage

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # puis remplir les identifiants IB
python run_server.py        # seule commande CLI du projet
```

Ouvrir ensuite http://127.0.0.1:8000 — tout le reste (config, activation
de la stratégie, graphiques, logs, statut broker) se pilote depuis le web.

## Arborescence

```
PyEA-Reborn/
├── run_server.py                          # Point d'entrée CLI unique : démarre le serveur web.
├── config.yaml                            # Paramètres fonctionnels versionnés (stratégie, risque, storage).
├── .env.example                           # Modèle des secrets (.env réel jamais commité).
├── requirements.txt
│
├── couleuvre/                             # Package applicatif.
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
│   │   └── data_market_feed.py            # Ingestion : ticks broker → bus d'événements.
│   │
│   ├── strategies/
│   │   ├── strategy_base.py               # Contrat abstrait Strategy (warmup / on_tick / shutdown).
│   │   ├── strategy_registry.py           # Registre plugin : @register_strategy, lookup par nom.
│   │   └── strategy_couleuvre_v0_1.py     # Couleuvre_v0.1 (LightGBM) — squelette vide typé.
│   │
│   ├── risk/
│   │   └── risk_manager.py                # Seul module qui transforme un Signal en OrderRequest.
│   │
│   ├── brokers/
│   │   ├── broker_gateway.py              # Contrat générique BrokerGateway + registre.
│   │   └── broker_interactive_brokers.py  # 1re implémentation (ib_async). Suivant : broker_<nom>.py.
│   │
│   ├── storage/
│   │   ├── storage_models.py              # Modèles SQLAlchemy (signals, trades).
│   │   └── storage_database.py            # Moteur/sessions ; SQLite → Postgres via database_url.
│   │
│   ├── api/
│   │   ├── api_pages.py                   # Pages HTML (Jinja2 + HTMX).
│   │   ├── api_rest.py                    # REST : statut, logs, données de graphiques (/api/*).
│   │   └── api_websocket.py               # WebSocket /ws : relais du bus vers les navigateurs.
│   │
│   └── web/
│       ├── templates/                     # base.html, dashboard.html.
│       └── static/js/charts.js            # Initialisation Chart.js (jamais inline dans les templates).
│
└── tests/                                 # Structure miroir de couleuvre/ (un dossier par package).
```

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

## Justification des choix techniques

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
- **HTMX + Tailwind + Chart.js via CDN** : aucun build front, dashboard et
  formulaires triviaux à écrire ; Tailwind ne gère que le style et
  n'interfère pas avec Chart.js, dont l'initialisation est centralisée
  dans `static/js/charts.js` et alimentée par `/api/charts/*`.

## Tests

```bash
pytest
```

Trois tests fumée existent déjà (registres stratégie/broker, endpoints
API) ; la structure miroir de `tests/` indique où placer les suivants.
