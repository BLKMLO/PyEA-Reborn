"""Route WebSocket : flux temps réel vers le dashboard.

Le ``ConnectionManager`` est abonné au bus d'événements : tout ce qui est
publié sur les topics suivis (ticks, signaux, état EA, logs) est relayé
tel quel aux navigateurs connectés, au format
``{"topic": ..., "payload": {...}}``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pyea.core.core_events import (
    TOPIC_EA_STATUS,
    TOPIC_LOG,
    TOPIC_SIGNAL,
    TOPIC_TICK,
    event_bus,
)
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["websocket"])

RELAYED_TOPICS = (TOPIC_TICK, TOPIC_SIGNAL, TOPIC_EA_STATUS, TOPIC_LOG)


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        for client in list(self._clients):
            try:
                await client.send_json(message)
            except Exception:
                self.disconnect(client)


manager = ConnectionManager()


def wire_event_bus() -> None:
    """Abonne le manager aux topics relayés. Appelé une fois par create_app()."""

    def make_relay(topic: str):
        async def relay(payload: dict[str, Any]) -> None:
            await manager.broadcast({"topic": topic, "payload": payload})

        return relay

    for topic in RELAYED_TOPICS:
        event_bus.subscribe(topic, make_relay(topic))


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Flux descendant uniquement pour l'instant ; on garde la
            # boucle de lecture pour détecter la déconnexion.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
