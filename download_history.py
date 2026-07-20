"""Téléchargement des données historiques pour le backtest PyEA.

EXCEPTION à la règle « une seule commande CLI » : ce script est le seul,
avec run_server.py, à s'exécuter hors du web — usage ponctuel, car un
téléchargement complet (30+ actifs depuis 2010) prend des heures et n'a
pas sa place dans le cycle de vie du serveur.

Usage :
    python download_history.py                        # tout config.yaml (history.*)
    python download_history.py --symbols EURUSD XAUUSD
    python download_history.py --start-year 2015 --force

La logique vit dans pyea/data/data_history_downloader.py ; l'interface
de backtest rechargera les mêmes fichiers via load_history().
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from pyea.config.config_settings import get_settings
from pyea.core.core_logging import get_logger, setup_logging
from pyea.data.data_history_downloader import download_history


def main() -> None:
    # Erreurs de config lisibles (mêmes règles que run_server.py).
    from run_server import load_settings_or_die

    settings = load_settings_or_die()
    setup_logging(settings.log_level, settings.log_file, settings.log_web_buffer_size)
    logger = get_logger(__name__)

    parser = argparse.ArgumentParser(
        prog="download_history",
        description="Télécharge l'historique M1 (Dukascopy) des actifs à trader.",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=settings.history_instruments,
        help="Symboles à télécharger (défaut : history.instruments de config.yaml).",
    )
    parser.add_argument(
        "--start-year", type=int, default=settings.history_start_year,
        help="Année de départ (défaut : history.start_year de config.yaml).",
    )
    parser.add_argument(
        "--end-year", type=int, default=date.today().year,
        help="Année de fin incluse (défaut : année en cours).",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(settings.history_data_dir),
        help="Dossier de destination (défaut : history.data_dir de config.yaml).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-télécharge même les années déjà présentes sur disque.",
    )
    args = parser.parse_args()

    logger.info(
        "Téléchargement : %d symboles, %d → %d, vers %s",
        len(args.symbols), args.start_year, args.end_year, args.data_dir,
    )
    try:
        written = asyncio.run(
            download_history(
                symbols=[symbol.upper() for symbol in args.symbols],
                start_year=args.start_year,
                end_year=args.end_year,
                data_dir=args.data_dir,
                force=args.force,
            )
        )
    except (KeyError, ValueError) as exc:
        # Symbole inconnu ou années incohérentes : détecté AVANT tout
        # téléchargement — message net, pas de traceback.
        import sys

        print(f"\nERREUR : {str(exc).strip(chr(39))}", file=sys.stderr)
        sys.exit(1)
    total = sum(len(years) for years in written.values())
    logger.info("Terminé : %d fichiers année/symbole écrits.", total)


if __name__ == "__main__":
    main()
