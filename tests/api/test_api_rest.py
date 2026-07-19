"""Tests fumée de l'API : l'app démarre et les endpoints clés répondent."""

from fastapi.testclient import TestClient

from pyea.app_factory import create_app


def _client() -> TestClient:
    return TestClient(create_app())


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
