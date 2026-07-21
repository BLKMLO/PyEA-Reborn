"""Implémentation Interactive Brokers du contrat ``BrokerGateway``.

Basée sur ``ib_async`` (fork communautaire maintenu d'ib_insync) : API
asyncio de haut niveau au-dessus de TWS/IB Gateway, bien plus simple à
maintenir que ``ibapi`` natif.

**PyEA ne s'authentifie PAS par login/mot de passe** : c'est TWS / IB
Gateway (déjà logué par l'utilisateur) qui gère le compte. PyEA se connecte
au *socket API* du terminal via hôte/port/client ID (config). Le mode
paper/live est entièrement déterminé par la config : seule la valeur de
``settings.ib_port`` change (7497 paper / 7496 live pour TWS).

``ib_async`` est importé **paresseusement** (dans les méthodes) — même
principe que MetaTrader : la gateway s'enregistre et l'app démarre même si
le paquet n'est pas installé (sandbox de dev). Une connexion sans le paquet
lève une ImportError claire (« installez ib_async »), jamais un import
cassé au démarrage. Comme pour MetaTrader et Dukascopy, **le premier run
réel (TWS ouvert, API activée) est à valider chez l'utilisateur** : la
sandbox n'a ni le paquet ni un terminal IB.

Livrés ici : cycle de vie (``connect``/``disconnect``/``is_connected``),
lecture de compte (``get_positions``/``get_account_summary``), **routage
d'ordres** (``place_order`` en bracket natif Market + TP/SL attachés OCO,
``cancel_order``) et **flux de prix** (``subscribe_market_data`` via
``reqMktData``). Comme la connexion, order routing et feed exigent un TWS /
IB Gateway réel : ils sont écrits sur le même modèle prudent (import
paresseux, échec honnête si déconnecté — jamais d'ordre ni de tick simulé)
et **restent à valider chez l'utilisateur** au premier run réel. Les
instruments supportés sont les paires **forex / métaux à 6 lettres**
(EURUSD, XAUUSD…) ; les indices (US500) restent à câbler (contrat non
forex) et sont signalés par une erreur claire.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from pyea.brokers.broker_gateway import (
    BrokerGateway,
    TickCallback,
    register_gateway,
)
from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, OrderSide, Position
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

#: Délai d'attente (s) de la connexion au socket API. Court : si TWS est
#: éteint ou l'API désactivée, on veut échouer vite avec un message clair.
_CONNECT_TIMEOUT = 8.0

#: Tags du résumé de compte IB → clés normalisées (mêmes intitulés que MT5,
#: pour une lecture homogène côté API/UI). Valeurs IB en devise de base.
_ACCOUNT_TAGS = {
    "NetLiquidation": "equity",
    "TotalCashValue": "balance",
    "FullMaintMarginReq": "margin",
    "AvailableFunds": "margin_free",
    "UnrealizedPnL": "profit",
}


def _import_ib_api() -> Any:
    """Import paresseux du module ``ib_async`` avec message clair si absent."""
    try:
        import ib_async  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de l'install
        raise ImportError(
            "Le paquet 'ib_async' est requis pour se connecter à Interactive "
            "Brokers. Installez-le : pip install ib_async, puis lancez TWS ou "
            "IB Gateway avec l'API socket activée."
        ) from exc
    return ib_async


def _import_ib() -> Any:
    """Classe ``IB`` du paquet ``ib_async`` (import paresseux)."""
    return _import_ib_api().IB


@register_gateway
class InteractiveBrokersGateway(BrokerGateway):
    name = "interactive_brokers"
    label = "Interactive Brokers"

    def __init__(self, settings: Settings) -> None:
        self._host = settings.ib_host
        self._port = settings.ib_port  # paper ou live selon trading_mode
        self._client_id = settings.ib_client_id
        self._ib: Any | None = None  # instance ib_async.IB une fois connectée
        # Souscriptions de marché vivantes : symbole → (contrat, ticker, handler).
        self._md: dict[str, tuple[Any, Any, Any]] = {}

    def connection_info(self) -> dict[str, str]:
        return {
            "Hôte": self._host,
            "Port": str(self._port),
            "Client ID": str(self._client_id),
        }

    def connection_hint(self) -> str:
        return (
            "L'authentification se fait dans TWS / IB Gateway (session déjà "
            "ouverte). PyEA s'y connecte via le socket API — hôte, port et "
            "client ID se règlent dans .env / config.yaml."
        )

    async def connect(self) -> None:
        # L'API IB ne prend PAS de login/mot de passe : l'authentification est
        # gérée par TWS / IB Gateway (déjà logué par l'utilisateur). PyEA se
        # connecte au socket API via host/port/client_id (config).
        IB = _import_ib()
        ib = IB()
        try:
            await ib.connectAsync(
                self._host,
                self._port,
                clientId=self._client_id,
                timeout=_CONNECT_TIMEOUT,
                readonly=False,
            )
        except Exception as exc:  # TWS éteint, API désactivée, port occupé…
            # Nettoyage défensif : connectAsync peut laisser un socket ouvert.
            try:
                ib.disconnect()
            except Exception:  # pragma: no cover - défensif
                pass
            raise ConnectionError(
                f"Connexion à Interactive Brokers impossible ({self._host}:"
                f"{self._port}, client {self._client_id}) : {exc}. Vérifiez que "
                "TWS / IB Gateway est lancé, l'API socket activée et le port "
                "correct (7497 paper / 7496 live)."
            ) from exc
        self._ib = ib
        account = ", ".join(ib.managedAccounts()) or "?"
        logger.info(
            "Interactive Brokers connecté — %s:%s (client %s), compte(s) %s.",
            self._host, self._port, self._client_id, account,
        )

    async def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()  # synchrone côté ib_async : coupe tous les flux
            self._ib = None
        self._md.clear()

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return bool(self._ib.isConnected())
        except Exception:  # pragma: no cover - défensif (socket coupé)
            return False

    async def get_positions(self) -> list[Position]:
        if not self.is_connected():
            return []
        raw = await self._ib.reqPositionsAsync()
        if not raw:
            return []
        # Le P&L latent n'est pas porté par les positions : on le récupère du
        # portefeuille (peuplé par les mises à jour de compte), indexé par conId.
        pnl_by_conid = {
            item.contract.conId: item.unrealizedPNL
            for item in self._ib.portfolio()
        }
        positions: list[Position] = []
        for p in raw:
            if not p.position:  # position soldée (0) — ignorée
                continue
            contract = p.contract
            positions.append(
                Position(
                    symbol=contract.localSymbol or contract.symbol,
                    quantity=p.position,  # déjà signé (long > 0, short < 0)
                    average_price=p.avgCost,
                    unrealized_pnl=pnl_by_conid.get(contract.conId),
                )
            )
        return positions

    async def get_account_summary(self) -> dict[str, float]:
        if not self.is_connected():
            return {}
        values = await self._ib.accountSummaryAsync()
        summary: dict[str, float] = {}
        for av in values:
            key = _ACCOUNT_TAGS.get(av.tag)
            if key is None:
                continue
            try:
                summary[key] = float(av.value)
            except (TypeError, ValueError):  # pragma: no cover - défensif
                continue
        return summary

    # --- Routage d'ordres ---
    async def place_order(self, order: OrderRequest) -> str:
        """Envoie un ordre en **bracket natif** : entrée Market + TP/SL attachés.

        Le parent est un ordre au marché (l'exécution immédiate en live est
        l'équivalent de la décision à la clôture de bougie du backtest). Les
        barrières triple-barrier (validées par le RiskManager) deviennent des
        ordres enfants au MÊME ``parentId`` : TWS les groupe automatiquement en
        OCA (le TP annule le SL et réciproquement). On ne simule jamais un
        envoi : broker déconnecté → ``ConnectionError`` explicite.
        """
        if not self.is_connected():
            raise ConnectionError(
                "Interactive Brokers non connecté : impossible d'envoyer un ordre."
            )
        ib_api = _import_ib_api()
        contract = await self._resolve_contract(order.symbol)
        action = order.side.value  # "BUY" / "SELL"
        reverse = "SELL" if order.side == OrderSide.BUY else "BUY"
        quantity = abs(order.quantity)

        parent = ib_api.MarketOrder(action, quantity)
        parent.orderId = self._ib.client.getReqId()
        parent.transmit = False
        bracket = [parent]
        if order.take_profit is not None:
            bracket.append(
                ib_api.LimitOrder(
                    reverse, quantity, order.take_profit,
                    orderId=self._ib.client.getReqId(),
                    parentId=parent.orderId, transmit=False,
                )
            )
        if order.stop_loss is not None:
            bracket.append(
                ib_api.StopOrder(
                    reverse, quantity, order.stop_loss,
                    orderId=self._ib.client.getReqId(),
                    parentId=parent.orderId, transmit=False,
                )
            )
        # Seul le DERNIER ordre porte transmit=True → envoi atomique du groupe.
        bracket[-1].transmit = True
        for child in bracket:
            self._ib.placeOrder(contract, child)
        logger.info(
            "Ordre IB soumis — %s %s x%s (id %s)%s.",
            action, order.symbol, quantity, parent.orderId,
            " + TP/SL" if len(bracket) > 1 else "",
        )
        return str(parent.orderId)

    async def cancel_order(self, order_id: str) -> None:
        if not self.is_connected():
            raise ConnectionError(
                "Interactive Brokers non connecté : impossible d'annuler un ordre."
            )
        try:
            target = int(order_id)
        except (TypeError, ValueError):
            raise ValueError(f"Identifiant d'ordre IB invalide : {order_id!r}.")
        for trade in self._ib.openTrades():
            if trade.order.orderId == target:
                self._ib.cancelOrder(trade.order)
                logger.info("Ordre IB %s annulé.", order_id)
                return
        logger.warning(
            "Ordre IB %s introuvable (déjà exécuté ou annulé ?).", order_id
        )

    # --- Flux de prix ---
    async def subscribe_market_data(self, symbol: str, on_tick: TickCallback) -> None:
        """S'abonne au flux de prix d'un symbole (``reqMktData``).

        Le callback ib_async est SYNCHRONE (appelé dans la boucle asyncio) : on
        y planifie ``on_tick`` (coroutine) sans bloquer le flux de ticks. Les
        prix non prêts (NaN) sont ignorés — jamais de tick fabriqué.
        """
        if not self.is_connected():
            raise ConnectionError(
                "Interactive Brokers non connecté : flux de prix indisponible."
            )
        if symbol in self._md:
            return  # déjà abonné
        contract = await self._resolve_contract(symbol)
        ticker = self._ib.reqMktData(contract, "", False, False)

        def _handler(updated: Any) -> None:
            price = updated.marketPrice()
            if price is None or not math.isfinite(price):
                return
            volume = updated.volume
            tick = TickData(
                symbol=symbol,
                price=float(price),
                volume=float(volume) if volume and math.isfinite(volume) else None,
                timestamp=updated.time or datetime.now(timezone.utc),
            )
            asyncio.ensure_future(on_tick(tick))

        ticker.updateEvent += _handler
        self._md[symbol] = (contract, ticker, _handler)
        logger.info("Flux de marché IB abonné — %s.", symbol)

    async def unsubscribe_market_data(self, symbol: str) -> None:
        entry = self._md.pop(symbol, None)
        if entry is None:
            return
        contract, ticker, handler = entry
        try:
            ticker.updateEvent -= handler
        except Exception:  # pragma: no cover - défensif (event déjà nettoyé)
            pass
        if self.is_connected():
            self._ib.cancelMktData(contract)
        logger.info("Flux de marché IB coupé — %s.", symbol)

    async def _resolve_contract(self, symbol: str) -> Any:
        """Résout un symbole en contrat IB qualifié.

        Supporté pour l'instant : paires **forex / métaux à 6 lettres**
        (EURUSD, XAUUSD…) via ``Forex``. Les indices (US500) et autres types
        restent à câbler — erreur claire plutôt qu'un contrat deviné.
        """
        ib_api = _import_ib_api()
        normalized = symbol.replace("/", "").upper()
        if len(normalized) == 6 and normalized.isalpha():
            contract = ib_api.Forex(normalized)
        else:
            raise ValueError(
                f"Instrument « {symbol} » non supporté par la gateway IB pour "
                "l'instant (paires forex/métaux à 6 lettres uniquement ; les "
                "indices comme US500 restent à câbler)."
            )
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Contrat IB non résolu pour « {symbol} ».")
        return qualified[0]
