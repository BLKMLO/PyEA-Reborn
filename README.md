# PyEA

**PyEA** est un Expert Advisor de trading algorithmique : cœur en
Python 3.11+, pilotage intégral depuis une interface web (FastAPI +
HTMX + Tailwind, graphiques TradingView Lightweight Charts), exécution
chez Interactive Brokers — en
paper trading d'abord. Son moteur de décision, **Couleuvre**, est une
stratégie basée sur un modèle LightGBM (`couleuvre_v0_1`), conçue comme
un plugin parmi d'autres possibles.

- Une seule commande pour tout lancer, le reste se pilote depuis le web
  (config, stratégie, graphiques, logs, statut broker).
- Architecture modulaire : brokers et stratégies interchangeables par
  contrat, risk management obligatoire sur le chemin des ordres.
- Données historiques M1 (31 instruments : forex, métaux, indices),
  ré-échantillonnage multi-timeframes, backtest et entraînement
  walk-forward intégrés.

## 🚀 Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # remplir les identifiants Interactive Brokers
python run_server.py        # démarre le serveur web
```

Ouvrir **http://127.0.0.1:8000** — tout le pilotage se fait depuis le
dashboard.

Optionnel, pour préparer le backtest (long : plusieurs heures, ~10-20 Go) :

```bash
python download_history.py  # historique M1 depuis 2010 (réglable)
```

> ⚠️ Le mode par défaut est **paper trading**. Ne passer
> `broker.trading_mode` à `live` dans `config.yaml` qu'en toute
> connaissance de cause : un EA peut perdre de l'argent réel.

## Documentation

| Document | Contenu |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Arborescence, rôle des modules, règles et conventions, où ajouter du code |
| [docs/donnees_historiques.md](docs/donnees_historiques.md) | Téléchargement Dukascopy, stockage Parquet, timeframes, ajout d'instruments |
| [docs/choix_techniques.md](docs/choix_techniques.md) | Pourquoi REST+WebSocket, ib_async, SQLite, libs vendorisées… |

Tests : `pytest` (structure miroir dans `tests/`).
