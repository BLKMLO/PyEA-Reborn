"""Gestion du risque : seul module autorisé à transformer un Signal en ordre.

Le flux est strict : Strategy → Signal → RiskManager → OrderRequest → Broker.
Aucun ordre ne part au broker (réel OU simulé en backtest) sans passer ici.

Limites appliquées (section ``risk`` de config.yaml) :
- taille de position fixe (``max_position_size``) ;
- plafond de positions ouvertes **par symbole** (``max_positions_per_symbol``)
  ET **sur le compte** (``max_open_positions``) ;
- **perte journalière maximale** (``max_daily_loss_pct``) : au-delà, plus
  aucune ENTRÉE n'est autorisée ; les sorties le restent toujours.

⚠ La limite de perte journalière est une garde **LIVE**. Elle exige l'équité
réelle du compte, que seul le broker fournit — le backtest, lui, ne trade
qu'une unité nominale sur un capital synthétique, où un pourcentage d'équité
n'a aucun sens. Un backtest est donc, sur ce point, légèrement OPTIMISTE par
rapport au live : c'est assumé et documenté plutôt que simulé de travers.

À enrichir plus tard : sizing dynamique (volatilité, corrélations).
"""

from __future__ import annotations

from pyea.config.config_settings import Settings
from pyea.core.core_domain import (
    AccountState,
    OrderRequest,
    OrderSide,
    Position,
    Signal,
    SignalAction,
)
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class RiskManager:
    """Applique les limites de risque définies dans config.yaml (section risk)."""

    def __init__(self, settings: Settings) -> None:
        self._max_position_size = settings.risk_max_position_size
        self._max_daily_loss_pct = settings.risk_max_daily_loss_pct
        self._max_open_positions = settings.risk_max_open_positions
        self._max_positions_per_symbol = settings.risk_max_positions_per_symbol

    @property
    def max_position_size(self) -> float:
        """Taille nominale d'une entrée (lue par le moteur de backtest pour
        remettre son P&L à l'échelle)."""
        return self._max_position_size

    async def evaluate(
        self,
        signal: Signal,
        open_positions: list[Position],
        account: AccountState | None = None,
    ) -> OrderRequest | None:
        """Valide un signal et le convertit en ordre, ou le rejette (``None``).

        ``open_positions`` = positions actuellement ouvertes sur le compte
        (réelles en live, simulées en backtest). ``account`` = état réel du
        compte chez le broker ; fourni en live uniquement, il active la limite
        de perte journalière (cf. avertissement en tête de module).
        """
        if signal.action == SignalAction.HOLD:
            return None

        if signal.action == SignalAction.EXIT:
            position = next(
                (p for p in open_positions if p.symbol == signal.symbol), None
            )
            if position is None:
                return None  # Rien à fermer.
            side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
            return OrderRequest(
                symbol=signal.symbol, side=side, quantity=abs(position.quantity)
            )

        # --- À partir d'ici : ENTRÉES uniquement ---
        # Une sortie n'est JAMAIS bloquée par une limite : quel que soit l'état
        # du compte, on doit toujours pouvoir fermer une position.
        if self._daily_loss_reached(account):
            return None
        if not self._position_room(signal, open_positions):
            return None

        side = (
            OrderSide.BUY
            if signal.action == SignalAction.ENTER_LONG
            else OrderSide.SELL
        )
        # Les barrières proposées par la stratégie (triple-barrier) sont
        # reportées sur l'ordre : leur exécution ultérieure (TP/SL touché)
        # découle de cet ordre déjà validé, elle ne contourne pas le risque.
        return OrderRequest(
            symbol=signal.symbol,
            side=side,
            quantity=self._max_position_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

    # -- limites ------------------------------------------------------------
    def _position_room(self, signal: Signal, open_positions: list[Position]) -> bool:
        """Reste-t-il de la place, sur le symbole ET sur le compte ?

        Les deux plafonds sont distincts : ``max_positions_per_symbol`` évite
        d'empiler des entrées sur la même paire, ``max_open_positions`` borne
        l'exposition totale. Les confondre (ce qui était le cas) rendait une
        seule position ouverte bloquante pour TOUTES les autres paires armées —
        un plafond global de 1 gelait 30 paires sur 31 en live.
        """
        on_symbol = sum(1 for p in open_positions if p.symbol == signal.symbol)
        if on_symbol >= self._max_positions_per_symbol:
            logger.info(
                "Signal %s %s rejeté : %d position(s) déjà ouverte(s) sur ce "
                "symbole (max %d).",
                signal.action.value, signal.symbol,
                on_symbol, self._max_positions_per_symbol,
            )
            return False
        if len(open_positions) >= self._max_open_positions:
            logger.info(
                "Signal %s %s rejeté : %d position(s) ouverte(s) sur le compte "
                "(max %d).",
                signal.action.value, signal.symbol,
                len(open_positions), self._max_open_positions,
            )
            return False
        return True

    def _daily_loss_reached(self, account: AccountState | None) -> bool:
        """La perte du jour a-t-elle atteint le plafond configuré ?

        Sans état de compte (backtest) ou sans plafond configuré (0), la règle
        ne s'applique pas — et on ne fait SURTOUT pas semblant de l'appliquer.
        """
        if account is None or self._max_daily_loss_pct <= 0:
            return False
        loss_pct = account.day_loss_pct
        if loss_pct is None or loss_pct < self._max_daily_loss_pct:
            return False
        logger.warning(
            "PERTE JOURNALIÈRE ATTEINTE : %.2f %% (plafond %.2f %%) — plus "
            "aucune entrée aujourd'hui. Équité %.2f contre %.2f en début de "
            "journée. Les sorties restent autorisées.",
            loss_pct, self._max_daily_loss_pct,
            account.equity, account.day_start_equity,
        )
        return True
