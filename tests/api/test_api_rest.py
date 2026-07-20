"""Tests fumée de l'API : l'app démarre et les endpoints clés répondent."""

import pytest
from fastapi.testclient import TestClient

from pyea.app_factory import create_app
from pyea.brokers.broker_credentials import broker_credentials


def _client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_broker_credentials():
    """Le store d'identifiants est un singleton de module : on l'isole
    entre tests pour éviter toute fuite d'état."""
    broker_credentials.clear()
    yield
    broker_credentials.clear()


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


def test_trading_toggle_et_verification_au_changement_d_onglet() -> None:
    with _client() as client:
        try:
            # Armement de la paire (bouton → Trading).
            put = client.put("/api/trading/EURCHF", json={"enabled": True})
            assert put.status_code == 200 and put.json()["enabled"] is True
            # Vérification faite à chaque changement d'onglet.
            get = client.get("/api/trading/EURCHF")
            assert get.json() == {"symbol": "EURCHF", "enabled": True}
            # La pastille de la watchlist suit le même état.
            symbols = client.get("/api/symbols").json()["symbols"]
            assert next(s for s in symbols if s["symbol"] == "EURCHF")["trading"] is True
        finally:
            client.put("/api/trading/EURCHF", json={"enabled": False})


def test_trading_symbole_inconnu_404() -> None:
    with _client() as client:
        assert client.get("/api/trading/NIMPORTE").status_code == 404
        assert client.put("/api/trading/NIMPORTE", json={"enabled": True}).status_code == 404


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


def test_broker_credentials_non_configure_par_defaut() -> None:
    with _client() as client:
        response = client.get("/api/broker/credentials")
        status = client.get("/api/status").json()
    data = response.json()
    assert response.status_code == 200
    assert data["configured"] is False
    assert data["username"] == ""
    assert status["broker_credentials_set"] is False


def test_broker_credentials_enregistrement_et_masquage() -> None:
    with _client() as client:
        put = client.put(
            "/api/broker/credentials",
            json={"username": "marianne", "password": "secret"},
        )
        assert put.status_code == 200
        assert put.json()["configured"] is True
        # Le mot de passe ne fuit JAMAIS via l'API.
        get = client.get("/api/broker/credentials").json()
        assert get["username"] == "marianne"
        assert "password" not in get
        assert "secret" not in str(get)
        assert client.get("/api/status").json()["broker_credentials_set"] is True


def test_broker_credentials_mdp_vide_conserve_l_existant() -> None:
    with _client() as client:
        client.put(
            "/api/broker/credentials",
            json={"username": "marianne", "password": "secret"},
        )
        # Re-PUT sans mot de passe : identifiant changé, mdp conservé.
        put = client.put("/api/broker/credentials", json={"username": "marianne2"})
        assert put.status_code == 200
    assert broker_credentials.username == "marianne2"
    assert broker_credentials.password == "secret"


def test_broker_credentials_mdp_requis_si_rien_enregistre() -> None:
    with _client() as client:
        put = client.put("/api/broker/credentials", json={"username": "marianne"})
    assert put.status_code == 422


def test_broker_credentials_username_requis() -> None:
    with _client() as client:
        put = client.put(
            "/api/broker/credentials", json={"username": "  ", "password": "x"}
        )
    assert put.status_code == 422


def test_broker_credentials_suppression() -> None:
    with _client() as client:
        client.put(
            "/api/broker/credentials",
            json={"username": "marianne", "password": "secret"},
        )
        delete = client.delete("/api/broker/credentials")
        assert delete.status_code == 200
        assert delete.json()["configured"] is False
        assert client.get("/api/broker/credentials").json()["configured"] is False


def test_positions_structure_et_pnl_total() -> None:
    with _client() as client:
        response = client.get("/api/positions")
    data = response.json()
    assert response.status_code == 200
    assert {"open", "closed", "total_pnl"} <= set(data)
    if data["closed"]:
        # Plus récentes en premier.
        closed_dates = [p["closed_at"] for p in data["closed"]]
        assert closed_dates == sorted(closed_dates, reverse=True)
