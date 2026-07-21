"""Passerelles broker.

- ``broker_gateway.py`` : le contrat générique ``BrokerGateway`` + registre.
- ``broker_interactive_brokers.py`` : Interactive Brokers (via ib_async).
- ``broker_metatrader.py`` : MetaTrader 5 (via le paquet ``MetaTrader5``).
- Broker suivant : créer ``broker_<nom>.py`` respectant le contrat, l'importer
  ici — rien d'autre à toucher (stratégie, risque, API restent inchangés).

L'import de chaque module suffit à enregistrer sa gateway (décorateur
``@register_gateway``) : c'est ce qui peuple la liste déroulante de l'UI.
"""

from pyea.brokers import (  # noqa: F401
    broker_interactive_brokers,
    broker_metatrader,
)
from pyea.brokers.broker_credentials import BrokerCredentials, broker_credentials
from pyea.brokers.broker_gateway import (
    BrokerGateway,
    get_gateway,
    list_gateways,
    register_gateway,
)

__all__ = [
    "BrokerGateway",
    "BrokerCredentials",
    "broker_credentials",
    "get_gateway",
    "list_gateways",
    "register_gateway",
]
