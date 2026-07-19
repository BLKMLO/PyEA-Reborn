"""Jobs d'entraînement en arrière-plan.

Un entraînement walk-forward dure de quelques secondes à plusieurs
minutes (LightGBM à venir) : il tourne dans un thread dédié, jamais dans
une requête HTTP. Le suivi passe par deux canaux complémentaires :
- l'état du job en mémoire (``GET /api/training/jobs/{id}`` = polling) ;
- la progression publiée sur le bus d'événements (topic
  ``training.progress``), relayée en temps réel par le WebSocket.

Pas de Celery/Redis : un EA est mono-utilisateur et local — un thread et
un dict suffisent (décision notée dans docs/choix_techniques.md).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from pyea.core.core_events import TOPIC_TRAINING_PROGRESS, event_bus
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

# target(progress, cancelled) -> résultat sérialisable
JobTarget = Callable[[Callable[[dict[str, Any]], None], Callable[[], bool]], dict[str, Any]]


@dataclass
class TrainingJob:
    id: str
    status: str = "running"  # running | completed | failed | cancelled
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class TrainingJobManager:
    """Lance et suit les jobs. Un seul job actif à la fois : un
    entraînement sature déjà un cœur, les runs concurrents ne feraient
    que fausser les durées."""

    def __init__(self) -> None:
        self._jobs: dict[str, TrainingJob] = {}
        self._lock = threading.Lock()

    def has_running_job(self) -> bool:
        return self.current() is not None

    def current(self) -> TrainingJob | None:
        """Le job en cours d'exécution, ou ``None``. Sert à l'interface pour
        se ré-attacher à un run après un rechargement de page."""
        with self._lock:
            for job in self._jobs.values():
                if job.status == "running":
                    return job
        return None

    def start(self, target: JobTarget, loop: asyncio.AbstractEventLoop | None) -> TrainingJob:
        """Démarre ``target`` dans un thread. ``loop`` = boucle asyncio du
        serveur, utilisée pour publier la progression sur le bus."""
        job = TrainingJob(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job

        def publish(payload: dict[str, Any]) -> None:
            job.progress = payload
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    event_bus.publish(
                        TOPIC_TRAINING_PROGRESS, {"job_id": job.id, **payload}
                    ),
                    loop,
                )

        def runner() -> None:
            try:
                result = target(publish, job.cancel_event.is_set)
                job.result = result
                job.status = (
                    "cancelled" if result.get("cancelled") else "completed"
                )
            except Exception as exc:  # noqa: BLE001 — remonté au client via l'état.
                logger.exception("Job d'entraînement %s en échec.", job.id)
                job.error = str(exc)
                job.status = "failed"
            publish({"phase": "done", "status": job.status})

        threading.Thread(target=runner, name=f"training-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> TrainingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status != "running":
            return False
        job.cancel_event.set()
        return True


# Instance unique de l'application (même statut que event_bus — cf. point
# de vigilance n°1 de CLAUDE.md : à injecter via app_factory si les tests
# l'exigent un jour).
job_manager = TrainingJobManager()
