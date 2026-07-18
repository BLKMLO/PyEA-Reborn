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
python run_server.py        # seule commande CLI : démarre le serveur web
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
- Graphiques : init Chart.js uniquement dans `pyea/web/static/js/charts.js`,
  données via endpoints JSON `/api/charts/*`.

## État du projet

- Échafaudage complet et fonctionnel : serveur web, dashboard
  (statut + graphique factice + logs), REST + WebSocket, registres
  stratégie/broker, SQLAlchemy (SQLite), 6 tests fumée verts.
- **Squelettes vides** (NotImplementedError) à développer plus tard :
  logique LightGBM de `CouleuvreV01` (warmup/on_tick), `RiskManager.evaluate`,
  `InteractiveBrokersGateway` (appels ib_async réels), `MarketDataFeed`.

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
