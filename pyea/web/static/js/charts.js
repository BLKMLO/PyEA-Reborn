/**
 * Dashboard live PyEA — logique du graphique et des panneaux.
 *
 * Règle du projet : tout graphique est créé ici (jamais inline dans les
 * templates) et se nourrit des endpoints JSON /api/*.
 *
 * - Watchlist à droite : un clic = un onglet, le graphique bascule.
 * - Seul le graphique ACTIF est rafraîchi, toutes les N secondes
 *   (N = ui.chart_refresh_seconds de config.yaml, servi par /api/status).
 * - Panneau bas : positions ouvertes + fermées (grisées), P&L total.
 */

"use strict";

const state = {
  chart: null,
  activeSymbol: null,
  refreshSeconds: 5,
  timer: null,
};

const UP_COLOR = "#34d399";
const DOWN_COLOR = "#f87171";

// --- Graphique -------------------------------------------------------------

function createChart(symbol, candles) {
  const ctx = document.getElementById("price-chart");
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "candlestick",
    data: {
      datasets: [{
        label: symbol,
        data: candles.map(c => ({ x: c.time, o: c.open, h: c.high, l: c.low, c: c.close })),
        color: { up: UP_COLOR, down: DOWN_COLOR, unchanged: "#94a3b8" },
        borderColor: { up: UP_COLOR, down: DOWN_COLOR, unchanged: "#94a3b8" },
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          type: "time",
          time: { unit: "minute" },
          ticks: { color: "#94a3b8", maxTicksLimit: 12 },
          grid: { color: "#1e293b" },
        },
        y: {
          position: "right",
          ticks: { color: "#94a3b8" },
          grid: { color: "#334155" },
        },
      },
    },
  });
}

async function refreshChart() {
  if (!state.activeSymbol) return;
  const response = await fetch(`/api/charts/price-history?symbol=${state.activeSymbol}&points=120`);
  if (!response.ok) return;
  const data = await response.json();
  if (data.symbol !== state.activeSymbol) return; // clic entre-temps
  createChart(data.symbol, data.candles);
  document.getElementById("chart-title").textContent = `${data.symbol} — M1`;
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
  document.querySelectorAll("#symbol-list li").forEach(li => {
    li.classList.toggle("bg-slate-700", li.dataset.symbol === symbol);
  });
  refreshChart();
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
