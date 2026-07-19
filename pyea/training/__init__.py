"""Entraînement des stratégies : walk-forward + jobs en arrière-plan.

- ``training_walkforward.py`` : découpe temporelle et orchestration
  train/test par pli (le test = le moteur de backtest existant).
- ``training_jobs.py`` : exécution en thread, suivi de progression
  (publiée sur le bus → WebSocket), annulation.
"""

from pyea.training.training_jobs import job_manager
from pyea.training.training_walkforward import run_walkforward, split_walkforward

__all__ = ["job_manager", "run_walkforward", "split_walkforward"]
