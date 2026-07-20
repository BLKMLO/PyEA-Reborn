"""Tests fumée de l'API : l'app démarre et les endpoints clés répondent."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyea.app_factory import create_app
from pyea.config.config_settings import get_settings


def _client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path):
    """Base SQLite temporaire par test → journal des trades vierge, pas de
    fuite d'état d'un test à l'autre."""
    settings = get_settings()
    original_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    yield
    settings.database_url = original_url


def test_dashboard_repond() -> None:
    with _client() as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "PyEA" in response.text


def test_page_backtest_repond() -> None:
    with _client() as client:
        response = client.get("/backtest")
    assert response.status_code == 200
    assert "Backtest" in response.text


def test_page_training_repond() -> None:
    with _client() as client:
        response = client.get("/training")
    assert response.status_code == 200
    assert "Entraînement" in response.text
    assert "/static/js/training.js" in response.text


def test_status_repond() -> None:
    with _client() as client:
        response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["strategy"] == "couleuvre_v0_1"
    assert data["chart_refresh_seconds"] >= 1


def test_symbols_watchlist() -> None:
    with _client() as client:
        response = client.get("/api/symbols")
    data = response.json()
    assert response.status_code == 200
    symbols = {item["symbol"] for item in data["symbols"]}
    assert {"EURUSD", "XAUUSD", "US500"} <= symbols
    # Façon « Market Watch » : chaque ligne porte un prix + variation.
    eurusd = next(item for item in data["symbols"] if item["symbol"] == "EURUSD")
    assert {"last", "change_pct", "trading"} <= set(eurusd)
    assert isinstance(eurusd["last"], (int, float)) and eurusd["last"] > 0
    assert isinstance(eurusd["change_pct"], (int, float))


def test_symbols_prix_coherent_avec_le_graphique() -> None:
    # Le prix de la watchlist est un vrai close de la série du graphique
    # (même marche aléatoire déterministe) — pas un nombre indépendant.
    # On tolère un basculement de minute entre les deux requêtes : le prix
    # doit égaler le close de l'une des deux dernières bougies.
    with _client() as client:
        last = next(
            item["last"]
            for item in client.get("/api/symbols").json()["symbols"]
            if item["symbol"] == "EURUSD"
        )
        candles = client.get(
            "/api/charts/price-history?symbol=EURUSD&points=10"
        ).json()["candles"]
    assert last in {candles[-1]["close"], candles[-2]["close"]}


def test_armer_sans_broker_refuse() -> None:
    # Honnêteté : sans broker connecté, armer une paire est REFUSÉ (409) —
    # pas de faux trades. Le broker n'est jamais connecté dans les tests.
    with _client() as client:
        put = client.put("/api/trading/EURCHF", json={"enabled": True})
        assert put.status_code == 409
        assert "déconnecté" in put.json()["detail"].lower()
        # L'état reste « arrêté » (rien n'a été armé en douce).
        assert client.get("/api/trading/EURCHF").json()["enabled"] is False


def test_desarmer_toujours_autorise() -> None:
    # Arrêter une paire doit toujours marcher (sécurité), broker ou pas.
    with _client() as client:
        put = client.put("/api/trading/EURCHF", json={"enabled": False})
        assert put.status_code == 200 and put.json()["enabled"] is False


def test_trading_symbole_inconnu_404() -> None:
    with _client() as client:
        assert client.get("/api/trading/NIMPORTE").status_code == 404
        assert client.put("/api/trading/NIMPORTE", json={"enabled": False}).status_code == 404


def test_price_history_bougies_ohlc() -> None:
    with _client() as client:
        response = client.get("/api/charts/price-history?symbol=EURUSD&points=30")
    data = response.json()
    assert response.status_code == 200
    assert data["symbol"] == "EURUSD"
    assert data["has_more"] is True
    assert len(data["candles"]) == 30
    candle = data["candles"][0]
    assert candle["low"] <= min(candle["open"], candle["close"])
    assert candle["high"] >= max(candle["open"], candle["close"])


def test_price_history_pagination_vers_le_passe() -> None:
    with _client() as client:
        page1 = client.get("/api/charts/price-history?symbol=EURUSD&points=30").json()
        oldest = page1["candles"][0]["time"]
        page2 = client.get(
            f"/api/charts/price-history?symbol=EURUSD&points=30&before={oldest}"
        ).json()
    # Strictement antérieures, contiguës (M1 = 60 s) et déterministes :
    # le close de la page ancienne = l'open de la page récente.
    assert all(candle["time"] < oldest for candle in page2["candles"])
    assert page2["candles"][-1]["time"] == oldest - 60
    assert page2["candles"][-1]["close"] == page1["candles"][0]["open"]


def test_price_history_fin_d_historique() -> None:
    with _client() as client:
        # Bien avant l'origine de la démo (3 jours) : plus aucune bougie.
        response = client.get(
            "/api/charts/price-history?symbol=EURUSD&points=30&before=60"
        ).json()
    assert response["candles"] == []
    assert response["has_more"] is False


def test_price_history_symbole_inconnu_404() -> None:
    with _client() as client:
        response = client.get("/api/charts/price-history?symbol=NIMPORTE")
    assert response.status_code == 404


def test_broker_info_lecture_seule() -> None:
    # L'API IB ne prend pas de login/mdp : la fenêtre broker n'expose que les
    # paramètres de socket (host/port/client_id) + l'état de connexion.
    with _client() as client:
        info = client.get("/api/broker").json()
        status = client.get("/api/status").json()
    assert info["broker"] == "interactive_brokers"
    assert info["trading_mode"] in ("paper", "live")
    assert info["port"] > 0
    assert info["connected"] is False
    # Plus aucune notion d'identifiants : l'endpoint credentials a disparu.
    assert client.get("/api/broker/credentials").status_code == 404
    assert "broker_credentials_set" not in status


def test_positions_vides_sans_broker() -> None:
    # Broker déconnecté (cas des tests) : AUCUNE position ni trade simulé,
    # P&L à zéro. L'interface ne ment pas.
    with _client() as client:
        data = client.get("/api/positions").json()
    assert data["broker_connected"] is False
    assert data["open"] == []
    assert data["trades"] == []
    assert data["total_pnl"] == 0


def test_status_broker_deconnecte_et_marche_demo() -> None:
    with _client() as client:
        status = client.get("/api/status").json()
    # État broker RÉEL (gateway non connectée), pas un booléen codé en dur.
    assert status["broker_connected"] is False
    # Données de marché signalées comme démo → l'UI affiche le badge « DÉMO ».
    assert status["market_data_live"] is False


def test_connexion_broker_retour_honnete() -> None:
    # La connexion IB n'est pas implémentée : réponse 501 explicite, JAMAIS
    # une fausse connexion réussie.
    with _client() as client:
        response = client.post("/api/broker/connect")
        assert response.status_code == 501
        assert client.get("/api/status").json()["broker_connected"] is False


def test_trades_affiches_viennent_du_journal_sql() -> None:
    # L'affichage lit le journal SQL (réel), pas une invention en mémoire.
    from pyea.storage.storage_database import init_db
    from pyea.storage.storage_trades import record_trade

    with _client() as client:
        init_db()
        record_trade("ORD-1", "EURUSD", "BUY", 1.0, 1.0855, status="FILLED")
        data = client.get("/api/positions").json()
    assert any(t["broker_order_id"] == "ORD-1" and t["symbol"] == "EURUSD"
               for t in data["trades"])
