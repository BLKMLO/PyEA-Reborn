"""Configuration centralisée du projet.

Deux sources, un seul objet ``Settings`` :
- ``.env``       : secrets et paramètres machine (ports IB paper/live, chemin MT5).
- ``config.yaml``: paramètres fonctionnels versionnables (stratégie, risque, storage).

**Priorité : config.yaml l'emporte sur .env.** Les valeurs du YAML sont
passées au constructeur de ``Settings``, et pydantic-settings donne aux
arguments d'initialisation la priorité la PLUS HAUTE (devant les variables
d'environnement et le ``.env``). Conséquence concrète : une clé présente dans
config.yaml ignore la variable d'environnement de même nom — mettre
``TRADING_MODE=live`` dans ``.env`` ne change rien si ``broker.trading_mode``
est renseigné dans le YAML. C'est voulu (le YAML est la source versionnée du
fonctionnel), mais il faut le savoir : pour qu'une variable d'environnement
prenne effet, la clé correspondante doit être ABSENTE de config.yaml.

Le reste du code ne lit JAMAIS os.environ ni le YAML directement :
tout passe par ``get_settings()``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML_PATH = PROJECT_ROOT / "config.yaml"


class Settings(BaseSettings):
    """Paramètres agrégés .env + config.yaml."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets / machine (.env) ---
    ib_host: str = "127.0.0.1"
    ib_port_paper: int = 7497
    ib_port_live: int = 7496
    ib_client_id: int = 1

    # MetaTrader 5 : PyEA s'ATTACHE à un terminal MT5 déjà lancé et connecté
    # (comme TWS/IB Gateway pour IB) — aucun identifiant saisi dans PyEA. Le
    # chemin ci-dessous est OPTIONNEL : renseigné, il permet à
    # MetaTrader5.initialize() de lancer le bon terminal s'il n'est pas déjà
    # ouvert. Vide = détection automatique du terminal en cours d'exécution.
    mt5_terminal_path: str = ""

    # --- Fonctionnel (config.yaml, surchargeables par .env) ---
    # Les bornes (ge/gt/le) transforment une valeur absurde saisie dans
    # config.yaml en ERREUR CLAIRE AU DÉMARRAGE plutôt qu'en comportement
    # dangereux au runtime (ex. : refresh 0 s = marteler le serveur,
    # taille de position négative = ordres inversés en live).
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, ge=1, le=65535)
    broker_name: str = "interactive_brokers"
    trading_mode: Literal["paper", "live"] = "paper"
    strategy_name: str = "couleuvre_v0_1"
    strategy_enabled: bool = False
    ui_chart_refresh_seconds: int = Field(default=5, ge=1)
    risk_max_position_size: float = Field(default=1, gt=0)
    # Perte journalière max, en % de l'équité de début de journée UTC.
    # Garde LIVE (exige l'équité réelle du broker) ; 0 = désactivée. Le
    # backtest ne la modélise pas (capital nominal synthétique) — cf.
    # risk_manager.py.
    risk_max_daily_loss_pct: float = Field(default=2.0, ge=0)
    # Deux plafonds DISTINCTS : par symbole (empilement d'entrées sur la même
    # paire) et sur le compte (exposition totale).
    risk_max_positions_per_symbol: int = Field(default=1, ge=1)
    risk_max_open_positions: int = Field(default=1, ge=1)
    # Commission du courtier, PAR CÔTÉ et par unité tradée, en unités de PRIX.
    # Le SPREAD n'est PAS réglable : il est mesuré dans les données (colonnes
    # ask_*), donc réaliste par paire et par période.
    costs_commission_per_unit: float = Field(default=0.0, ge=0)
    history_data_dir: str = "./data/history"
    history_start_year: int = Field(default=2010, ge=1990, le=2100)
    history_instruments: list[str] = ["EURUSD"]
    database_url: str = "sqlite:///./data/pyea.db"
    models_dir: str = "./data/models"
    log_level: str = "INFO"
    log_file: str = "./logs/pyea.log"
    log_web_buffer_size: int = 500

    @property
    def ib_port(self) -> int:
        """Port IB effectif : le passage paper → live ne change que trading_mode."""
        return self.ib_port_live if self.trading_mode == "live" else self.ib_port_paper


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            f"config.yaml illisible (syntaxe YAML invalide) : {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ValueError(
            "config.yaml illisible : le contenu doit être un mapping clé/valeur."
        )
    return loaded


def _yaml_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Aplatit le YAML hiérarchique vers les champs de ``Settings``."""
    server = raw.get("server", {})
    broker = raw.get("broker", {})
    strategy = raw.get("strategy", {})
    risk = raw.get("risk", {})
    ui = raw.get("ui", {})
    costs = raw.get("costs", {})
    history = raw.get("history", {})
    storage = raw.get("storage", {})
    logging_cfg = raw.get("logging", {})

    mapping = {
        "server_host": server.get("host"),
        "server_port": server.get("port"),
        "broker_name": broker.get("name"),
        "trading_mode": broker.get("trading_mode"),
        "mt5_terminal_path": broker.get("mt5_terminal_path"),
        "strategy_name": strategy.get("name"),
        "strategy_enabled": strategy.get("enabled"),
        "ui_chart_refresh_seconds": ui.get("chart_refresh_seconds"),
        "risk_max_position_size": risk.get("max_position_size"),
        "risk_max_daily_loss_pct": risk.get("max_daily_loss_pct"),
        "risk_max_positions_per_symbol": risk.get("max_positions_per_symbol"),
        "risk_max_open_positions": risk.get("max_open_positions"),
        "costs_commission_per_unit": costs.get("commission_per_unit"),
        "history_data_dir": history.get("data_dir"),
        "history_start_year": history.get("start_year"),
        "history_instruments": history.get("instruments"),
        "database_url": storage.get("database_url"),
        "models_dir": storage.get("models_dir"),
        "log_level": logging_cfg.get("level"),
        "log_file": logging_cfg.get("file"),
        "log_web_buffer_size": logging_cfg.get("web_buffer_size"),
    }
    return {key: value for key, value in mapping.items() if value is not None}


@lru_cache
def get_settings() -> Settings:
    """Instance unique. ATTENTION à la priorité : le YAML est passé en
    arguments d'initialisation, qui PRIMENT sur .env et les variables
    d'environnement (cf. l'en-tête du module)."""
    return Settings(**_yaml_overrides(_load_yaml(CONFIG_YAML_PATH)))
