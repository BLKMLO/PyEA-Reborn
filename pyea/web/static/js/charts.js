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
  hovering: false,      // crosshair sur une bougie (fige la légende dessus)
  activeSymbol: null,
  refreshSeconds: 5,
  timer: null,
  tradingMode: "paper",   // "live" déclenche une confirmation avant d'armer
};

const UP_COLOR = "#34d399";
const DOWN_COLOR = "#f87171";

// --- Formatage -------------------------------------------------------------
// Nombre de décimales selon l'ordre de grandeur : 5 pour le forex
// (0.8xxxx), 2 pour JPY / métaux / indices (>= 100). Évite d'afficher
// « 1823.40000 » ou « 0.86 » tronqué.
function formatPrice(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(2) : value.toFixed(5);
}

function formatChange(pct) {
  if (pct == null || Number.isNaN(pct)) return "";
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)} %`;
}

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
  // Légende OHLC (façon TradingView) : la bougie sous le crosshair, ou la
  // dernière bougie hors survol.
  state.chart.subscribeCrosshairMove(param => {
    state.hovering = Boolean(param.time);
    const candle = state.hovering
      ? param.seriesData.get(state.series)
      : state.candles[state.candles.length - 1];
    updateLegend(candle);
  });
}

// Légende OHLC + variation intra-bougie (close vs open), colorée.
function updateLegend(candle) {
  const legend = document.getElementById("chart-legend");
  if (!candle) { legend.classList.add("hidden"); return; }
  legend.classList.remove("hidden");
  const up = candle.close >= candle.open;
  const color = up ? UP_COLOR : DOWN_COLOR;
  const delta = candle.close - candle.open;
  const pct = candle.open ? (delta / candle.open) * 100 : 0;
  legend.innerHTML =
    `<span class="text-slate-300">${state.activeSymbol} · M1</span>  ` +
    `<span class="text-slate-500">O</span> ${formatPrice(candle.open)}  ` +
    `<span class="text-slate-500">H</span> ${formatPrice(candle.high)}  ` +
    `<span class="text-slate-500">L</span> ${formatPrice(candle.low)}  ` +
    `<span class="text-slate-500">C</span> ${formatPrice(candle.close)}  ` +
    `<span style="color:${color}">${delta >= 0 ? "+" : ""}${formatPrice(delta)} ` +
    `(${pct >= 0 ? "+" : ""}${pct.toFixed(2)} %)</span>`;
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
  document.getElementById("chart-loading").classList.add("hidden");
  updateLegend(state.candles[state.candles.length - 1]);
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
  // Hors survol, la légende suit la dernière bougie « vivante ».
  if (!state.hovering) updateLegend(state.candles[state.candles.length - 1]);
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
  state.hovering = false;
  document.querySelectorAll("#symbol-list li").forEach(li => {
    li.classList.toggle("bg-slate-700", li.dataset.symbol === symbol);
  });
  document.getElementById("chart-legend").classList.add("hidden");
  document.getElementById("chart-loading").classList.remove("hidden");
  createChart();          // nouveau graphique vierge pour l'onglet
  loadInitialCandles();
  refreshTradingButton(); // vérifie si le trading est déjà en cours sur la paire
}

// --- Bouton Trading/Stopped ------------------------------------------------

function renderTradingButton(enabled) {
  const button = document.getElementById("trading-toggle");
  button.classList.remove("hidden");
  button.dataset.enabled = String(enabled);
  button.textContent = enabled ? "Trading" : "Stopped";
  button.className = "rounded px-3 py-0.5 text-xs font-semibold " + (enabled
    ? "bg-emerald-600 text-white hover:bg-emerald-500"
    : "bg-red-600 text-white hover:bg-red-500");
}

async function refreshTradingButton() {
  // Interroge l'état serveur à CHAQUE changement d'onglet : l'état peut
  // avoir bougé depuis un autre navigateur/onglet.
  const symbol = state.activeSymbol;
  const response = await fetch(`/api/trading/${symbol}`);
  if (!response.ok || symbol !== state.activeSymbol) return;
  renderTradingButton((await response.json()).enabled);
}

async function toggleTrading() {
  const button = document.getElementById("trading-toggle");
  const target = button.dataset.enabled !== "true";
  if (target && state.tradingMode === "live" &&
      !window.confirm(`Armer le trading LIVE sur ${state.activeSymbol} ?`)) {
    return;
  }
  const response = await fetch(`/api/trading/${state.activeSymbol}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: target }),
  });
  if (!response.ok) return;
  renderTradingButton((await response.json()).enabled);
  loadSymbols(); // synchronise les pastilles de la watchlist
}

async function loadSymbols() {
  const response = await fetch("/api/symbols");
  if (!response.ok) return;
  const data = await response.json();
  renderWatchlist(data.symbols);
}

// Watchlist « Market Watch » : symbole + pastille de trading + dernier prix
// + variation 24 h colorée. La structure n'est bâtie qu'une fois ; les
// rafraîchissements périodiques ne mettent à jour QUE prix/variation/pastille
// (pas de innerHTML global → pas de flicker, l'onglet actif reste surligné).
function renderWatchlist(items) {
  const list = document.getElementById("symbol-list");
  if (list.children.length !== items.length) {
    list.innerHTML = "";
    for (const item of items) {
      const li = document.createElement("li");
      li.dataset.symbol = item.symbol;
      li.className = "flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5 hover:bg-slate-700";
      li.innerHTML = `
        <div class="flex min-w-0 items-center gap-2">
          <span class="h-2 w-2 shrink-0 rounded-full" data-dot></span>
          <span class="truncate font-mono">${item.symbol}</span>
        </div>
        <div class="text-right leading-tight">
          <div class="font-mono text-slate-200" data-last></div>
          <div class="text-[10px]" data-change></div>
        </div>`;
      li.addEventListener("click", () => setActiveSymbol(item.symbol));
      list.appendChild(li);
    }
  }
  for (const item of items) {
    const li = list.querySelector(`li[data-symbol="${item.symbol}"]`);
    if (!li) continue;
    li.classList.toggle("bg-slate-700", item.symbol === state.activeSymbol);
    const dot = li.querySelector("[data-dot]");
    dot.className = `h-2 w-2 shrink-0 rounded-full ${item.trading ? "bg-emerald-400" : "bg-slate-600"}`;
    dot.title = item.trading ? "En trading" : "Inactif";
    li.querySelector("[data-last]").textContent = formatPrice(item.last);
    const change = li.querySelector("[data-change]");
    change.textContent = formatChange(item.change_pct);
    change.className = `text-[10px] ${item.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`;
  }
  if (!state.activeSymbol && items.length) {
    setActiveSymbol(items[0].symbol);
  }
}

// --- Positions & P&L -------------------------------------------------------

function pnlClass(value) {
  return value >= 0 ? "text-emerald-400" : "text-red-400";
}

function sideBadge(side, dimmed) {
  // BUY vert / SELL rouge (convention des terminaux) ; grisé si fermée.
  const color = dimmed
    ? "text-slate-500"
    : side === "BUY" ? "text-emerald-400" : "text-red-400";
  return `<span class="font-semibold ${color}">${side}</span>`;
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
      <td class="pr-2">${sideBadge(p.side, closed)}</td>
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
  // Le serveur garantit ≥ 1 (validation config), ceinture côté client :
  // un intervalle 0 martèlerait l'API en boucle.
  state.refreshSeconds = Math.max(1, status.chart_refresh_seconds || 5);
  state.tradingMode = status.trading_mode;
  // Statut en badges colorés (façon barre d'état d'un terminal de trading) :
  // mode (LIVE en ambre = prudence), connexion broker (pastille), stratégie.
  const live = status.trading_mode === "live";
  const modePill = live ? "bg-amber-600 text-white" : "bg-sky-700 text-sky-100";
  const brokerDot = status.broker_connected ? "bg-emerald-400" : "bg-red-500";
  const strategyColor = status.strategy_enabled ? "text-emerald-400" : "text-slate-500";
  // Le badge broker est CLIQUABLE : il ouvre la fenêtre de saisie des
  // identifiants. Une clé (🔑) signale que des identifiants sont en mémoire.
  const credsMark = status.broker_credentials_set
    ? `<span title="Identifiants enregistrés">🔑</span>`
    : "";
  document.getElementById("header-status").innerHTML =
    `<span class="inline-flex items-center gap-2">` +
    `<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${modePill}">${status.trading_mode}</span>` +
    `<button id="broker-badge" type="button" title="Configurer les identifiants du broker"` +
    ` class="inline-flex items-center gap-1 rounded px-1 hover:bg-slate-700">` +
    `<span class="h-1.5 w-1.5 rounded-full ${brokerDot}"></span>${status.broker}${credsMark}</button>` +
    `<span class="text-slate-500">·</span>` +
    `<span class="${strategyColor}">${status.strategy}</span>` +
    `</span>`;
  document.getElementById("broker-badge").addEventListener("click", openBrokerModal);
}

// --- Identifiants broker (fenêtre modale) ----------------------------------

function setBrokerError(message) {
  const el = document.getElementById("broker-error");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

async function openBrokerModal() {
  setBrokerError("");
  const modal = document.getElementById("broker-modal");
  const usernameInput = document.getElementById("broker-username");
  const passwordInput = document.getElementById("broker-password");
  const hint = document.getElementById("broker-password-hint");
  const clearBtn = document.getElementById("broker-clear");
  let data = { configured: false, username: "", broker: "broker" };
  try {
    const response = await fetch("/api/broker/credentials");
    if (response.ok) data = await response.json();
  } catch (err) {
    // Réseau HS : on ouvre quand même avec des champs vides.
  }
  document.getElementById("broker-modal-name").textContent = data.broker;
  usernameInput.value = data.username || "";
  passwordInput.value = "";
  // Déjà enregistré : le mot de passe s'affiche en étoiles (placeholder),
  // laisser vide = on garde le mot de passe en mémoire.
  passwordInput.placeholder = data.configured ? "••••••••" : "";
  hint.classList.toggle("hidden", !data.configured);
  clearBtn.classList.toggle("hidden", !data.configured);
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  usernameInput.focus();
}

function closeBrokerModal() {
  const modal = document.getElementById("broker-modal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

async function submitBrokerCredentials(event) {
  event.preventDefault();
  setBrokerError("");
  const username = document.getElementById("broker-username").value.trim();
  const password = document.getElementById("broker-password").value;
  try {
    const response = await fetch("/api/broker/credentials", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      setBrokerError(err.detail || "Enregistrement impossible.");
      return;
    }
  } catch (err) {
    setBrokerError("Réseau indisponible.");
    return;
  }
  closeBrokerModal();
  await loadStatus(); // rafraîchit le badge (clé 🔑)
}

async function clearBrokerCredentials() {
  setBrokerError("");
  try {
    await fetch("/api/broker/credentials", { method: "DELETE" });
  } catch (err) {
    setBrokerError("Réseau indisponible.");
    return;
  }
  closeBrokerModal();
  await loadStatus();
}

function initWebSocket() {
  const statusEl = document.getElementById("ws-status");
  // wss derrière HTTPS (reverse proxy sur un VPS) — ws:// y serait bloqué.
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);
  ws.onopen = () => {
    statusEl.textContent = "● temps réel";
    statusEl.className = "text-xs text-emerald-400";
  };
  ws.onclose = () => {
    statusEl.textContent = "● hors ligne";
    statusEl.className = "text-xs text-red-400";
  };
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
  document.getElementById("trading-toggle").addEventListener("click", toggleTrading);
  // Fenêtre d'identifiants broker (boutons statiques câblés une fois).
  document.getElementById("broker-form").addEventListener("submit", submitBrokerCredentials);
  document.getElementById("broker-clear").addEventListener("click", clearBrokerCredentials);
  document.getElementById("broker-cancel").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal-close").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal").addEventListener("click", (event) => {
    if (event.target.id === "broker-modal") closeBrokerModal(); // clic sur le fond
  });
  await loadStatus();
  await loadSymbols();      // déclenche le premier rendu du graphique
  await refreshPositions();
  await refreshLogs();
  scheduleRefresh();
  setInterval(refreshPositions, state.refreshSeconds * 1000);
  setInterval(refreshLogs, 15000);
  // Prix de la watchlist rafraîchis à part (cadence lente : recalcul de
  // tous les symboles), en place — l'onglet actif n'est jamais perturbé.
  setInterval(loadSymbols, 10000);
});
