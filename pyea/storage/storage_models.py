"""Modèles SQLAlchemy : état de trading, entraînements, équité et trades."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SymbolTradingState(Base):
    """Interrupteur de trading par symbole (bouton Trading/Stopped du
    dashboard). Persisté pour survivre aux redémarrages ; toute paire
    absente de la table est considérée arrêtée (défaut sûr)."""

    __tablename__ = "symbol_trading_states"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DailyEquity(Base):
    """Équité de référence d'une journée UTC (limite de perte journalière).

    Persistée pour qu'un redémarrage du serveur ne remette pas le compteur
    de perte à zéro en milieu de séance.
    """

    __tablename__ = "daily_equity"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    start_equity: Mapped[float] = mapped_column(Float)


class TrainingRun(Base):
    """Un entraînement walk-forward : paramètres, métriques out-of-sample
    et chemin des artefacts. C'est ce qui permet de comparer deux runs."""

    __tablename__ = "training_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    strategy_name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    params_json: Mapped[str] = mapped_column(String(2048), default="{}")
    folds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running")
    oos_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oos_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    artifacts_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class TradeRecord(Base):
    """Trade exécuté chez le broker (rempli au fil des exécutions)."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    broker_order_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: P&L réalisé, tel que calculé par le BROKER — renseigné uniquement sur
    #: une sortie de position (une entrée n'a pas encore de résultat).
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
