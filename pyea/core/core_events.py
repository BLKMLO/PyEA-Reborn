"""Bus d'événements asynchrone minimal (pub/sub en mémoire).

Découple les producteurs (broker, stratégie, logs) des consommateurs
(WebSocket, persistance) : personne ne connaît personne, tout le monde
parle au bus. C'est ce qui permettra d'alimenter le dashboard temps réel
sans coupler la logique de trading à FastAPI.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# Topics standard du système. On n'en déclare que de RÉELLEMENT publiés :
# `ea.status` et `log.line` ont existé sans jamais avoir de producteur — du
# câblage mort qui laissait croire à un flux temps réel inexistant. L'état de
# l'EA est servi par /api/status, les logs par /api/logs.
TOPIC_TICK = "market.tick"
TOPIC_SIGNAL = "strategy.signal"
TOPIC_TRAINING_PROGRESS = "training.progress"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Retire un abonné. Un abonné déjà absent n'est PAS une erreur : un
        arrêt appelé deux fois (double ``stop()``, rechargement) ne doit pas
        faire échouer la séquence d'arrêt."""
        handlers = self._subscribers.get(topic)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Diffuse à tous les abonnés du topic, chacun ISOLÉ des autres.

        Un abonné qui lève ne doit JAMAIS remonter au producteur : le
        producteur d'un tick est la boucle de flux de prix du broker (tâche de
        scrutation MT5, callback IB). Sans cette isolation, une erreur de
        stratégie ou de gateway tuait la boucle de prix du symbole — sans un
        seul log PyEA. On journalise l'abonné fautif et le flux continue.
        """
        handlers = list(self._subscribers.get(topic, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(payload) for handler in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result  # une annulation de tâche doit se propager
                logger.exception(
                    "Abonné au topic « %s » en échec (%s) — les autres abonnés "
                    "et le flux producteur ne sont pas interrompus.",
                    topic,
                    getattr(handler, "__qualname__", handler),
                    exc_info=result,
                )


# Bus unique de l'application.
event_bus = EventBus()
