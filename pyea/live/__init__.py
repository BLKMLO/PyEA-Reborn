"""Orchestration du trading en temps réel (pendant live du backtest).

``LiveTradingEngine`` impose le flux strict
``Strategy → Signal → RiskManager → OrderRequest → BrokerGateway`` sur les
ticks reçus du ``MarketDataFeed`` via le bus d'événements. ``LiveRuntime``
en est le singleton d'application, démarré/arrêté avec la connexion broker.
"""
