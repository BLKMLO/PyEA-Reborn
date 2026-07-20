"""Tests de robustesse de la config face aux valeurs absurdes.

Une valeur dangereuse dans config.yaml doit échouer AU DÉMARRAGE avec un
message clair — jamais produire un comportement aberrant au runtime
(refresh 0 s = serveur martelé, taille de position négative = ordres
inversés en live).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from pyea.config.config_settings import Settings, _load_yaml


def test_yaml_malforme_erreur_lisible(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("broker: [trading_mode: 'oops'", encoding="utf-8")
    with pytest.raises(ValueError, match="config.yaml illisible"):
        _load_yaml(bad)


def test_yaml_non_mapping_refuse(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("- juste\n- une liste\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        _load_yaml(bad)


def test_yaml_absent_donne_defauts(tmp_path: Path) -> None:
    assert _load_yaml(tmp_path / "inexistant.yaml") == {}


def test_trading_mode_invalide_refuse() -> None:
    with pytest.raises(ValidationError):
        Settings(trading_mode="banana")


def test_refresh_zero_refuse() -> None:
    # Un refresh de 0 s ferait marteler l'API par le front.
    with pytest.raises(ValidationError):
        Settings(ui_chart_refresh_seconds=0)


def test_risque_negatif_refuse() -> None:
    # Une taille négative inverserait le sens des ordres en live.
    with pytest.raises(ValidationError):
        Settings(risk_max_position_size=-3)
    with pytest.raises(ValidationError):
        Settings(risk_max_open_positions=0)


def test_annee_historique_farfelue_refusee() -> None:
    with pytest.raises(ValidationError):
        Settings(history_start_year=210)  # faute de frappe pour 2010