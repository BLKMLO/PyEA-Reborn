"""Point d'entrée CLI principal de PyEA.

Usage :
    python run_server.py [--host HOST] [--port PORT] [--reload]

C'est la SEULE interaction terminal prévue (avec download_history.py) :
tout le pilotage (config, stratégie, graphiques, logs) se fait ensuite
via l'interface web servie par FastAPI.
"""

from __future__ import annotations

import argparse
import socket
import sys

import uvicorn
from pydantic import ValidationError

from pyea.config.config_settings import get_settings


def _die(message: str) -> None:
    print(f"\nERREUR : {message}", file=sys.stderr)
    sys.exit(1)


def load_settings_or_die():
    """Charge la config en transformant les erreurs en messages lisibles
    (pas de traceback Python pour une faute de frappe dans config.yaml)."""
    try:
        return get_settings()
    except ValidationError as exc:
        details = "\n".join(
            f"  - {'.'.join(str(part) for part in error['loc'])} : {error['msg']}"
            f" (valeur reçue : {error.get('input')!r})"
            for error in exc.errors()
        )
        _die(f"config.yaml (ou .env) contient des valeurs invalides :\n{details}")
    except ValueError as exc:  # YAML malformé (message déjà explicite)
        _die(str(exc))


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main() -> None:
    settings = load_settings_or_die()

    parser = argparse.ArgumentParser(
        prog="pyea",
        description="Démarre le serveur web de PyEA.",
    )
    parser.add_argument("--host", default=settings.server_host)
    parser.add_argument("--port", type=int, default=settings.server_port)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Rechargement auto du code (développement uniquement).",
    )
    args = parser.parse_args()

    if not _port_free(args.host, args.port):
        _die(
            f"le port {args.port} est déjà utilisé sur {args.host}.\n"
            "  PyEA tourne probablement déjà (une seule instance à la fois) —\n"
            "  sinon, relancer avec un autre port : python run_server.py --port 8001"
        )

    uvicorn.run(
        "pyea.app_factory:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
