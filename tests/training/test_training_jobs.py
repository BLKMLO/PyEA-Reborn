"""Tests du gestionnaire de jobs (thread, statut, annulation)."""

import time

from pyea.training.training_jobs import TrainingJobManager


def _wait(job, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.02)


def test_job_complete() -> None:
    manager = TrainingJobManager()
    job = manager.start(lambda progress, cancelled: {"ok": True, "cancelled": False}, None)
    _wait(job)
    assert job.status == "completed"
    assert job.result == {"ok": True, "cancelled": False}
    assert manager.get(job.id) is job


def test_job_echec_capture_l_erreur() -> None:
    manager = TrainingJobManager()

    def failing(progress, cancelled):
        raise RuntimeError("boum")

    job = manager.start(failing, None)
    _wait(job)
    assert job.status == "failed"
    assert "boum" in job.error


def test_current_expose_le_job_en_cours() -> None:
    """`current()` sert au ré-attachement de l'interface après un
    rechargement de page pendant un run."""
    manager = TrainingJobManager()
    assert manager.current() is None

    def long_running(progress, cancelled):
        while not cancelled():
            time.sleep(0.02)
        return {"cancelled": True}

    job = manager.start(long_running, None)
    assert manager.current() is job
    manager.cancel(job.id)
    _wait(job)
    assert manager.current() is None


def test_job_annulation() -> None:
    manager = TrainingJobManager()

    def long_running(progress, cancelled):
        while not cancelled():
            time.sleep(0.02)
        return {"cancelled": True}

    job = manager.start(long_running, None)
    assert manager.has_running_job() is True
    assert manager.cancel(job.id) is True
    _wait(job)
    assert job.status == "cancelled"
    assert manager.has_running_job() is False
    # Un job terminé ne peut plus être annulé.
    assert manager.cancel(job.id) is False
