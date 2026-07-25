"""Le bus d'événements découple les producteurs des consommateurs — et doit
les ISOLER : un abonné qui plante ne doit jamais remonter au producteur.

C'est vital en live : le producteur d'un tick est la boucle de flux de prix du
broker (tâche de scrutation MT5, callback IB). Sans isolation, une erreur de
stratégie tuait la boucle de prix du symbole, en silence.
"""

import asyncio

import pytest

from pyea.core.core_events import EventBus


def test_un_abonne_en_echec_ne_remonte_pas_au_producteur() -> None:
    bus = EventBus()
    recus: list[dict] = []

    async def casse(_payload: dict) -> None:
        raise RuntimeError("stratégie plantée")

    async def sain(payload: dict) -> None:
        recus.append(payload)

    bus.subscribe("market.tick", casse)
    bus.subscribe("market.tick", sain)

    # Ne lève pas : le producteur (flux de prix) survit à l'abonné fautif...
    asyncio.run(bus.publish("market.tick", {"symbol": "EURUSD"}))
    # ...et les autres abonnés sont servis quand même.
    assert recus == [{"symbol": "EURUSD"}]


def test_publications_suivantes_toujours_delivrees() -> None:
    # Le flux ne doit pas se dégrader : un abonné qui plante en boucle ne
    # désabonne rien et n'empêche pas les ticks suivants d'arriver.
    bus = EventBus()
    recus: list[dict] = []

    async def casse(_payload: dict) -> None:
        raise ValueError("boom")

    async def sain(payload: dict) -> None:
        recus.append(payload)

    bus.subscribe("market.tick", casse)
    bus.subscribe("market.tick", sain)

    async def scenario() -> None:
        for i in range(3):
            await bus.publish("market.tick", {"n": i})

    asyncio.run(scenario())
    assert recus == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_annulation_de_tache_se_propage() -> None:
    # Exception à l'isolation : une CancelledError est un ordre d'arrêt de la
    # boucle asyncio, pas une erreur d'abonné — elle DOIT se propager.
    bus = EventBus()

    async def annule(_payload: dict) -> None:
        raise asyncio.CancelledError

    bus.subscribe("market.tick", annule)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bus.publish("market.tick", {}))


def test_desabonnement_idempotent() -> None:
    # Un double stop() (ou un arrêt après rechargement) ne doit pas faire
    # échouer la séquence d'arrêt sur un ValueError de list.remove.
    bus = EventBus()

    async def handler(_payload: dict) -> None:  # pragma: no cover - jamais appelé
        pass

    bus.subscribe("market.tick", handler)
    bus.unsubscribe("market.tick", handler)
    bus.unsubscribe("market.tick", handler)  # ne lève pas
    bus.unsubscribe("topic.inconnu", handler)  # ne lève pas
