"""Identifiants broker saisis au runtime, conservés EN MÉMOIRE uniquement.

Contexte : les paramètres machine du broker (host, ports paper/live,
client_id) vivent dans ``.env`` / ``config.yaml``. Mais l'utilisateur veut
pouvoir saisir ses **identifiants de connexion** (nom d'utilisateur + mot de
passe) depuis le dashboard, sans les écrire dans un fichier.

Décision de sécurité : ces identifiants ne sont **jamais persistés** (ni
SQLite, ni disque, ni log) — ils vivent dans ce singleton de module et
disparaissent à l'arrêt du serveur. C'est exactement le comportement
demandé (« jusqu'à ce que le serveur soit éteint »).

Singleton de module (même statut que ``event_bus`` / ``web_log_buffer``) :
l'API le lit/écrit, et le futur câblage réel de ``InteractiveBrokersGateway``
lira ``broker_credentials.password`` au moment du ``connect()``.
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
