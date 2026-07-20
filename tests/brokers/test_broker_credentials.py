"""Tests du store d'identifiants broker en mémoire."""

from pyea.brokers.broker_credentials import BrokerCredentials


def test_non_configure_par_defaut() -> None:
    creds = BrokerCredentials()
    assert creds.is_configured() is False
    assert creds.username == ""


def test_set_et_configure() -> None:
    creds = BrokerCredentials()
    creds.set("marianne", "secret")
    assert creds.is_configured() is True
    assert creds.username == "marianne"
    assert creds.password == "secret"


def test_update_username_conserve_le_mot_de_passe() -> None:
    creds = BrokerCredentials()
    creds.set("marianne", "secret")
    creds.update_username("marianne2")
    assert creds.username == "marianne2"
    assert creds.password == "secret"  # inchangé


def test_clear_efface_tout() -> None:
    creds = BrokerCredentials()
    creds.set("marianne", "secret")
    creds.clear()
    assert creds.is_configured() is False
    assert creds.username == ""
    assert creds.password == ""


def test_identifiant_seul_ne_suffit_pas() -> None:
    creds = BrokerCredentials()
    creds.update_username("marianne")
    assert creds.is_configured() is False  # pas de mot de passe
