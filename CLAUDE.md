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
- Graphiques : init Chart.js uniquement dans `pyea/web/static/js/charts.js`,
  données via endpoints JSON `/api/charts/*`.
- Libs front (Tailwind, HTMX, Chart.js) **vendorisées** dans
  `pyea/web/static/vendor/` — jamais de CDN au runtime (le dashboard doit
  marcher sur un VPS sans internet sortant).

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
  registres stratégie/broker, SQLAlchemy (SQLite), 19 tests verts.
- Dashboard live façon TradingView : chandeliers M1 au centre
  (**TradingView Lightweight Charts** : pan/zoom natifs, historique
  paginé via `?before=`, refresh incrémental `series.update` qui
  préserve le défilement), watchlist à droite (clic = onglet, pastille
  verte = « en trading » d'après `strategy.symbols` + `strategy.enabled`),
  panneau bas Positions (fermées grisées, récentes en premier) / Logs,
  P&L total en bas à droite, switch Live/Backtest dans le header
  (`/backtest` = placeholder). Rafraîchissement du seul graphique actif
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
- Interface de backtest : à venir ; elle lira les Parquet via
  `data_history_downloader.load_history()`.
- **Squelettes vides** (NotImplementedError) à développer plus tard :
  logique LightGBM de `CouleuvreV01` (warmup/on_tick), `RiskManager.evaluate`,
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
