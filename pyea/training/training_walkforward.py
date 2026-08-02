"""Walk-forward : la SEULE validation honnête d'une stratégie de trading.

Principe (fenêtre expansive, ordre temporel strict, jamais de split
aléatoire) : la seconde moitié de l'historique est découpée en ``n_folds``
blocs de test consécutifs ; le pli i s'entraîne sur TOUT ce qui précède
son bloc de test, puis est backtesté sur ce bloc (out-of-sample).

La métrique qui compte est l'agrégat OUT-OF-SAMPLE (concaténation des
blocs de test) — les métriques in-sample ne servent qu'à diagnostiquer le
surapprentissage.

Le test de chaque pli passe par le MÊME moteur que la page backtest
(``BacktestEngine``) : flux Strategy → Signal → RiskManager → OrderRequest.

**Mode poolé (modèle unique multi-actifs)** — ``run_walkforward_pooled`` :
UN modèle par pli, entraîné sur les données de TOUS les actifs (le split
temporel est calculé sur la plage commune, chaque actif est tranché aux
mêmes dates), puis évalué actif par actif. Les agrégats OOS restent honnêtes
: taux de gain et profit factor recalculés sur TOUS les trades de TOUS les
actifs (jamais de moyenne de ratios), P&L et coûts sommés, courbe d'équité
= somme des P&L cumulés par actif sur la timeline commune. Simplification
assumée et documentée : chaque actif est backtesté avec son propre moteur —
les limites du RiskManager s'appliquent par actif, l'exposition croisée
inter-actifs n'est pas plafonnée dans le walk-forward.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from pyea.backtest import BacktestEngine
from pyea.core.core_logging import get_logger
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy

logger = get_logger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]

#: Bougies de contexte (fin du bloc d'entraînement) données à la chauffe de la
#: stratégie avant chaque bloc de test. Confortablement au-dessus des fenêtres
#: de features (la plus longue vaut 50, ``WARMUP_BARS`` = 60) pour que les
#: indicateurs RÉCURSIFS (EMA, lissage de Wilder), qui n'ont pas de fenêtre
#: finie, soient stabilisés dès la première bougie évaluée.
OOS_CONTEXT_BARS = 300


@dataclass
class WalkForwardFold:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    train_report: dict[str, Any] | None = None  # Retour de strategy.train()
    test_stats: dict[str, Any] = field(default_factory=dict)
    # AUC recalculée sur le bloc de test (skill réel du modèle, ≈ 0,5 = aucun
    # pouvoir prédictif). L'écart avec l'AUC in-sample mesure le
    # surapprentissage — bien plus directement que le taux de gain OOS, qui
    # dépend en plus des coûts et du tie-break d'exécution.
    oos_auc: float | None = None
    # Mode poolé : stats OOS VENTILÉES par actif (le pli agrégé reste dans
    # test_stats). Vide en mono-symbole.
    per_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)


def split_walkforward(
    frame: pd.DataFrame, n_folds: int, initial_train_fraction: float = 0.5
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Découpe en plis (train expansif, test consécutifs).

    Les blocs de test partagent équitablement la fin de l'historique
    (après ``initial_train_fraction``) ; le train du pli i = tout ce qui
    précède son bloc de test.
    """
    if n_folds < 1:
        raise ValueError("n_folds doit être ≥ 1.")
    first_test_index = int(len(frame) * initial_train_fraction)
    test_span = (len(frame) - first_test_index) // n_folds
    if first_test_index == 0 or test_span == 0:
        raise ValueError(
            f"Historique trop court ({len(frame)} bougies) pour {n_folds} plis."
        )
    folds = []
    for i in range(n_folds):
        test_start = first_test_index + i * test_span
        test_end = test_start + test_span if i < n_folds - 1 else len(frame)
        folds.append((frame.iloc[:test_start], frame.iloc[test_start:test_end]))
    return folds


def _split_frames(
    frames: dict[str, pd.DataFrame], n_folds: int, initial_train_fraction: float = 0.5
) -> list[tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]]:
    """Découpe multi-actifs : mêmes bornes de plis pour TOUS les actifs.

    La découpe est calculée sur la timeline commune (union des index, après
    restriction à la plage de dates couverte par tous — un actif qui ne
    couvre pas la plage commune est écarté avec un warning, jamais étiré).
    Chaque actif est ensuite tranché aux MÊMES timestamps : le pli i
    entraîne sur tout ce qui précède son bloc de test, pour tous les actifs.
    """
    if len(frames) == 1:
        symbol = next(iter(frames))
        return [
            ({symbol: train}, {symbol: test})
            for train, test in split_walkforward(
                frames[symbol], n_folds, initial_train_fraction
            )
        ]

    # Plage commune : intersection des couvertures. Un actif disjoint des
    # autres (ex. historique d'une autre époque) rendrait l'intersection
    # VIDE — on l'écarte plutôt que de sacrifier tout le run.
    pool = dict(frames)
    while len(pool) > 1:
        common_start = max(frame.index[0] for frame in pool.values())
        common_end = min(frame.index[-1] for frame in pool.values())
        if common_start < common_end:
            break
        latest_start = max(pool, key=lambda s: pool[s].index[0])
        earliest_end = min(pool, key=lambda s: pool[s].index[-1])
        sans_latest = {s: f for s, f in pool.items() if s != latest_start}
        if (
            max(f.index[0] for f in sans_latest.values())
            < min(f.index[-1] for f in sans_latest.values())
        ):
            dropout = latest_start
        else:
            dropout = earliest_end
        logger.warning(
            "Walk-forward poolé : %s écarté (couverture disjointe des autres "
            "actifs — pas de plage commune).", dropout,
        )
        del pool[dropout]
    common_start = max(frame.index[0] for frame in pool.values())
    common_end = min(frame.index[-1] for frame in pool.values())
    trimmed: dict[str, pd.DataFrame] = {}
    for symbol, frame in sorted(pool.items()):
        slice_ = frame[(frame.index >= common_start) & (frame.index <= common_end)]
        if len(slice_) >= 2:
            trimmed[symbol] = slice_
        else:
            logger.warning(
                "Walk-forward poolé : %s écarté (ne couvre pas la plage commune "
                "%s → %s).", symbol, common_start, common_end,
            )
    if not trimmed:
        raise ValueError(
            "Aucun actif ne couvre la plage commune — vérifier les historiques."
        )

    timeline = pd.DatetimeIndex([])
    for frame in trimmed.values():
        timeline = timeline.union(frame.index)
    n = len(timeline)
    first_test = int(n * initial_train_fraction)
    test_span = (n - first_test) // n_folds
    if first_test == 0 or test_span == 0:
        raise ValueError(
            f"Historique commun trop court ({n} bougies) pour {n_folds} plis."
        )

    folds = []
    for i in range(n_folds):
        ts_start = timeline[first_test + i * test_span]
        ts_end = (
            timeline[first_test + (i + 1) * test_span] if i < n_folds - 1 else None
        )
        train_frames: dict[str, pd.DataFrame] = {}
        test_frames: dict[str, pd.DataFrame] = {}
        for symbol, frame in trimmed.items():
            train = frame[frame.index < ts_start]
            test = frame[frame.index >= ts_start]
            if ts_end is not None:
                test = test[test.index < ts_end]
            if len(train):
                train_frames[symbol] = train
            if len(test):
                test_frames[symbol] = test
        folds.append((train_frames, test_frames))
    return folds


def run_walkforward(
    strategy_factory: Callable[[], Strategy],
    risk_manager: RiskManager,
    symbol: str,
    frame: pd.DataFrame,
    timeframe: str,
    n_folds: int,
    artifacts_dir: Path,
    progress: ProgressCallback,
    cancelled: CancelCheck,
    commission_per_unit: float = 0.0,
    initial_capital: float = 10000.0,
    leverage: float = 30.0,
) -> dict[str, Any]:
    """Walk-forward MONO-actif (signature historique, inchangée)."""
    return _run(
        strategy_factory, risk_manager, {symbol: frame}, symbol, timeframe,
        n_folds, artifacts_dir, progress, cancelled, commission_per_unit,
        initial_capital, leverage, pooled=False,
    )


def run_walkforward_pooled(
    strategy_factory: Callable[[], Strategy],
    risk_manager: RiskManager,
    frames: dict[str, pd.DataFrame],
    timeframe: str,
    n_folds: int,
    artifacts_dir: Path,
    progress: ProgressCallback,
    cancelled: CancelCheck,
    commission_per_unit: float = 0.0,
    initial_capital: float = 10000.0,
    leverage: float = 30.0,
) -> dict[str, Any]:
    """Walk-forward POOLÉ : UN modèle par pli, entraîné sur tous les actifs.

    ``frames`` = ``{symbole: historique ré-échantillonné}``. Le rapport est
    enregistré sous le symbole sentinelle ``ALL`` (un seul modèle pour tous)
    et ajoute ``symbols`` (actifs retenus) + ``oos_by_symbol`` (agrégats OOS
    honnêtes par actif) aux clés habituelles.
    """
    if len(frames) < 2:
        raise ValueError("Le mode poolé exige au moins deux actifs.")
    return _run(
        strategy_factory, risk_manager, frames, "ALL", timeframe,
        n_folds, artifacts_dir, progress, cancelled, commission_per_unit,
        initial_capital, leverage, pooled=True,
    )


def _run(
    strategy_factory: Callable[[], Strategy],
    risk_manager: RiskManager,
    frames: dict[str, pd.DataFrame],
    label: str,
    timeframe: str,
    n_folds: int,
    artifacts_dir: Path,
    progress: ProgressCallback,
    cancelled: CancelCheck,
    commission_per_unit: float,
    initial_capital: float,
    leverage: float,
    pooled: bool,
) -> dict[str, Any]:
    """Exécute le walk-forward complet ; conçu pour tourner dans un thread.

    Retourne le rapport final : plis, stats out-of-sample agrégées,
    courbe d'équité OOS concaténée. Écrit ``metadata.json`` (et, plus
    tard, les modèles retournés par ``strategy.train``) dans
    ``artifacts_dir``.
    """
    mono = not pooled
    folds_frames = _split_frames(frames, n_folds)
    # Actifs RÉELLEMENT retenus : ``_split_frames`` écarte ceux qui ne
    # couvrent pas la plage commune. Se fier à ``frames`` (la demande) faisait
    # figurer un actif écarté dans le rapport avec « 0 trade / 0,00 » —
    # indiscernable d'un actif effectivement tradé sur lequel le modèle s'est
    # abstenu, et donc une affirmation fabriquée.
    retained = sorted(
        {s for train, test in folds_frames for s in (*train, *test)}
    )
    dropped = sorted(set(frames) - set(retained))
    if dropped:
        logger.warning(
            "Walk-forward : %s écarté(s) du run (couverture incompatible) — "
            "absent(s) du modèle ET du rapport.", ", ".join(dropped),
        )
    # Un run POOLÉ dont la découpe ne laisse qu'un actif n'est plus un modèle
    # multi-actifs : il serait pourtant enregistré sous la sentinelle ``ALL``
    # et servi à TOUTES les paires en live. On échoue avec un message clair
    # plutôt que de livrer un modèle mono-actif sous une étiquette mutualisée.
    if pooled and len(retained) < 2:
        raise ValueError(
            "Mode poolé : un seul actif survit à la découpe "
            f"({', '.join(retained) or 'aucun'}) — les autres n'ont pas de "
            f"plage commune ({', '.join(dropped)}). Un modèle « multi-actifs » "
            "ne peut pas être entraîné sur un seul actif : téléchargez des "
            "historiques qui se recouvrent, ou entraînez par actif."
        )
    folds: list[WalkForwardFold] = []
    oos_equity: list[dict[str, Any]] = []
    oos_trade_pnls: list[float] = []
    per_symbol_pnls: dict[str, list[float]] = {s: [] for s in retained}
    oos_offset = 0.0

    for i, (train_frames, test_frames) in enumerate(folds_frames):
        if cancelled():
            logger.info("Walk-forward annulé au pli %d/%d.", i + 1, n_folds)
            return _report(label, timeframe, folds, oos_equity, oos_trade_pnls, cancelled=True)

        fold = WalkForwardFold(
            index=i + 1,
            train_start=min(f.index[0] for f in train_frames.values()).isoformat(),
            train_end=max(f.index[-1] for f in train_frames.values()).isoformat(),
            test_start=min(f.index[0] for f in test_frames.values()).isoformat(),
            test_end=max(f.index[-1] for f in test_frames.values()).isoformat(),
            train_bars=sum(len(f) for f in train_frames.values()),
            test_bars=sum(len(f) for f in test_frames.values()),
        )

        progress({"fold": i + 1, "total": n_folds, "phase": "train",
                  "message": f"Pli {i + 1}/{n_folds} : entraînement…"})
        strategy = strategy_factory()
        # Chaque pli sauvegarde son modèle (model.txt + features.json) dans
        # un sous-dossier — artefacts inspectables. Mono-actif : le frame seul
        # (interface historique) ; poolé : le dict {symbole: frame}.
        train_input: Any = (
            next(iter(train_frames.values())) if mono else train_frames
        )
        fold.train_report = asyncio.run(
            strategy.train(
                train_input,
                {"fold": i + 1, "model_dir": str(artifacts_dir / f"fold_{i + 1}")},
            )
        )

        # Re-vérifié entre les phases : un pli peut durer des minutes, ne pas
        # attendre le pli suivant pour honorer une annulation.
        if cancelled():
            logger.info("Walk-forward annulé après l'entraînement du pli %d/%d.", i + 1, n_folds)
            folds.append(fold)
            return _report(label, timeframe, folds, oos_equity, oos_trade_pnls, cancelled=True)

        progress({"fold": i + 1, "total": n_folds, "phase": "test",
                  "message": f"Pli {i + 1}/{n_folds} : backtest out-of-sample…"})

        # Un backtest OOS PAR ACTIF, sur sa tranche de test. La fin du bloc
        # d'ENTRAÎNEMENT sert de contexte de chauffe : sans elle, les ~60
        # premières bougies de chaque bloc de test donnaient des features NaN,
        # donc zéro trade — chaque pli perdait le début de sa période OOS. Ces
        # bougies ne sont PAS rejouées (aucune décision, aucun trade, aucune
        # statistique) et sont strictement antérieures au bloc : pas de fuite.
        results: dict[str, Any] = {}
        warmups: dict[str, pd.DataFrame] = {}
        for symbol, test_frame in test_frames.items():
            train_frame = train_frames.get(symbol)
            context = (
                train_frame.tail(OOS_CONTEXT_BARS)
                if train_frame is not None and len(train_frame)
                else None
            )
            engine = BacktestEngine(
                strategy, risk_manager, commission_per_unit, initial_capital,
                leverage,
            )
            results[symbol] = engine.run(symbol, test_frame, timeframe, context=context)
            warmup_frame = (
                pd.concat([context, test_frame]) if context is not None else test_frame
            )
            warmups[symbol] = warmup_frame[~warmup_frame.index.duplicated(keep="last")]

        # AUC out-of-sample du pli : le moteur a chauffé la stratégie sur
        # contexte + test (mêmes features qu'à l'exécution) ; on restreint
        # l'évaluation au bloc de test. Poolé : UNE AUC sur les prédictions
        # de tous les actifs mises en commun. None pour une stratégie sans
        # modèle.
        if mono:
            symbol = next(iter(results))
            fold.oos_auc = strategy.oos_auc(warmups[symbol], test_frames[symbol].index)
        else:
            fold.oos_auc = strategy.oos_auc(
                warmups, {s: f.index for s, f in test_frames.items()}
            )

        # Stats du pli : mono = stats du moteur telles quelles ; poolé =
        # agrégat honnête sur tous les actifs + ventilation par actif.
        fold_curve = _pooled_pnl_curve(results, initial_capital)
        if mono:
            fold.test_stats = results[next(iter(results))].stats
        else:
            fold.per_symbol = {s: r.stats for s, r in results.items()}
            fold.test_stats = _aggregate_symbol_stats(results, fold_curve)

        # Courbe OOS concaténée : chaque pli repart du cumul précédent.
        for timestamp, pnl in fold_curve:
            oos_equity.append(
                {"time": timestamp.isoformat(), "equity": round(oos_offset + pnl, 5)}
            )
        oos_offset += sum(r.stats["total_pnl"] for r in results.values())
        # P&L de chaque trade OOS : sert à agréger un profit factor EXACT sur
        # l'ensemble des plis (gains bruts / pertes brutes) — on ne moyenne
        # jamais les ratios par pli (mathématiquement faux), ni par actif.
        for symbol, result in results.items():
            oos_trade_pnls.extend(trade.pnl for trade in result.trades)
            per_symbol_pnls[symbol].extend(trade.pnl for trade in result.trades)
        folds.append(fold)

    report = _report(
        label, timeframe, folds, oos_equity, oos_trade_pnls, cancelled=False,
        symbols=retained,
        dropped_symbols=dropped,
        by_symbol_pnls=None if mono else per_symbol_pnls,
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "metadata.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report


def _pooled_pnl_curve(
    results: dict[str, Any], initial_capital: float
) -> list[tuple[pd.Timestamp, float]]:
    """Courbe de P&L cumulé d'un pli, SOMMÉE sur les actifs.

    La courbe du moteur est la VALEUR DU COMPTE (elle part du capital
    initial) : on retranche le capital pour retrouver un P&L cumulé par
    actif, puis on somme sur la timeline commune (réindexation + report de
    la dernière valeur connue — un actif sans bougie à un timestamp garde
    son cumul). Mono-actif : la courbe de P&L du moteur, inchangée.
    """
    curves: list[pd.Series] = []
    for result in results.values():
        if not result.equity_curve:
            continue
        index = pd.DatetimeIndex([ts for ts, _ in result.equity_curve])
        values = [value - initial_capital for _, value in result.equity_curve]
        curve = pd.Series(values, index=index)
        # La bougie fantôme du moteur répète le dernier timestamp : on garde
        # la dernière valeur (un index dupliqué casserait l'alignement).
        curves.append(curve[~curve.index.duplicated(keep="last")])
    if not curves:
        return []
    timeline = curves[0].index
    for curve in curves[1:]:
        timeline = timeline.union(curve.index)
    total = pd.Series(0.0, index=timeline)
    for curve in curves:
        total = total + curve.reindex(timeline).ffill().fillna(0.0)
    return [(ts, round(float(total[ts]), 5)) for ts in timeline]


def _aggregate_symbol_stats(
    results: dict[str, Any], fold_curve: list[tuple[pd.Timestamp, float]]
) -> dict[str, Any]:
    """Stats OOS d'un pli POOLÉ : agrégat honnête sur tous les actifs.

    Mêmes clés que les stats du moteur. Taux de gain et profit factor
    recalculés sur TOUS les trades du pli (jamais de moyenne de ratios par
    actif). Sharpe/SQN laissés à None : ce sont des ratios de séries
    temporelles par actif, non fusionnables honnêtement — la série commune
    n'a pas de fréquence régulière.
    """
    stats_list = [r.stats for r in results.values()]
    pnls = [trade.pnl for r in results.values() for trade in r.trades]
    wins = [p for p in pnls if p > 0]
    gross_win = sum(wins)
    gross_loss = -sum(p for p in pnls if p < 0)
    peak, max_dd = float("-inf"), 0.0
    for _, value in fold_curve:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return {
        "bars": sum(s.get("bars", 0) for s in stats_list),
        "trades": len(pnls),
        "total_pnl": round(sum(pnls), 5),
        "gross_pnl": round(sum(s.get("gross_pnl", 0.0) for s in stats_list), 5),
        "total_costs": round(sum(s.get("total_costs", 0.0) for s in stats_list), 5),
        # ``all`` et non ``any`` : le drapeau affirme « TOUS ces trades ont
        # payé leurs coûts ». Un seul actif pourvu de colonnes ask ne peut pas
        # en répondre pour les autres — sinon l'interface masque son
        # avertissement « résultats OPTIMISTES » alors que l'essentiel des
        # trades du pli s'est exécuté gratuitement.
        "costs_modelled": all(s.get("costs_modelled") for s in stats_list),
        "spread": None,  # différent par actif — voir per_symbol
        "commission_per_unit": stats_list[0].get("commission_per_unit", 0.0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "max_drawdown": round(max_dd, 5),
        "sharpe_ratio": None,  # non fusionnable honnêtement (voir docstring)
        "sqn": None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "avg_trade_pnl": round(sum(pnls) / len(pnls), 5) if pnls else None,
        "best_trade": round(max(pnls), 5) if pnls else None,
        "worst_trade": round(min(pnls), 5) if pnls else None,
    }


def _report(
    symbol: str,
    timeframe: str,
    folds: list[WalkForwardFold],
    oos_equity: list[dict[str, Any]],
    oos_trade_pnls: list[float],
    cancelled: bool,
    symbols: list[str] | None = None,
    dropped_symbols: list[str] | None = None,
    by_symbol_pnls: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    trades = sum(fold.test_stats.get("trades", 0) for fold in folds)
    total_pnl = round(sum(fold.test_stats.get("total_pnl", 0.0) for fold in folds), 5)
    # Coûts (spread + commission) payés sur l'ensemble des trades OOS. Affichés
    # à part : c'est l'écart entre « ça a l'air de marcher » et « ça marche ».
    total_costs = round(sum(fold.test_stats.get("total_costs", 0.0) for fold in folds), 5)
    # ``all`` (cf. _aggregate_symbol_stats) : le drapeau ne vaut que si CHAQUE
    # pli a modélisé ses coûts — un pli gratuit rendrait l'agrégat optimiste.
    costs_modelled = bool(folds) and all(
        fold.test_stats.get("costs_modelled") for fold in folds
    )
    equity_values = [point["equity"] for point in oos_equity]
    max_drawdown, peak = 0.0, float("-inf")
    for value in equity_values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    # Taux de gain ET profit factor agrégés sur TOUS les trades OOS : la seule
    # agrégation correcte. On ne moyenne JAMAIS les ratios par pli — une moyenne
    # non pondérée donnerait le même poids à un pli de 2 trades qu'à un pli de
    # 200, et un pli sans trade fausserait le compte. (``oos_trade_pnls`` couvre
    # tous les plis ; son cardinal vaut ``trades``, même dénominateur partout.)
    oos_wins = sum(1 for pnl in oos_trade_pnls if pnl > 0)
    win_rate = round(oos_wins / len(oos_trade_pnls), 4) if oos_trade_pnls else None
    gross_profit = sum(pnl for pnl in oos_trade_pnls if pnl > 0)
    gross_loss = -sum(pnl for pnl in oos_trade_pnls if pnl < 0)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "cancelled": cancelled,
        "folds": [vars(fold) for fold in folds],
        "oos_stats": {
            "trades": trades,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "max_drawdown": round(max_drawdown, 5),
            "profit_factor": profit_factor,
            "total_costs": total_costs,
            "costs_modelled": costs_modelled,
        },
        "oos_equity_curve": oos_equity[:2000],
    }
    if symbols is not None:
        report["symbols"] = symbols
    if dropped_symbols:
        # Actifs DEMANDÉS mais absents du run (couverture incompatible). Les
        # taire les faisait passer pour tradés-sans-signal ; les nommer permet
        # à l'interface de dire « non entraîné » plutôt que « 0 trade ».
        report["dropped_symbols"] = dropped_symbols
    if by_symbol_pnls is not None:
        # Ventilation par actif, agrégée avec la MÊME honnêteté que le global
        # (taux de gain et PF sur tous les trades de l'actif, jamais moyennés).
        report["oos_by_symbol"] = {
            sym: {
                "trades": len(pnls),
                "total_pnl": round(sum(pnls), 5),
                "win_rate": (
                    round(sum(1 for p in pnls if p > 0) / len(pnls), 4)
                    if pnls else None
                ),
                "profit_factor": (
                    round(
                        sum(p for p in pnls if p > 0) / -sum(p for p in pnls if p < 0),
                        4,
                    )
                    if any(p < 0 for p in pnls) else None
                ),
            }
            for sym, pnls in sorted(by_symbol_pnls.items())
        }
    return report
