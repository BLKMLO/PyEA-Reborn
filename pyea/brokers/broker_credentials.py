"""Identifiants broker saisis au runtime, conservés EN MÉMOIRE uniquement.

⚠ **AUCUN APPELANT AUJOURD'HUI** — module conservé en réserve, pas mort par
accident. Les deux brokers supportés ne s'authentifient PAS par login/mot de
passe : Interactive Brokers délègue à TWS / IB Gateway (déjà logué) et
MetaTrader 5 à un terminal MT5 déjà connecté. PyEA ne fait que s'attacher.
Une version antérieure prévoyait que ``InteractiveBrokersGateway.connect()``
lise ``password`` ici : cette décision a été ANNULÉE le 2026-07-20, et les
endpoints d'identifiants ont été retirés de l'API en conséquence.

Ce module n'a donc de sens que si un futur broker exige de vrais
identifiants. Sa règle tiendra toujours : ces identifiants ne sont **jamais
persistés** (ni SQLite, ni disque, ni log) — ils vivent dans ce singleton de
module et disparaissent à l'arrêt du serveur, et le mot de passe ne doit
jamais transiter par l'API en lecture.
"""

from __future__ import annotations


class BrokerCredentials:
    """Stockage volatile du couple identifiant/mot de passe du broker."""

    def __init__(self) -> None:
        self._username = ""
        self._password = ""

    def set(self, username: str, password: str) -> None:
        """Enregistre un nouveau couple identifiant/mot de passe."""
        self._username = username
        self._password = password

    def update_username(self, username: str) -> None:
        """Change le seul identifiant en gardant le mot de passe existant
        (cas « l'utilisateur ne re-saisit pas le mot de passe masqué »)."""
        self._username = username

    def clear(self) -> None:
        """Efface les identifiants (retour à l'état non configuré)."""
        self._username = ""
        self._password = ""

    @property
    def username(self) -> str:
        return self._username

    @property
    def password(self) -> str:
        """Mot de passe en clair — réservé au câblage broker (connect()).
        N'est JAMAIS renvoyé par l'API ni journalisé."""
        return self._password

    def is_configured(self) -> bool:
        """Vrai si un identifiant ET un mot de passe sont présents."""
        return bool(self._username and self._password)


#: Instance unique partagée par l'API et (à terme) la gateway broker.
broker_credentials = BrokerCredentials()
