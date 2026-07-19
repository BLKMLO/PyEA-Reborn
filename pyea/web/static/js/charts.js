/**
 * Dashboard live PyEA — logique du graphique et des panneaux.
 *
 * Règle du projet : tout graphique est créé ici (jamais inline dans les
 * templates) et se nourrit des endpoints JSON /api/*.
 *
 * - Graphique central : TradingView Lightweight Charts (chandeliers,
 *   pan/zoom natifs). L'historique se charge par pages en défilant vers
 *   le passé ; le refresh périodique passe par series.update() et ne
 *   touche donc pas à la position de défilement.
 * - Watchlist à droite : un clic = un onglet, le graphique bascule.
 * - Seul le graphique ACTIF est rafraîchi, toutes les N secondes
 *   (N = ui.chart_refresh_seconds de config.yaml, servi par /api/status).
 * - Panneau bas : positions ouvertes + fermées (grisées), P&L total.
 */

"use strict";

const state = {
  chart: null,          // instance LightweightCharts
  series: null,         // série chandeliers
  candles: [],          // bougies chargées (ordre chronologique)
  hasMore: true,        // reste-t-il de l'historique côté serveur ?
  loadingOlder: false,  // garde anti-requêtes concurrentes du lazy-load
  activeSymbol: null,
  refreshSeconds: 5,
  timer: null,
};

const UP_COLOR = "#34d399";
const DOWN_COLOR = "#f87171";

// --- Graphique (TradingView Lightweight Charts) ----------------------------
// Pan/zoom natifs ; on remonte le passé par pagination : quand l'utilisateur
// approche du bord gauche, on précharge les bougies antérieures (`before=`).

function createChart() {
  const container = document.getElementById("price-chart");
  if (state.chart) state.chart.remove();
  state.chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "transparent" }, textColor: "#94a3b8" },
    grid: {
      vertLines: { color: "#1e293b" },
      horzLines: { color: "#334155" },
    },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#334155" },
    rightPriceScale: { borderColor: "#334155" },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });
  state.series = state.chart.addCandlestickSeries({
    upColor: UP_COLOR, downColor: DOWN_COLOR,
    wickUpColor: UP_COLOR, wickDownColor: DOWN_COLOR,
    borderVisible: false,
  });
  // Lazy-load du passé : déclenché quand la fenêtre visible approche du
  // début des données chargées.
  state.chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && range.from < 15) loadOlderCandles();
  });
}

async function loadInitialCandles() {
  const response = await fetch(`/api/charts/price-history?symbol=${state.activeSymbol}&points=180`);
  if (!response.ok) return;
  const data = await response.json();
  if (data.symbol !== state.activeSymbol) return; // clic entre-temps
  state.candles = data.candles;
  state.hasMore = data.has_more;
  state.series.setData(state.candles);
  state.chart.timeScale().scrollToRealTime();
  setChartHeader();
}

async function loadOlderCandles() {
  if (state.loadingOlder || !state.hasMore || !state.candles.length) return;
  state.loadingOlder = true;
  try {
    const oldest = state.candles[0].time;
    const response = await fetch(
      `/api/charts/price-history?symbol=${state.activeSymbol}&points=180&before=${oldest}`);
    if (!response.ok) return;
    const data = await response.json();
    if (data.symbol !== state.activeSymbol || !data.candles.length) return;
    state.candles = data.candles.concat(state.candles);
    state.hasMore = data.has_more;
    // setData avec les données préfixées : Lightweight Charts conserve la
    // plage visible — le défilement de l'utilisateur n'est pas perturbé.
    state.series.setData(state.candles);
  } finally {
    state.loadingOlder = false;
  }
}

async function refreshChart() {
  // Rafraîchissement périodique : uniquement les dernières bougies, via
  // series.update() — la position de défilement est préservée.
  if (!state.activeSymbol || !state.series) return;
  const response = await fetch(`/api/charts/price-history?symbol=${state.activeSymbol}&points=10`);
  if (!response.ok) return;
  const data = await response.json();
  if (data.symbol !== state.activeSymbol) return;
  for (const candle of data.candles) {
    const last = state.candles[state.candles.length - 1];
    if (!last || candle.time > last.time) {
      state.candles.push(candle);
      state.series.update(candle);
    } else if (candle.time === last.time) {
      state.candles[state.candles.length - 1] = candle;
      state.series.update(candle);
    }
  }
  setChartHeader();
}

function setChartHeader() {
  document.getElementById("chart-title").textContent = `${state.activeSymbol} — M1`;
  document.getElementById("chart-updated").textContent =
    `maj ${new Date().toLocaleTimeString()} (toutes les ${state.refreshSeconds}s)`;
}

function scheduleRefresh() {
  if (state.timer) clearInterval(state.timer);
  // Seul le graphique actif est rafraîchi : un seul fetch par période.
  state.timer = setInterval(refreshChart, state.refreshSeconds * 1000);
}

// --- Watchlist -------------------------------------------------------------

function setActiveSymbol(symbol) {
  state.activeSymbol = symbol;
  state.candles = [];
  state.hasMore = true;
  document.querySelectorAll("#symbol-list li").forEach(li => {
    li.classList.toggle("bg-slate-700", li.dataset.symbol === symbol);
  });
  createChart();          // nouveau graphique vierge pour l'onglet
  loadInitialCandles();
}

async function loadSymbols() {
  const response = await fetch("/api/symbols");
  const data = await response.json();
  const list = document.getElementById("symbol-list");
  list.innerHTML = "";
  for (const item of data.symbols) {
    const li = document.createElement("li");
    li.dataset.symbol = item.symbol;
    li.className = "flex cursor-pointer items-center justify-between px-3 py-1.5 hover:bg-slate-700";
    li.innerHTML = `
      <span class="font-mono">${item.symbol}</span>
      <span class="h-2 w-2 rounded-full ${item.trading ? "bg-emerald-400" : "bg-slate-600"}"
            title="${item.trading ? "En trading" : "Inactif"}"></span>`;
    li.addEventListener("click", () => setActiveSymbol(item.symbol));
    list.appendChild(li);
  }
  if (!state.activeSymbol && data.symbols.length) {
    setActiveSymbol(data.symbols[0].symbol);
  }
}

// --- Positions & P&L -------------------------------------------------------

function pnlClass(value) {
  return value >= 0 ? "text-emerald-400" : "text-red-400";
}

function positionRow(p, closed) {
  const price = closed ? p.close_price : p.current_price;
  const rowClass = closed ? "text-slate-500" : "";
  const statut = closed
    ? `fermée ${new Date(p.closed_at).toLocaleDateString()}`
    : "ouverte";
  return `
    <tr class="${rowClass} border-t border-slate-700/60">
      <td class="py-1 pr-2 font-mono">${p.symbol}</td>
      <td class="pr-2">${p.side}</td>
      <td class="pr-2">${p.quantity}</td>
      <td class="pr-2">${p.entry_price}</td>
      <td class="pr-2">${price}</td>
      <td class="pr-2 ${closed ? "" : pnlClass(p.pnl)}">${p.pnl >= 0 ? "+" : ""}${p.pnl}</td>
      <td>${statut}</td>
    </tr>`;
}

async function refreshPositions() {
  const response = await fetch("/api/positions");
  if (!response.ok) return;
  const data = await response.json();
  const body = document.getElementById("positions-body");
  body.innerHTML =
    data.open.map(p => positionRow(p, false)).join("") +
    data.closed.map(p => positionRow(p, true)).join("");
  const total = document.getElementById("total-pnl");
  total.textContent = `${data.total_pnl >= 0 ? "+" : ""}${data.total_pnl}`;
  total.className = `mt-1 text-2xl font-semibold ${pnlClass(data.total_pnl)}`;
  document.getElementById("pnl-detail").textContent =
    `${data.open.length} ouverte(s) · ${data.closed.length} fermée(s)`;
}

// --- Onglets du panneau bas ------------------------------------------------

function initBottomTabs() {
  document.querySelectorAll(".bottom-tab").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".bottom-tab").forEach(b => {
        const active = b === button;
        b.classList.toggle("bg-slate-700", active);
        b.classList.toggle("text-slate-400", !active);
      });
      document.getElementById("tab-positions").classList.toggle("hidden", button.dataset.tab !== "positions");
      document.getElementById("tab-logs").classList.toggle("hidden", button.dataset.tab !== "logs");
    });
  });
}

async function refreshLogs() {
  const response = await fetch("/api/logs?count=100");
  if (!response.ok) return;
  const data = await response.json();
  document.getElementById("log-lines").textContent = data.lines.join("\n");
}

// --- Statut & WebSocket ----------------------------------------------------

async function loadStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  state.refreshSeconds = status.chart_refresh_seconds || 5;
  document.getElementById("header-status").textContent =
    `${status.trading_mode.toUpperCase()} · ${status.broker}` +
    ` · broker ${status.broker_connected ? "connecté" : "déconnecté"}` +
    ` · stratégie ${status.strategy} ${status.strategy_enabled ? "active" : "inactive"}`;
}

function initWebSocket() {
  const statusEl = document.getElementById("ws-status");
  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.onopen = () => { statusEl.textContent = "WS : connecté"; };
  ws.onclose = () => { statusEl.textContent = "WS : déconnecté"; };
  ws.onmessage = (event) => {
    // Plus tard : dispatch par topic (market.tick → dernière bougie,
    // strategy.signal → marqueurs, ea.status → header, log.line → logs).
    console.debug("WS", JSON.parse(event.data));
  };
}

// --- Init ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  initBottomTabs();
  initWebSocket();
  await loadStatus();
  await loadSymbols();      // déclenche le premier rendu du graphique
  await refreshPositions();
  await refreshLogs();
  scheduleRefresh();
  setInterval(refreshPositions, state.refreshSeconds * 1000);
  setInterval(refreshLogs, 15000);
});
