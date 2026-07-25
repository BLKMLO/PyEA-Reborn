/**
 * Dashboard live PyEA — logique du graphique et des panneaux.
 *
 * Règle du projet : tout graphique est créé ici (jamais inline dans les
 * templates) et se nourrit des endpoints JSON /api/*. Les helpers communs
 * aux trois pages (formats, préférences, tables triables, bandeau d'état)
 * viennent de `ui.js` (`window.PyEA`).
 *
 * - Graphique central : TradingView Lightweight Charts (chandeliers,
 *   pan/zoom natifs). L'historique se charge par pages en défilant vers
 *   le passé ; le refresh périodique passe par series.update() et ne
 *   touche donc pas à la position de défilement. Les trades RÉELS du
 *   symbole affiché (journal SQL) sont posés en marqueurs.
 * - Watchlist à droite : un clic = un onglet, recherche + tri + filtre
 *   « armées seulement » (31 instruments : la liste brute était pénible).
 * - Seul le graphique ACTIF est rafraîchi, toutes les N secondes
 *   (N = ui.chart_refresh_seconds de config.yaml, servi par /api/status).
 * - Panneau bas redimensionnable : positions (triables, exportables) +
 *   logs colorés par niveau, P&L total et compte à droite.
 */

"use strict";

const {
  prefs, formatPrice, pnlClass, formatUtcDate, formatUtcDateTime,
  loadHeaderStatus, makeSortable, exportTableCsv, registerOverlay,
} = window.PyEA;

const state = {
  chart: null,          // instance LightweightCharts
  series: null,         // série chandeliers
  candles: [],          // bougies chargées (ordre chronologique)
  hasMore: true,        // reste-t-il de l'historique côté serveur ?
  loadingOlder: false,  // garde anti-requêtes concurrentes du lazy-load
  hovering: false,      // crosshair sur une bougie (fige la légende dessus)
  activeSymbol: null,
  symbols: [],            // dernière charge utile de /api/symbols
  trades: [],             // trades exécutés (journal SQL) — marqueurs du graphique
  refreshSeconds: 5,
  timer: null,
  tradingMode: "paper",   // "live" déclenche une confirmation avant d'armer
  brokerConnected: false, // aucune action de trading possible si déconnecté
  brokers: [],            // brokers disponibles (liste déroulante de la modale)
  logLines: [],           // dernières lignes servies par /api/logs
};

const UP_COLOR = "#34d399";
const DOWN_COLOR = "#f87171";

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

/**
 * Marqueurs des trades RÉELLEMENT exécutés sur la paire affichée.
 *
 * Source = journal SQL (`/api/positions`), jamais une simulation : tant
 * qu'aucun broker n'a exécuté, il n'y a aucun marqueur. Chaque ligne du
 * journal est un fill (entrée OU sortie) : on pose une flèche à son
 * horodatage, verte à l'achat, rouge à la vente.
 */
function applyTradeMarkers() {
  if (!state.series || !state.activeSymbol) return;
  const markers = state.trades
    .filter(trade => trade.symbol === state.activeSymbol && trade.executed_at)
    .map(trade => {
      const moment = new Date(
        /[Zz]|[+-]\d{2}:?\d{2}$/.test(trade.executed_at)
          ? trade.executed_at
          : `${trade.executed_at}Z`);
      const buy = trade.side === "BUY";
      return {
        time: Math.floor(moment.getTime() / 1000),
        position: buy ? "belowBar" : "aboveBar",
        color: buy ? UP_COLOR : DOWN_COLOR,
        shape: buy ? "arrowUp" : "arrowDown",
        text: `${trade.side} ${trade.quantity}` +
          (trade.fill_price == null ? "" : ` @ ${formatPrice(trade.fill_price)}`),
      };
    })
    .filter(marker => Number.isFinite(marker.time))
    .sort((a, b) => a.time - b.time); // Lightweight Charts exige l'ordre croissant
  state.series.setMarkers(markers);
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
  applyTradeMarkers();
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
    applyTradeMarkers(); // setData efface les marqueurs : on les repose
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
  // Variation 24 h de la paire affichée (même source que la watchlist).
  const quote = state.symbols.find(item => item.symbol === state.activeSymbol);
  const change = document.getElementById("chart-change");
  if (quote && quote.change_pct != null) {
    change.textContent = `${formatPrice(quote.last)}  ${formatChange(quote.change_pct)}`;
    change.className = `font-mono text-xs ${pnlClass(quote.change_pct)}`;
  } else {
    change.textContent = "";
  }
}

function scheduleRefresh() {
  if (state.timer) clearInterval(state.timer);
  // Seul le graphique actif est rafraîchi : un seul fetch par période.
  state.timer = setInterval(refreshChart, state.refreshSeconds * 1000);
}

// --- Watchlist -------------------------------------------------------------

function setActiveSymbol(symbol) {
  state.activeSymbol = symbol;
  prefs.set("live:symbol", symbol); // retrouvé au prochain chargement de page
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
  renderPositions();      // le filtre « symbole affiché » suit l'onglet
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
    button.title = enabled
      ? "Cliquer pour arrêter le trading sur cette paire."
      : "Cliquer pour armer le trading sur cette paire.";
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
  state.symbols = (await response.json()).symbols;
  renderWatchlist();
  setChartHeader();
}

// Filtre (recherche + « armées seulement ») et tri appliqués côté client :
// /api/symbols sert la liste complète, la mise en forme reste une affaire
// d'interface.
function visibleSymbols() {
  const query = (document.getElementById("symbol-search").value || "").trim().toUpperCase();
  const armedOnly = document.getElementById("symbol-armed-only").checked;
  const sort = document.getElementById("symbol-sort").value;
  let items = state.symbols.filter(item =>
    (!query || item.symbol.includes(query)) && (!armedOnly || item.trading));
  const byChange = (a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0);
  if (sort === "change_desc") items = [...items].sort(byChange);
  else if (sort === "change_asc") items = [...items].sort((a, b) => byChange(b, a));
  else if (sort === "trading") {
    items = [...items].sort((a, b) =>
      Number(b.trading) - Number(a.trading) || a.symbol.localeCompare(b.symbol));
  } else items = [...items].sort((a, b) => a.symbol.localeCompare(b.symbol));
  return items;
}

// Watchlist « Market Watch » : symbole + pastille de trading + dernier prix
// + variation 24 h colorée. La structure n'est rebâtie que si l'ENSEMBLE
// affiché change (filtre/tri) ; un simple rafraîchissement de prix ne met à
// jour que prix/variation/pastille → pas de flicker, l'onglet actif reste
// surligné.
function renderWatchlist() {
  const list = document.getElementById("symbol-list");
  const items = visibleSymbols();
  const signature = items.map(item => item.symbol).join(",");
  if (list.dataset.signature !== signature) {
    list.dataset.signature = signature;
    list.replaceChildren();
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
    change.className = `text-[10px] ${pnlClass(item.change_pct)}`;
  }
  const armed = state.symbols.filter(item => item.trading).length;
  document.getElementById("symbol-count").textContent =
    `${items.length}/${state.symbols.length} · ${armed} armée(s)`;
  // Aucun symbole actif encore choisi : on reprend celui de la session
  // précédente s'il existe toujours, sinon le premier de la liste.
  if (!state.activeSymbol && state.symbols.length) {
    const remembered = prefs.get("live:symbol");
    const known = state.symbols.some(item => item.symbol === remembered);
    setActiveSymbol(known ? remembered : state.symbols[0].symbol);
  }
}

function initWatchlistControls() {
  const search = document.getElementById("symbol-search");
  const sort = document.getElementById("symbol-sort");
  const armed = document.getElementById("symbol-armed-only");
  sort.value = prefs.get("live:sort", "symbol");
  armed.checked = Boolean(prefs.get("live:armedOnly", false));
  search.addEventListener("input", renderWatchlist);
  sort.addEventListener("change", () => {
    prefs.set("live:sort", sort.value);
    renderWatchlist();
  });
  armed.addEventListener("change", () => {
    prefs.set("live:armedOnly", armed.checked);
    renderWatchlist();
  });
}

// --- Positions & P&L -------------------------------------------------------

function sideBadge(side, dimmed) {
  // BUY vert / SELL rouge (convention des terminaux) ; grisé si fermée.
  const color = dimmed
    ? "text-slate-500"
    : side === "BUY" ? "text-emerald-400" : "text-red-400";
  return `<span class="font-semibold ${color}">${side}</span>`;
}

// `data-v` = valeur brute pour le tri et l'export CSV (le texte affiché est
// formaté, coloré, parfois vide — il ne se trie pas correctement).
function openPositionRow(p) {
  const pnl = p.pnl == null ? "—" : `${p.pnl >= 0 ? "+" : ""}${p.pnl}`;
  return `
    <tr class="border-t border-slate-700/60">
      <td class="py-1 pr-2 font-mono">${p.symbol}</td>
      <td class="pr-2" data-v="${p.side}">${sideBadge(p.side, false)}</td>
      <td class="pr-2" data-v="${p.quantity}">${p.quantity}</td>
      <td class="pr-2" data-v="${p.entry_price ?? ""}">${formatPrice(p.entry_price)}</td>
      <td class="pr-2" data-v="${p.current_price ?? ""}">${p.current_price == null ? "—" : formatPrice(p.current_price)}</td>
      <td class="pr-2 ${p.pnl == null ? "" : pnlClass(p.pnl)}" data-v="${p.pnl ?? ""}">${pnl}</td>
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
      <td class="pr-2" data-v="${t.side}">${sideBadge(t.side, true)}</td>
      <td class="pr-2" data-v="${t.quantity}">${t.quantity}</td>
      <td class="pr-2" data-v="${t.fill_price ?? ""}">${t.fill_price == null ? "—" : formatPrice(t.fill_price)}</td>
      <td class="pr-2">—</td>
      <td class="pr-2" data-v="${t.pnl ?? ""}">${pnl}</td>
      <td title="${formatUtcDateTime(t.executed_at)}">${t.status.toLowerCase()} ${formatUtcDate(t.executed_at)}</td>
    </tr>`;
}

// État du compte chez le broker (équité, solde, marge) + perte du jour face à
// la limite configurée. Déconnecté → tirets : on n'invente aucun chiffre.
const ACCOUNT_ROWS = [
  ["equity", "Équité", "Valeur liquidative du compte, rapportée par le broker."],
  ["balance", "Solde", "Solde espèces hors positions ouvertes."],
  ["margin", "Marge", "Marge immobilisée par les positions ouvertes."],
  ["margin_free", "Marge libre", "Marge encore disponible pour de nouvelles entrées."],
];

function renderAccount(data) {
  const box = document.getElementById("account-summary");
  if (!box) return;
  const summary = (data && data.summary) || {};
  const connected = Boolean(data && data.connected);
  box.replaceChildren();
  for (const [key, label, hint] of ACCOUNT_ROWS) {
    const row = document.createElement("div");
    row.className = "flex justify-between gap-2";
    row.title = hint;
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
  // (au-delà, le RiskManager refuse toute nouvelle entrée). Une barre de
  // progression rend la marge restante lisible d'un coup d'œil.
  if (connected && data.day_loss_pct != null && data.max_daily_loss_pct > 0) {
    const loss = data.day_loss_pct;
    const max = data.max_daily_loss_pct;
    const tone = loss >= max ? "text-red-400" : loss >= max / 2 ? "text-amber-400" : "text-slate-300";
    const barColor = loss >= max ? "bg-red-500" : loss >= max / 2 ? "bg-amber-500" : "bg-emerald-500";
    const wrap = document.createElement("div");
    wrap.className = "mt-1 border-t border-slate-700/60 pt-1";
    wrap.title = loss >= max
      ? "Limite atteinte : plus aucune entrée aujourd'hui (les sorties restent autorisées)."
      : "Perte du jour rapportée à l'équité de début de journée UTC.";
    wrap.innerHTML =
      `<div class="flex justify-between gap-2">` +
      `<dt class="text-slate-500">Perte du jour</dt>` +
      `<dd class="font-mono ${tone}">${loss.toFixed(2)} / ${max} %</dd></div>` +
      `<div class="mt-1 h-1 w-full overflow-hidden rounded bg-slate-700">` +
      `<div class="h-full ${barColor}" style="width:${Math.min(100, (loss / max) * 100).toFixed(1)}%"></div></div>`;
    box.append(wrap);
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
  state.positions = await response.json();
  state.trades = state.positions.trades;
  applyTradeMarkers(); // le journal a pu s'enrichir depuis le dernier fetch
  renderPositions();
}

// Rendu (re)joué aussi au changement d'onglet ou de filtre, sans refetch.
function renderPositions() {
  const data = state.positions;
  if (!data) return;
  const activeOnly = document.getElementById("positions-active-only").checked;
  const keep = row => !activeOnly || row.symbol === state.activeSymbol;
  const open = data.open.filter(keep);
  const trades = data.trades.filter(keep);
  const body = document.getElementById("positions-body");
  const empty = document.getElementById("positions-empty");
  body.innerHTML = open.map(openPositionRow).join("") + trades.map(tradeRow).join("");
  // État vide HONNÊTE : rien n'est inventé quand le broker est déconnecté.
  const isEmpty = !open.length && !trades.length;
  empty.classList.toggle("hidden", !isEmpty);
  if (isEmpty) {
    empty.textContent = !data.broker_connected
      ? "Broker déconnecté — aucune position réelle à afficher."
      : activeOnly
        ? `Aucune position ni trade sur ${state.activeSymbol}.`
        : "Aucune position ouverte ni trade exécuté.";
  }
  document.getElementById("positions-count").textContent =
    `${data.open.length}/${data.trades.length}`;

  const total = document.getElementById("total-pnl");
  total.textContent = `${data.total_pnl >= 0 ? "+" : ""}${data.total_pnl}`;
  total.className = `mt-1 text-center text-2xl font-semibold ${pnlClass(data.total_pnl)}`;
  // Détail des deux composantes réelles du P&L : latent (positions ouvertes
  // chez le broker) et réalisé (trades journalisés, P&L calculé par le broker).
  document.getElementById("pnl-detail").innerHTML =
    `${data.open.length} ouverte(s) · ${data.trades.length} trade(s) exécuté(s)<br>` +
    `latent <span class="${pnlClass(data.open_pnl)}">${data.open_pnl}</span> · ` +
    `réalisé <span class="${pnlClass(data.realized_pnl)}">${data.realized_pnl}</span>`;
}

// --- Onglets du panneau bas ------------------------------------------------

function showBottomTab(name) {
  document.querySelectorAll(".bottom-tab").forEach(button => {
    const active = button.dataset.tab === name;
    button.classList.toggle("bg-slate-700", active);
    button.classList.toggle("text-slate-400", !active);
  });
  document.getElementById("tab-positions").classList.toggle("hidden", name !== "positions");
  document.getElementById("tab-logs").classList.toggle("hidden", name !== "logs");
  // Chaque onglet a sa propre barre d'outils (filtre, export…).
  const tools = document.getElementById("positions-tools");
  tools.classList.toggle("hidden", name !== "positions");
  tools.classList.toggle("flex", name === "positions");
  const logTools = document.getElementById("logs-tools");
  logTools.classList.toggle("hidden", name !== "logs");
  logTools.classList.toggle("flex", name === "logs");
  prefs.set("live:bottomTab", name);
}

function initBottomTabs() {
  document.querySelectorAll(".bottom-tab").forEach(button => {
    button.addEventListener("click", () => showBottomTab(button.dataset.tab));
  });
  showBottomTab(prefs.get("live:bottomTab", "positions"));
}

// Panneau bas redimensionnable à la souris, hauteur mémorisée. Bornes : assez
// haut pour lire deux lignes, jamais au point d'écraser le graphique.
function initPanelResizer() {
  const panel = document.getElementById("bottom-panel");
  const handle = document.getElementById("panel-resizer");
  const stored = prefs.get("live:panelHeight");
  if (stored) panel.style.height = `${stored}px`;
  let dragging = false;
  const onMove = (event) => {
    if (!dragging) return;
    const height = Math.round(
      Math.min(window.innerHeight * 0.7, Math.max(80, window.innerHeight - event.clientY)));
    panel.style.height = `${height}px`;
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("select-none");
    prefs.set("live:panelHeight", panel.getBoundingClientRect().height);
  };
  handle.addEventListener("mousedown", (event) => {
    dragging = true;
    event.preventDefault();
    document.body.classList.add("select-none"); // pas de sélection de texte en glissant
  });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

// --- Logs ------------------------------------------------------------------
// Format serveur : « date | NIVEAU | module | message » (core_logging).
// On colore le niveau et on estompe l'entête pour que le message ressorte ;
// sans ça, repérer une erreur dans 100 lignes grises était impossible.

const LEVEL_STYLES = {
  ERROR: "text-red-400",
  CRITICAL: "text-red-400 font-semibold",
  WARNING: "text-amber-400",
  INFO: "text-sky-400",
  DEBUG: "text-slate-500",
};

function escapeHtml(text) {
  return text.replace(/[&<>]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
}

function renderLogs() {
  const container = document.getElementById("log-lines");
  const query = (document.getElementById("logs-filter").value || "").trim().toLowerCase();
  const lines = query
    ? state.logLines.filter(line => line.toLowerCase().includes(query))
    : state.logLines;
  container.innerHTML = lines.map(line => {
    const parts = line.split(" | ");
    if (parts.length < 4) return `<div>${escapeHtml(line)}</div>`;
    const [stamp, level, module, ...rest] = parts;
    const style = LEVEL_STYLES[level.trim()] || "text-slate-400";
    return `<div class="whitespace-pre-wrap">` +
      `<span class="text-slate-600">${escapeHtml(stamp.slice(11, 19))}</span> ` +
      `<span class="${style}">${escapeHtml(level.trim().padEnd(7))}</span> ` +
      `<span class="text-slate-600">${escapeHtml(module.split(".").pop())}</span> ` +
      `<span class="text-slate-300">${escapeHtml(rest.join(" | "))}</span></div>`;
  }).join("");
  document.getElementById("logs-count").textContent =
    query ? `${lines.length}/${state.logLines.length}` : `${state.logLines.length}`;
  // Suivi automatique : on ne force le défilement que si l'utilisateur le
  // demande — sinon relire une vieille ligne devenait impossible.
  if (document.getElementById("logs-autoscroll").checked) {
    const pane = document.getElementById("tab-logs");
    pane.scrollTop = pane.scrollHeight;
  }
}

async function refreshLogs() {
  const response = await fetch("/api/logs?count=200");
  if (!response.ok) return;
  state.logLines = (await response.json()).lines;
  renderLogs();
}

// --- Statut & WebSocket ----------------------------------------------------

async function loadStatus() {
  // Le bandeau (mode / broker / stratégie / DÉMO) est peint par ui.js —
  // identique sur les trois pages ; ici le badge broker ouvre la fenêtre.
  const status = await loadHeaderStatus(openBrokerModal);
  if (!status) return;
  // Le serveur garantit ≥ 1 (validation config), ceinture côté client :
  // un intervalle 0 martèlerait l'API en boucle.
  state.refreshSeconds = Math.max(1, status.chart_refresh_seconds || 5);
  state.tradingMode = status.trading_mode;
  const wasConnected = state.brokerConnected;
  state.brokerConnected = status.broker_connected;
  // Un changement d'état de connexion réactualise le bouton trade actif et
  // prévient l'utilisateur (une chute de connexion est silencieuse sinon).
  if (wasConnected !== state.brokerConnected) {
    if (state.activeSymbol) refreshTradingButton();
    if (!state.brokerConnected && wasConnected) {
      showToast("Broker déconnecté : plus aucun ordre ne partira.", "error");
    }
  }
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
  const button = document.getElementById("broker-connect");
  button.disabled = true;
  button.textContent = "Connexion…";
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
    button.disabled = false;
    button.textContent = "Se connecter";
    return;
  }
  button.disabled = false;
  button.textContent = "Se connecter";
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
  initPanelResizer();
  initWatchlistControls();
  initWebSocket();
  makeSortable(document.getElementById("positions-table"));
  document.getElementById("trading-toggle").addEventListener("click", toggleTrading);
  document.getElementById("chart-fit").addEventListener("click", () => {
    if (state.chart) state.chart.timeScale().scrollToRealTime();
  });
  document.getElementById("positions-active-only").addEventListener("change", renderPositions);
  document.getElementById("positions-export").addEventListener("click", () =>
    exportTableCsv(document.getElementById("positions-table"),
      `pyea_positions_${new Date().toISOString().slice(0, 10)}.csv`));
  document.getElementById("logs-filter").addEventListener("input", renderLogs);
  // Fenêtre de connexion broker (boutons statiques câblés une fois).
  document.getElementById("broker-select").addEventListener("change", renderBrokerDetails);
  document.getElementById("broker-connect").addEventListener("click", connectBroker);
  document.getElementById("broker-disconnect").addEventListener("click", disconnectBroker);
  document.getElementById("broker-cancel").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal-close").addEventListener("click", closeBrokerModal);
  document.getElementById("broker-modal").addEventListener("click", (event) => {
    if (event.target.id === "broker-modal") closeBrokerModal(); // clic sur le fond
  });
  registerOverlay(document.getElementById("broker-modal"), closeBrokerModal); // Échap
  await loadStatus();
  await loadSymbols();      // déclenche le premier rendu du graphique
  await refreshPositions();
  await refreshAccount();
  await refreshLogs();
  scheduleRefresh();
  setInterval(refreshPositions, state.refreshSeconds * 1000);
  setInterval(refreshAccount, state.refreshSeconds * 1000);
  setInterval(loadStatus, 10000);  // connexion broker perdue → header à jour
  setInterval(refreshLogs, 15000);
  // Prix de la watchlist rafraîchis à part (cadence lente : recalcul de
  // tous les symboles), en place — l'onglet actif n'est jamais perturbé.
  setInterval(loadSymbols, 10000);
});
