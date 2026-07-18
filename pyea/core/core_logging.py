"""Logging structuré : fichier rotatif + console + tampon mémoire pour le web.

``WebLogBuffer`` conserve les N dernières lignes en mémoire ; l'API REST
les sert au dashboard, et le bus d'événements pourra pousser chaque
nouvelle ligne sur le WebSocket.
"""

from __future__ import annotations

import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class WebLogBuffer(logging.Handler):
    """Handler qui garde les dernières lignes de log pour l'interface web."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter(LOG_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))

    def tail(self, count: int = 100) -> list[str]:
        return list(self.records)[-count:]


# Instance unique, branchée par setup_logging() et lue par l'API.
web_log_buffer = WebLogBuffer()


def setup_logging(level: str, log_file: str, web_buffer_size: int = 500) -> None:
    """Configure le logger racine. À appeler une seule fois, au démarrage."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    web_log_buffer.records = deque(web_log_buffer.records, maxlen=web_buffer_size)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.addHandler(web_log_buffer)


def get_logger(name: str) -> logging.Logger:
    """Point d'accès unique aux loggers (``get_logger(__name__)``)."""
    return logging.getLogger(name)
