"""Tests de l'interrupteur de trading par symbole (persistance SQLite)."""

from pathlib import Path

import pytest

from pyea.config.config_settings import get_settings
from pyea.storage.storage_database import init_db
from pyea.storage.storage_trading_state import (
    get_trading_states,
    is_trading_enabled,
    set_trading_enabled,
)


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Base isolée pour le test, puis retour à la base configurée."""
    settings = get_settings()
    original_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    init_db()
    yield
    settings.database_url = original_url
    init_db()


def test_defaut_sur_arrete(tmp_db: None) -> None:
    # Paire jamais touchée = arrêtée (défaut sûr).
    assert is_trading_enabled("EURUSD") is False
    assert get_trading_states() == {}


def test_armement_et_arret_persistes(tmp_db: None) -> None:
    assert set_trading_enabled("EURUSD", True) is True
    assert is_trading_enabled("EURUSD") is True
    assert get_trading_states() == {"EURUSD": True}

    assert set_trading_enabled("EURUSD", False) is False
    assert is_trading_enabled("EURUSD") is False


def test_cache_reflete_les_ecritures(tmp_db) -> None:
    """Le cache mémoire ne doit jamais mentir sur ce qui est persisté.

    `is_trading_enabled` est appelé sur CHAQUE tick (une centaine de fois par
    seconde en live) : il lit un cache. Une écriture doit donc être visible
    immédiatement, et la base rester la source de vérité."""
    assert is_trading_enabled("EURUSD") is False  # défaut sûr
    set_trading_enabled("EURUSD", True)
    assert is_trading_enabled("EURUSD") is True
    assert get_trading_states()["EURUSD"] is True
    set_trading_enabled("EURUSD", False)
    assert is_trading_enabled("EURUSD") is False
