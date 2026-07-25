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
  brokerConnected: false, // aucune action de trading possible si déconnecté
  brokers: [],            // brokers disponibles (liste déroulante de la modale)
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

// Date locale à partir d'un horodatage serveur. Le serveur envoie désormais
// un fuseau explicite ; on tolère une valeur sans fuseau (base écrite par une
// version antérieure) en la lisant comme de l'UTC — jamais comme du local.
function formatUtcDate(iso) {
  if (!iso) return "";
  const stamped = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(stamped).toLocaleDateString();
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
  // Broker déconnecté : on ne peut PAS armer (pas de faux trades). Le bouton
  // reste visible mais grisé et désactivé — sauf pour désarmer une paire déjà
  // armée, toujours autorisé (sécurité).
  const canArm = state.brokerConnected || enabled;
  button.disabled = !canArm;
  button.textContent = enabled ? "Trading" : "Stopped";
  const base = "rounded px-3 py-0.5 text-xs font-semibold ";
  if (!canArm) {
    button.className = base + "bg-slate-600 text-slate-400 cursor-not-allowed";
    button.title = "Broker déconnecté : connectez-vous pour armer une paire.";
  } else {
    button.className = base + (enabled
      ? "bg-emerald-600 text-white hover:bg-emerald-500"
      : "bg-red-600 text-white hover:bg-red-500");
    button.title = "";
  }
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
  if (target && !state.brokerConnected) {
    showToast("Broker déconnecté : connectez-vous avant d'armer une paire.", "error");
    return;
  }
  if (target && state.tradingMode === "live" &&
      !window.confirm(`Armer le trading LIVE sur ${state.activeSymbol} ?`)) {
    return;
  }
  const response = await fetch(`/api/trading/${state.activeSymbol}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: target }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    showToast(err.detail || "Action de trading refusée.", "error");
    return;
  }
  const enabled = (await response.json()).enabled;
  renderTradingButton(enabled);
  showToast(
    `${state.activeSymbol} : trading ${enabled ? "armé" : "arrêté"}.`,
    enabled ? "success" : "info");
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

function openPositionRow(p) {
  const pnl = p.pnl == null ? "—" : `${p.pnl >= 0 ? "+" : ""}${p.pnl}`;
  return `
    <tr class="border-t border-slate-700/60">
      <td class="py-1 pr-2 font-mono">${p.symbol}</td>
      <td class="pr-2">${sideBadge(p.side, false)}</td>
      <td class="pr-2">${p.quantity}</td>
      <td class="pr-2">${formatPrice(p.entry_price)}</td>
      <td class="pr-2">${p.current_price == null ? "—" : formatPrice(p.current_price)}</td>
      <td class="pr-2 ${p.pnl == null ? "" : pnlClass(p.pnl)}">${pnl}</td>
      <td>ouverte</td>
    </tr>`;
}

// Trade RÉELLEMENT exécuté (journal SQL), grisé comme historique. Le P&L
// n'est présent que sur les sorties de position (calculé par le broker).
function tradeRow(t) {
  const pnl = t.pnl == null
    ? `<span class="text-slate-600">—</span>`
    : `<span class="${pnlClass(t.pnl)}">${t.pnl >= 0 ? "+" : ""}${t.pnl}</span>`;
  return `
    <tr class="border-t border-slate-700/60 text-slate-500">
      <td class="py-1 pr-2 font-mono">${t.symbol}</td>
      <td class="pr-2">${sideBadge(t.side, true)}</td>
      <td class="pr-2">${t.quantity}</td>
      <td class="pr-2">${t.fill_price == null ? "—" : formatPrice(t.fill_price)}</td>
      <td class="pr-2">—</td>
      <td class="pr-2">${pnl}</td>
      <td>${t.status.toLowerCase()} ${formatUtcDate(t.executed_at)}</td>
    </tr>`;
}

// État du compte chez le broker (équité, solde, marge) + perte du jour face à
// la limite configurée. Déconnecté → tirets : on n'invente aucun chiffre.
const ACCOUNT_ROWS = [
  ["equity", "Équité"],
  ["balance", "Solde"],
  ["margin", "Marge"],
  ["margin_free", "Marge libre"],
];

function renderAccount(data) {
  const box = document.getElementById("account-summary");
  if (!box) return;
  const summary = (data && data.summary) || {};
  const connected = Boolean(data && data.connected);
  box.replaceChildren();
  for (const [key, label] of ACCOUNT_ROWS) {
    const row = document.createElement("div");
    row.className = "flex justify-between gap-2";
    const dt = document.createElement("dt");
    dt.className = "text-slate-500";
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.className = "font-mono text-slate-300";
    const value = summary[key];
    dd.textContent = connected && value != null ? Number(value).toFixed(2) : "—";
    row.append(dt, dd);
    box.append(row);
  }
  // Perte du jour : ambre dès la moitié du plafond, rouge une fois atteint
  // (au-delà, le RiskManager refuse toute nouvelle entrée).
  if (connected && data.day_loss_pct != null && data.max_daily_loss_pct > 0) {
    const loss = data.day_loss_pct;
    const max = data.max_daily_loss_pct;
    const row = document.createElement("div");
    row.className = "mt-1 flex justify-between gap-2 border-t border-slate-700/60 pt-1";
    const dt = document.createElement("dt");
    dt.className = "text-slate-500";
    dt.textContent = "Perte du jour";
    const dd = document.createElement("dd");
    const tone = loss >= max ? "text-red-400" : loss >= max / 2 ? "text-amber-400" : "text-slate-300";
    dd.className = `font-mono ${tone}`;
    dd.textContent = `${loss.toFixed(2)} / ${max} %`;
    dd.title = loss >= max
      ? "Limite atteinte : plus aucune entrée aujourd'hui (les sorties restent autorisées)."
      : "Perte du jour rapportée à l'équité de début de journée UTC.";
    row.append(dt, dd);
    box.append(row);
  }
}

async function refreshAccount() {
  try {
    const response = await fetch("/api/account");
    renderAccount(response.ok ? await response.json() : null);
  } catch (err) {
    renderAccount(null);
  }
}

async function refreshPositions() {
  const response = await fetch("/api/positions");
  if (!response.ok) return;
  const data = await response.json();
  const body = document.getElementById("positions-body");
  const empty = document.getElementById("positions-empty");
  const rows = data.open.map(openPositionRow).join("") + data.trades.map(tradeRow).join("");
  body.innerHTML = rows;
  // État vide HONNÊTE : rien n'est inventé quand le broker est déconnecté.
  const isEmpty = !data.open.length && !data.trades.length;
  empty.classList.toggle("hidden", !isEmpty);
  if (isEmpty) {
    empty.textContent = data.broker_connected
      ? "Aucune position ouverte ni trade exécuté."
      : "Broker déconnecté — aucune position réelle à afficher.";
  }
  const total = document.getElementById("total-pnl");
  total.textContent = `${data.total_pnl >= 0 ? "+" : ""}${data.total_pnl}`;
  total.className = `mt-1 text-2xl font-semibold ${pnlClass(data.total_pnl)}`;
  // Détail des deux composantes réelles du P&L : latent (positions ouvertes
  // chez le broker) et réalisé (trades journalisés, P&L calculé par le broker).
  document.getElementById("pnl-detail").innerHTML =
    `${data.open.length} ouverte(s) · ${data.trades.length} trade(s) exécuté(s)<br>` +
    `latent <span class="${pnlClass(data.open_pnl)}">${data.open_pnl}</span> · ` +
    `réalisé <span class="${pnlClass(data.realized_pnl)}">${data.realized_pnl}</span>`;
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
  const wasConnected = state.brokerConnected;
  state.brokerConnected = status.broker_connected;
  // Statut en badges colorés (façon barre d'état d'un terminal de trading) :
  // mode (LIVE en ambre = prudence), connexion broker (pastille), stratégie.
  const live = status.trading_mode === "live";
  const modePill = live ? "bg-amber-600 text-white" : "bg-sky-700 text-sky-100";
  const brokerDot = status.broker_connected ? "bg-emerald-400" : "bg-red-500";
  const strategyColor = status.strategy_enabled ? "text-emerald-400" : "text-slate-500";
  // Badge DÉMO franc quand les données de marché ne sont pas réelles : le
  // graphique et les prix de la watchlist sont simulés — pas de tromperie.
  const demoBadge = status.market_data_live
    ? ""
    : `<span class="rounded bg-purple-700 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-purple-100"` +
      ` title="Données de marché simulées (aucun flux broker connecté)">démo</span>`;
  // Le badge broker est CLIQUABLE : il ouvre la fenêtre de connexion broker.
  document.getElementById("header-status").innerHTML =
    `<span class="inline-flex items-center gap-2">` +
    `<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${modePill}">${status.trading_mode}</span>` +
    `<button id="broker-badge" type="button" title="Connexion au broker"` +
    ` class="inline-flex items-center gap-1 rounded px-1 hover:bg-slate-700">` +
    `<span class="h-1.5 w-1.5 rounded-full ${brokerDot}"></span>${status.broker}` +
    `<span class="text-[10px] ${status.broker_connected ? "text-emerald-400" : "text-red-400"}">` +
    `${status.broker_connected ? "connecté" : "déconnecté"}</span></button>` +
    `<span class="text-slate-500">·</span>` +
    `<span class="${strategyColor}">${status.strategy}</span>` +
    demoBadge +
    `</span>`;
  document.getElementById("broker-badge").addEventListener("click", openBrokerModal);
  // Un changement d'état de connexion réactualise le bouton trade actif.
  if (wasConnected !== state.brokerConnected && state.activeSymbol) refreshTradingButton();
}

// --- Connexion broker (fenêtre modale) -------------------------------------
// Aucun broker ne prend de login/mot de passe dans PyEA : IB s'authentifie
// via TWS/IB Gateway, MetaTrader 5 via un terminal MT5 déjà ouvert. La liste
// déroulante choisit le broker ; les paramètres (lecture seule) et la note
// explicative dépendent du broker sélectionné. On affiche l'état réel.

function setBrokerError(message) {
  const el = document.getElementById("broker-error");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

function selectedBroker() {
  const name = document.getElementById("broker-select").value;
  return state.brokers.find((b) => b.name === name) || null;
}

// Reconstruit les paramètres + la ligne d'état du broker sélectionné, et
// ajuste les boutons Se connecter / Se déconnecter selon son état réel.
function renderBrokerDetails() {
  const broker = selectedBroker();
  const params = document.getElementById("broker-params");
  params.replaceChildren();
  const rows = broker ? Object.entries(broker.params) : [];
  for (const [key, value] of rows) {
    const row = document.createElement("div");
    row.className = "flex justify-between gap-4";
    const dt = document.createElement("dt");
    dt.className = "text-slate-400";
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.className = "font-mono text-right";
    dd.textContent = value;
    row.append(dt, dd);
    params.append(row);
  }
  const connected = !!(broker && broker.connected);
  const stateRow = document.createElement("div");
  stateRow.className = "flex justify-between gap-4";
  const stateDt = document.createElement("dt");
  stateDt.className = "text-slate-400";
  stateDt.textContent = "État";
  const stateDd = document.createElement("dd");
  stateDd.className = `font-semibold ${connected ? "text-emerald-400" : "text-red-400"}`;
  stateDd.textContent = connected ? "connecté" : "déconnecté";
  stateRow.append(stateDt, stateDd);
  params.append(stateRow);

  document.getElementById("broker-hint").textContent = broker ? broker.hint : "";
  document.getElementById("broker-connect").classList.toggle("hidden", connected);
  document.getElementById("broker-disconnect").classList.toggle("hidden", !connected);
  // On ne change pas de broker tant qu'une connexion est vivante.
  document.getElementById("broker-select").disabled = connected;
}

async function openBrokerModal() {
  setBrokerError("");
  const modal = document.getElementById("broker-modal");
  try {
    const response = await fetch("/api/brokers");
    const data = response.ok ? await response.json() : { brokers: [], active: null };
    state.brokers = data.brokers || [];
    const select = document.getElementById("broker-select");
    select.replaceChildren();
    for (const broker of state.brokers) {
      const option = document.createElement("option");
      option.value = broker.name;
      option.textContent = broker.label;
      select.append(option);
    }
    if (data.active) select.value = data.active;
  } catch (err) {
    state.brokers = [];
  }
  renderBrokerDetails();
  modal.classList.remove("hidden");
  modal.classList.add("flex");
}

function closeBrokerModal() {
  const modal = document.getElementById("broker-modal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

// Met à jour l'état connecté d'un broker en mémoire (les autres = déconnectés,
// un seul broker actif à la fois).
function markBrokerConnected(name, connected) {
  for (const broker of state.brokers) {
    broker.connected = connected && broker.name === name;
  }
}

async function connectBroker() {
  setBrokerError("");
  const broker = selectedBroker();
  if (!broker) return;
  showToast("Connexion au broker…", "info");
  let response;
  try {
    response = await fetch("/api/broker/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ broker: broker.name }),
    });
  } catch (err) {
    showToast("Réseau indisponible.", "error");
    return;
  }
  if (response.ok) {
    showToast(`${broker.label} connecté.`, "success");
    markBrokerConnected(broker.name, true);
    renderBrokerDetails();
  } else {
    // Retour HONNÊTE du serveur (501 = pas encore câblé, 503 = dépendance
    // absente, 502 = terminal/TWS injoignable) — jamais de fausse connexion.
    const err = await response.json().catch(() => ({}));
    setBrokerError(err.detail || "Connexion au broker impossible.");
    showToast(err.detail || "Connexion au broker impossible.", "error");
  }
  await loadStatus();
}

async function disconnectBroker() {
  const broker = selectedBroker();
  try {
    await fetch("/api/broker/disconnect", { method: "POST" });
  } catch (err) {
    showToast("Réseau indisponible.", "error");
    return;
  }
  showToast("Broker déconnecté.", "info");
  if (broker) markBrokerConnected(broker.name, false);
  renderBrokerDetails();
  await loadStatus();
}

function initWebSocket() {
  // Reconnexion automatique gérée par le helper partagé (websocket.js) : un
  // redémarrage du serveur ne doit pas laisser la page muette jusqu'au F5.
  openLiveSocket((data) => {
    // Plus tard : dispatch par topic (market.tick → dernière bougie,
    // strategy.signal → marqueurs, ea.status → header).
    console.debug("WS", data);
  });
}

// --- Init ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  initBottomTabs();
  initWebSocket();
  document.getElementById("trading-toggle").addEventListener("click", toggleTrading);
  // Fenêtre de connexion broker (boutons statiques câblés une fois).
  document.getElementById("broker-select").addEventListener("change", renderBrokerDetails);
  document.getElementById("broker-connect").addEventListener("click", connectBroker);
  document.getElementById("broker-disconnect").addEventListener("click", disconnectBroker);
  document.getElementById("broker-cancel").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal-close").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal").addEventListener("click", (event) => {
    if (event.target.id === "broker-modal") closeBrokerModal(); // clic sur le fond
  });
  await loadStatus();
  await loadSymbols();      // déclenche le premier rendu du graphique
  await refreshPositions();
  await refreshAccount();
  await refreshLogs();
  scheduleRefresh();
  setInterval(refreshPositions, state.refreshSeconds * 1000);
  setInterval(refreshAccount, state.refreshSeconds * 1000);
  setInterval(refreshLogs, 15000);
  // Prix de la watchlist rafraîchis à part (cadence lente : recalcul de
  // tous les symboles), en place — l'onglet actif n'est jamais perturbé.
  setInterval(loadSymbols, 10000);
});
