# Données historiques (backtest)

## Téléchargement

```bash
python download_history.py                          # tout config.yaml (history.*)
python download_history.py --symbols EURUSD XAUUSD --start-year 2015
python download_history.py --force                  # re-télécharge l'existant
```

Télécharge les bougies **M1 bid/ask** depuis le flux public Dukascopy
(gratuit, sans compte) pour les 31 instruments de `config.yaml:history`
(majeures, croisées, XAUUSD/XAGUSD, US500), depuis `history.start_year`
(2010 par défaut, réglable). Le téléchargement est **incrémental** : les
années déjà présentes sur disque sont sautées (sauf `--force` ; l'année
en cours est toujours rafraîchie).

Prévoir plusieurs heures et ~10-20 Go pour un run complet. L'historique
des indices (US500) démarre souvent après 2010 chez Dukascopy ; les
années absentes sont signalées et sautées, sans bloquer le reste.

**Premier lancement** : vérifier dans les logs la ligne émise par année
écrite (elle inclut la première bougie) — les prix doivent être plausibles
(EURUSD ≈ 1.x, XAUUSD ≈ 1000-3000…). Un prix absurde signale un facteur
décimal à corriger dans `INSTRUMENT_SPECS`.

## Stockage

```
data/history/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet
```

Un dossier par actif, un fichier Parquet par année. **Supprimer une
paire = supprimer son dossier** ; supprimer une période = supprimer les
fichiers d'années. Colonnes : `bid_open/high/low/close`,
`ask_open/high/low/close`, `volume` — index UTC.

## Lecture et timeframes

```python
from pyea.data.data_history_downloader import load_history, resample_history

m1 = load_history(Path("data/history"), "EURUSD", start, end)
h1 = resample_history(m1, "H1")   # M5, M15, M30, H1, H4, D1, W1, MN1
```

Seul le M1 est stocké (la granularité la plus fine) ; tout timeframe
supérieur en dérive à la demande : open = première M1 de la période,
high = max, low = min, close = dernière, volume = somme, bid et ask
agrégés séparément, périodes vides (week-ends) retirées.

## Ajouter un instrument

1. Ajouter le symbole dans `config.yaml:history.instruments`.
2. S'il est inconnu du module : ajouter une ligne dans
   `INSTRUMENT_SPECS` (`pyea/data/data_history_downloader.py`) avec l'id
   Dukascopy et le facteur décimal (5 forex, 3 paires JPY/métaux/indices).

## Pièges du flux Dukascopy (déjà gérés, ne pas « corriger »)

- Les **mois sont 0-based** dans les URLs (`00` = janvier).
- Les prix sont des **entiers** : prix réel = entier ÷ 10^facteur.
- **404 = normal** : week-end, jour férié, ou antérieur au début de
  l'historique de l'instrument.
