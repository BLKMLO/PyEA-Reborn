"""PyEA — Expert Advisor de trading algorithmique.

PyEA est le logiciel (plateforme : web, brokers, risque, stockage) ;
« Couleuvre » désigne uniquement le moteur de décision, implémenté comme
stratégie dans ``strategies/strategy_couleuvre_v0_1.py``.

Packages :
- ``config``     : chargement centralisé de la configuration (.env + config.yaml)
- ``core``       : briques transverses (logging, types du domaine, bus d'événements)
- ``data``       : ingestion des données de marché
- ``strategies`` : contrat ``Strategy``, registre, implémentations
- ``risk``       : gestion du risque (validation des ordres)
- ``brokers``    : contrat ``BrokerGateway``, implémentations par broker
- ``storage``    : persistance SQLAlchemy (SQLite → Postgres)
- ``api``        : routes FastAPI (REST, WebSocket, pages HTML)
- ``web``        : templates Jinja2 et fichiers statiques
"""

__version__ = "0.1.0"

# --- Dépendances Python vendorisées (lib/) ---------------------------------
# Certaines libs tierces PURES PYTHON sont embarquées dans ``lib/`` à la racine
# du dépôt plutôt qu'installées via pip, pour un fonctionnement « zéro install »
# et hors-ligne (VPS sans internet, install Windows fragile). On les rend
# importables en préfixant ``sys.path`` dès l'import de ``pyea``, avant tout
# ``import backtrader``. Seules des libs sans extension native peuvent l'être
# (backtrader = GPLv3, pur Python) — numba/lightgbm/pyarrow, eux, restent pip.
import sys as _sys
from pathlib import Path as _Path

_VENDOR_DIR = _Path(__file__).resolve().parent.parent / "lib"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR_DIR))
