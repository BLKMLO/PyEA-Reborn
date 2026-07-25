"""Route WebSocket : flux temps réel vers le dashboard.

Le ``ConnectionManager`` est abonné au bus d'événements : tout ce qui est
publié sur les topics suivis (ticks, signaux, progression d'entraînement) est
relayé tel quel aux navigateurs connectés, au format
``{"topic": ..., "payload": {...}}``.

On ne relaie QUE des topics réellement publiés : relayer un topic sans
producteur donne l'illusion d'un flux temps réel qui n'existe pas.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pyea.core.core_events import (
    TOPIC_SIGNAL,
    TOPIC_TICK,
    TOPIC_TRAINING_PROGRESS,
    event_bus,
)
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["websocket"])

RELAYED_TOPICS = (
    TOPIC_TICK,
    TOPIC_SIGNAL,
    TOPIC_TRAINING_PROGRESS,
)


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

#: Relais actuellement abonnés au bus (topic, handler), pour pouvoir les
#: retirer à l'arrêt. Le bus est un singleton de module : sans désabonnement,
#: chaque démarrage d'application EMPILAIT un jeu de relais supplémentaire, et
#: le même tick partait N fois vers les navigateurs (visible surtout en tests
#: et sous `--reload`, qui recréent l'app plusieurs fois dans le processus).
_wired: list[tuple[str, Any]] = []


def wire_event_bus() -> None:
    """Abonne le manager aux topics relayés. Idempotent (cf. ``_wired``)."""
    if _wired:
        return

    def make_relay(topic: str):
        async def relay(payload: dict[str, Any]) -> None:
            await manager.broadcast({"topic": topic, "payload": payload})

        return relay

    for topic in RELAYED_TOPICS:
        handler = make_relay(topic)
        event_bus.subscribe(topic, handler)
        _wired.append((topic, handler))


def unwire_event_bus() -> None:
    """Retire les relais du bus. Appelé à l'arrêt de l'application."""
    for topic, handler in _wired:
        event_bus.unsubscribe(topic, handler)
    _wired.clear()


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
