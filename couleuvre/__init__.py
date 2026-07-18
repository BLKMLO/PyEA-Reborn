"""Couleuvre — Expert Advisor de trading algorithmique.

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
