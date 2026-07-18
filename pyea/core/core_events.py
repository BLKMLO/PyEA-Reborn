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

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# Topics standard du système.
TOPIC_TICK = "market.tick"
TOPIC_SIGNAL = "strategy.signal"
TOPIC_EA_STATUS = "ea.status"
TOPIC_LOG = "log.line"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic].remove(handler)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        handlers = list(self._subscribers.get(topic, []))
        if handlers:
            await asyncio.gather(*(handler(payload) for handler in handlers))


# Bus unique de l'application.
event_bus = EventBus()
