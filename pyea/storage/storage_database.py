"""Accès base de données via SQLAlchemy.

SQLite au départ ; la migration vers Postgres se fait en changeant
``storage.database_url`` dans config.yaml, sans toucher aux modèles ni
à la logique.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from pyea.config.config_settings import get_settings
from pyea.storage.storage_models import Base

_engine = None
_session_factory: sessionmaker[Session] | None = None


def init_db() -> None:
    """Crée le moteur, le dossier data/ si besoin, et les tables manquantes."""
    global _engine, _session_factory
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, future=True)
    _session_factory = sessionmaker(bind=_engine, future=True)
    Base.metadata.create_all(_engine)


def get_session() -> Session:
    """Session SQLAlchemy ; à utiliser en ``with get_session() as session:``."""
    if _session_factory is None:
        raise RuntimeError("init_db() doit être appelé au démarrage de l'application.")
    return _session_factory()
