# Justification des choix techniques

Les décisions datées vivent dans le journal de `CLAUDE.md` ; ce document
développe le raisonnement pour celles qui structurent le projet.

- **REST + WebSocket** (plutôt que du polling ou du SSE) : REST couvre les
  actions ponctuelles (config, historique) avec HTMX sans build front ;
  le WebSocket porte le flux continu (prix, signaux, statut) nécessaire à
  la mise à jour live des graphiques — SSE aurait suffi pour du
  descendant pur, mais le WS laisse la porte ouverte aux commandes
  temps réel depuis le dashboard.

- **`ib_async` plutôt que `ibapi` natif ou `ib_insync`** : `ibapi` impose
  de gérer soi-même threading, callbacks et reconnexions (beaucoup de code
  fragile) ; `ib_insync` n'est plus maintenu depuis le décès de son
  auteur (2024) ; `ib_async` en est le fork communautaire maintenu, avec
  la même API asyncio de haut niveau — le meilleur rapport
  simplicité/maintenance aujourd'hui.

- **SQLite + SQLAlchemy** : zéro infra au départ ; la migration Postgres
  se réduit à changer `storage.database_url`.

- **HTMX + Tailwind + Chart.js, vendorisés en local** : aucun build front,
  dashboard et formulaires triviaux à écrire ; les libs sont servies
  depuis `static/vendor/` (pas de CDN au runtime — un dashboard de
  trading sur VPS doit fonctionner sans internet sortant, avec des
  versions déterministes). Tailwind ne gère que le style et n'interfère
  pas avec Chart.js, dont l'initialisation est centralisée dans
  `static/js/charts.js` et alimentée par `/api/charts/*`.

- **Dukascopy comme source d'historique** (plutôt qu'IB ou yfinance) :
  flux public gratuit sans compte, M1 remontant avant 2010 sur le forex ;
  IB exige TWS connecté et impose des limites de débit sévères sur
  l'historique ; yfinance n'a pas d'intraday ancien. Détails :
  [donnees_historiques.md](donnees_historiques.md).
