"""Vérifie que la gateway IB est enregistrée et respecte le contrat."""

from pyea.brokers import BrokerGateway, get_gateway


def test_interactive_brokers_est_enregistree() -> None:
    cls = get_gateway("interactive_brokers")
    assert issubclass(cls, BrokerGateway)
    assert cls.name == "interactive_brokers"
