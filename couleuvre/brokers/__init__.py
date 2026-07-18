"""Passerelles broker.

- ``broker_gateway.py`` : le contrat générique ``BrokerGateway`` + registre.
- ``broker_interactive_brokers.py`` : première implémentation (IB via ib_async).
- Broker suivant : créer ``broker_<nom>.py`` respectant le contrat, l'importer
  ici — rien d'autre à toucher (stratégie, risque, API restent inchangés).
"""

from couleuvre.brokers import broker_interactive_brokers  # noqa: F401
from couleuvre.brokers.broker_gateway import BrokerGateway, get_gateway, register_gateway

__all__ = ["BrokerGateway", "get_gateway", "register_gateway"]
