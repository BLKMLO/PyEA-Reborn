"""Accès base de données via SQLAlchemy.

SQLite au départ ; la migration vers Postgres se fait en changeant
``storage.database_url`` dans config.yaml, sans toucher aux modèles ni
à la logique.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
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
    _add_missing_columns(_engine)


def _add_missing_columns(engine: Engine) -> None:
    """Micro-migration SQLite : ajoute les colonnes NULLABLE présentes dans les
    modèles mais absentes des tables déjà créées. ``create_all`` ne touche pas
    aux tables existantes — sans ce rattrapage, une base d'une version
    antérieure ferait planter tout ``SELECT`` sur « no such column » dès qu'on
    ajoute un champ (ex. ``training_runs.oos_profit_factor``). On se limite aux
    colonnes nullable (ajout sûr, valeur NULL sur les lignes existantes)."""
    if engine.dialect.name != "sqlite":
        return  # sur un vrai SGBD (Postgres), on passera par de vraies migrations
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}')
                )


def get_session() -> Session:
    """Session SQLAlchemy ; à utiliser en ``with get_session() as session:``."""
    if _session_factory is None:
        raise RuntimeError("init_db() doit être appelé au démarrage de l'application.")
    return _session_factory()
