"""Point d'entrée CLI unique du projet Couleuvre.

Usage :
    python run_server.py [--host HOST] [--port PORT] [--reload]

C'est la SEULE interaction terminal prévue : tout le pilotage (config,
activation de la stratégie, graphiques, logs) se fait ensuite via
l'interface web servie par FastAPI.
"""

from __future__ import annotations

import argparse

import uvicorn

from couleuvre.config.config_settings import get_settings


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        prog="couleuvre",
        description="Démarre le serveur web de l'EA Couleuvre.",
    )
    parser.add_argument("--host", default=settings.server_host)
    parser.add_argument("--port", type=int, default=settings.server_port)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Rechargement auto du code (développement uniquement).",
    )
    args = parser.parse_args()

    uvicorn.run(
        "couleuvre.app_factory:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
