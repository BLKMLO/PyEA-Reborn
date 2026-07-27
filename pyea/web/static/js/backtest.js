/**
 * Interface de backtest — run unique : formulaire, exécution, résultats.
 *
 * L'entraînement walk-forward vit désormais sur sa propre page
 * (/training, training.js). Ici : un seul backtest → courbe d'équité
 * (Chart.js) + trades, données de /api/backtest/*.
 *
 * Règle du projet : les graphiques sont initialisés dans static/js/
 * (jamais inline dans les templates) et nourris par les endpoints JSON.
 */

"use strict";

// Socle partagé (ui.js) : formats, formulaires, cartes. Destructuration —
// ui.js est enfermé dans une IIFE et n'expose que window.PyEA, pour qu'aucun
// nom ne se télescope entre les scripts de page (ils partagent tous le même
// scope global, ce ne sont pas des modules ES).
const {
  fillSelect, statCard, apiErrorText, num2, pct1, shortStamp,
  loadHeaderStatus, rememberForm,
} = window.PyEA;

let equityChart = null;

// --- Formulaire ------------------------------------------------------------

async function loadDatasets() {
  const response = await fetch("/api/backtest/datasets");
  const data = await response.json();
  const message = document.getElementById("bt-message");
  if (!data.datasets.length) {
    message.textContent =
      "Aucun historique local — lancer `python download_history.py` d'abord.";
    document.getElementById("bt-run").disabled = true;
    return;
  }
  fillSelect("bt-symbol", data.datasets.map(d => d.symbol));
  fillSelect("bt-timeframe", data.timeframes, "H1");
  fillSelect("bt-strategy", data.strategies);
  message.textContent = "";
}

// --- Exécution -------------------------------------------------------------

async function runBacktest() {
  const button = document.getElementById("bt-run");
  const message = document.getElementById("bt-message");
  button.disabled = true;
  message.textContent = "Backtest en cours…";
  const symbol = document.getElementById("bt-symbol").value;
  showToast(`Backtest ${symbol} lancé…`, "info");
  try {
    const body = {
      symbol,
      timeframe: document.getElementById("bt-timeframe").value,
      strategy: document.getElementById("bt-strategy").value,
    };
    const start = document.getElementById("bt-start").value;
    const end = document.getElementById("bt-end").value;
    if (start) body.start = start;
    if (end) body.end = end;

    const response = await fetch("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = apiErrorText(await response.json());
      message.textContent = `Erreur : ${detail}`;
      showToast(`Backtest échoué : ${detail}`, "error");
      return;
    }
    const result = await response.json();
    renderResults(result);
    message.textContent = "";
    showToast(`Backtest terminé : ${result.stats.trades} trade(s).`, "success");
  } catch (error) {
    message.textContent = `Erreur réseau : ${error.message}`;
    showToast(`Erreur réseau : ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

// --- Rendu -----------------------------------------------------------------

// Coûts payés (spread + commission). Sans données ask, rien n'est modélisé :
// on le dit franchement plutôt que d'afficher un zéro trompeur.
function costLabel(stats) {
  if (!stats.costs_modelled) return "non modélisés";
  return `−${Number(stats.total_costs).toFixed(5)}`;
}

// Bandeau sous les cartes : d'où vient le spread, et ce qu'il coûte face au
// P&L brut. C'est l'écart entre « ça a l'air de marcher » et « ça marche ».
function renderCostNote(stats) {
  const note = document.getElementById("bt-cost-note");
  if (!note) return;
  if (!stats.costs_modelled) {
    note.className = "rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-300";
    note.textContent =
      "⚠ Aucun coût de transaction modélisé : cet historique n'a pas de colonnes " +
      "ask (spread). Le résultat est donc OPTIMISTE — re-téléchargez les données " +
      "pour obtenir un chiffre réaliste.";
    note.classList.remove("hidden");
    return;
  }
  const gross = Number(stats.gross_pnl);
  const costs = Number(stats.total_costs);
  const part = gross > 0 ? ` — soit ${((costs / gross) * 100).toFixed(0)} % du P&L brut` : "";
  note.className = "rounded border border-slate-700 bg-slate-800/60 px-3 py-2 text-[11px] text-slate-400";
  note.textContent =
    `Spread médian mesuré dans les données : ${stats.spread} ` +
    `(commission ${stats.commission_per_unit} par côté). ` +
    `P&L brut ${gross.toFixed(5)} − coûts ${costs.toFixed(5)} = net ` +
    `${Number(stats.total_pnl).toFixed(5)}${part}.`;
  note.classList.remove("hidden");
}

// Modèle chargé pour ce run (page backtest = dernier run entraîné de la
// paire, même règle de sélection que le live). Sans modèle, une stratégie
// ML est muette : le dire évite de lire une courbe plate comme un bug.
function renderModelNote(result) {
  const note = document.getElementById("bt-model-note");
  if (!note) return;
  const model = result.model;
  if (!model) {
    note.className = "rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-300";
    note.textContent =
      "⚠ Aucun modèle entraîné pour cette paire : la stratégie est MUETTE " +
      "(0 trade). Entraînez-la d'abord sur la page Entraînement.";
    note.classList.remove("hidden");
    return;
  }
  const mismatch = model.timeframe !== result.timeframe;
  note.className = mismatch
    ? "rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-300"
    : "rounded border border-slate-700 bg-slate-800/60 px-3 py-2 text-[11px] text-slate-400";
  note.textContent =
    `Modèle : run ${model.run_id}, pli ${model.fold} (entraîné en ${model.timeframe})` +
    (mismatch
      ? ` — ⚠ vous backtestez en ${result.timeframe} : les features n'ont pas la même granularité.`
      : ".");
  note.classList.remove("hidden");
}

function renderResults(result) {
  const stats = result.stats;
  document.getElementById("bt-empty").classList.add("hidden");
  const results = document.getElementById("bt-results");
  results.classList.remove("hidden");
  results.classList.add("flex");
  document.getElementById("bt-stats").innerHTML =
    statCard("Bougies", stats.bars) +
    statCard("Trades", stats.trades) +
    // P&L NET de spread et commission — le seul chiffre qui compte.
    statCard("P&L net", stats.total_pnl, { colored: true }) +
    statCard("Coûts", costLabel(stats)) +
    statCard("Taux de gain", pct1(stats.win_rate)) +
    statCard("Drawdown max", stats.max_drawdown) +
    // Métriques fournies par backtrader (moteur d'exécution).
    statCard("Sharpe", num2(stats.sharpe_ratio)) +
    statCard("SQN", num2(stats.sqn)) +
    statCard("Profit factor", num2(stats.profit_factor));
  renderModelNote(result);
  renderCostNote(stats);

  if (equityChart) equityChart.destroy();
  equityChart = new Chart(document.getElementById("bt-equity"), {
    type: "line",
    data: {
      labels: result.equity_curve.map(p => shortStamp(p.time)),
      datasets: [{
        label: "Équité",
        data: result.equity_curve.map(p => p.equity),
        borderColor: "#34d399",
        backgroundColor: "rgba(52, 211, 153, 0.08)",
        fill: true,
        pointRadius: 0,
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#94a3b8", maxTicksLimit: 10 }, grid: { color: "#1e293b" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "#334155" } },
      },
    },
  });

  const rows = result.trades.map(trade => `
    <tr class="border-t border-slate-700/60">
      <td class="py-1 pr-2"><span class="font-semibold ${trade.side === "BUY" ? "text-emerald-400" : "text-red-400"}">${trade.side}</span></td>
      <td class="pr-2">${trade.quantity}</td>
      <td class="pr-2">${shortStamp(trade.entry_time)}</td>
      <td class="pr-2">${trade.entry_price}</td>
      <td class="pr-2">${shortStamp(trade.exit_time)}</td>
      <td class="pr-2">${trade.exit_price}</td>
      <td class="${trade.pnl >= 0 ? "text-emerald-400" : "text-red-400"}">${trade.pnl >= 0 ? "+" : ""}${trade.pnl}</td>
    </tr>`);
  document.getElementById("bt-trades").innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="7" class="py-2 text-slate-500">Aucun trade — la stratégie n'a émis aucun signal sur la période.</td></tr>`;
}

// --- Init ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  // Bandeau d'état sur CETTE page aussi (cf. training.js) : sans callback,
  // le badge broker devient un lien vers la page Live.
  loadHeaderStatus();
  await loadDatasets();
  // Restauré après le remplissage des <select> : relancer le même backtest en
  // ne changeant qu'un paramètre ne demande plus de tout re-saisir.
  rememberForm("backtest", ["bt-symbol", "bt-timeframe", "bt-strategy",
                            "bt-start", "bt-end"]);
  document.getElementById("bt-run").addEventListener("click", runBacktest);
});
