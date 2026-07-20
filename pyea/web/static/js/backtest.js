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

let equityChart = null;

// --- Formulaire ------------------------------------------------------------

function fillSelect(id, values, selected) {
  const select = document.getElementById(id);
  select.innerHTML = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selected) option.selected = true;
    select.appendChild(option);
  }
}

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

// Erreur API lisible : FastAPI renvoie soit une chaîne (HTTPException),
// soit un tableau d'objets (erreur de validation 422) — sans ce garde-fou,
// l'utilisateur voyait « [object Object] ».
function apiErrorText(payload) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(e => `${(e.loc || []).join(".")} : ${e.msg}`).join(" ; ");
  }
  return "erreur inattendue du serveur.";
}

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

function statCard(label, value, colored) {
  const color = !colored ? "text-slate-100"
    : value >= 0 ? "text-emerald-400" : "text-red-400";
  return `
    <div class="rounded-lg bg-slate-800 p-3">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">${label}</div>
      <div class="mt-1 text-lg font-semibold ${color}">${value ?? "—"}</div>
    </div>`;
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
    statCard("P&L total", stats.total_pnl, true) +
    statCard("Taux de gain", stats.win_rate === null ? null : `${(stats.win_rate * 100).toFixed(1)} %`) +
    statCard("Drawdown max", stats.max_drawdown);

  if (equityChart) equityChart.destroy();
  equityChart = new Chart(document.getElementById("bt-equity"), {
    type: "line",
    data: {
      labels: result.equity_curve.map(p => p.time.slice(0, 16).replace("T", " ")),
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
      <td class="pr-2">${trade.entry_time.slice(0, 16).replace("T", " ")}</td>
      <td class="pr-2">${trade.entry_price}</td>
      <td class="pr-2">${trade.exit_time.slice(0, 16).replace("T", " ")}</td>
      <td class="pr-2">${trade.exit_price}</td>
      <td class="${trade.pnl >= 0 ? "text-emerald-400" : "text-red-400"}">${trade.pnl >= 0 ? "+" : ""}${trade.pnl}</td>
    </tr>`);
  document.getElementById("bt-trades").innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="7" class="py-2 text-slate-500">Aucun trade — la stratégie n'a émis aucun signal sur la période.</td></tr>`;
}

// --- Init ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadDatasets();
  document.getElementById("bt-run").addEventListener("click", runBacktest);
});
