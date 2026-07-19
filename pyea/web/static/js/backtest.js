/**
 * Interface de backtest — formulaire, exécution, rendu des résultats.
 *
 * Règle du projet : les graphiques sont initialisés dans static/js/
 * (jamais inline dans les templates) et nourris par les endpoints JSON.
 * Ici : courbe d'équité en Chart.js, données de /api/backtest/*.
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

async function runBacktest() {
  const button = document.getElementById("bt-run");
  const message = document.getElementById("bt-message");
  button.disabled = true;
  message.textContent = "Backtest en cours…";
  try {
    const body = {
      symbol: document.getElementById("bt-symbol").value,
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
      const error = await response.json();
      message.textContent = `Erreur : ${error.detail}`;
      return;
    }
    renderResults(await response.json());
    message.textContent = "";
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
      <td class="py-1 pr-2">${trade.side}</td>
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

// --- Entraînement walk-forward ---------------------------------------------
// POST /api/training/run → job en arrière-plan. Progression en temps réel
// par le WebSocket (topic training.progress) + polling de secours.

let currentJobId = null;
let pollTimer = null;

function setProgress(payload) {
  const wrap = document.getElementById("tr-progress-wrap");
  wrap.classList.remove("hidden");
  const percent = payload.total
    ? Math.round(((payload.fold - (payload.phase === "train" ? 1 : 0.5)) / payload.total) * 100)
    : 100;
  document.getElementById("tr-progress-bar").style.width = `${Math.max(2, percent)}%`;
  document.getElementById("tr-progress-text").textContent = payload.message || payload.phase || "";
}

function initTrainingWebSocket() {
  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.topic === "training.progress" && data.payload.job_id === currentJobId) {
      setProgress(data.payload);
    }
  };
}

async function runTraining() {
  const button = document.getElementById("tr-run");
  const message = document.getElementById("tr-message");
  button.disabled = true;
  message.textContent = "";
  document.getElementById("tr-results").classList.add("hidden");
  const body = {
    symbol: document.getElementById("bt-symbol").value,
    timeframe: document.getElementById("bt-timeframe").value,
    strategy: document.getElementById("bt-strategy").value,
    folds: parseInt(document.getElementById("tr-folds").value, 10) || 4,
  };
  const start = document.getElementById("bt-start").value;
  const end = document.getElementById("bt-end").value;
  if (start) body.start = start;
  if (end) body.end = end;

  const response = await fetch("/api/training/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    message.textContent = `Erreur : ${(await response.json()).detail}`;
    button.disabled = false;
    return;
  }
  currentJobId = (await response.json()).job_id;
  setProgress({ message: "Démarrage…" });
  pollTimer = setInterval(pollJob, 2000);
}

async function pollJob() {
  const response = await fetch(`/api/training/jobs/${currentJobId}`);
  if (!response.ok) return;
  const job = await response.json();
  if (job.status === "running") return;
  clearInterval(pollTimer);
  document.getElementById("tr-run").disabled = false;
  document.getElementById("tr-progress-wrap").classList.add("hidden");
  const message = document.getElementById("tr-message");
  if (job.status === "completed") {
    message.textContent = "";
    renderTraining(job.result);
  } else if (job.status === "cancelled") {
    message.textContent = "Entraînement annulé.";
  } else {
    message.textContent = `Échec : ${job.error}`;
  }
  loadRuns();
}

async function cancelTraining() {
  if (currentJobId) await fetch(`/api/training/jobs/${currentJobId}`, { method: "DELETE" });
}

function renderTraining(report) {
  const stats = report.oos_stats;
  document.getElementById("tr-results").classList.remove("hidden");
  document.getElementById("tr-stats").innerHTML =
    statCard("Trades OOS", stats.trades) +
    statCard("P&L OOS", stats.total_pnl, true) +
    statCard("Taux de gain OOS", stats.win_rate === null ? null : `${(stats.win_rate * 100).toFixed(1)} %`) +
    statCard("Drawdown max OOS", stats.max_drawdown);
  document.getElementById("tr-folds-body").innerHTML = report.folds.map(fold => `
    <tr class="border-t border-slate-700/60">
      <td class="py-1 pr-2">${fold.index}</td>
      <td class="pr-2">${fold.train_bars}</td>
      <td class="pr-2">${fold.test_start.slice(0, 10)} → ${fold.test_end.slice(0, 10)}</td>
      <td class="pr-2">${fold.test_stats.trades}</td>
      <td class="pr-2 ${fold.test_stats.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"}">${fold.test_stats.total_pnl}</td>
      <td class="pr-2">${fold.test_stats.win_rate === null ? "—" : (fold.test_stats.win_rate * 100).toFixed(1) + " %"}</td>
      <td>${fold.test_stats.max_drawdown}</td>
    </tr>`).join("");
}

async function loadRuns() {
  const response = await fetch("/api/training/runs");
  if (!response.ok) return;
  const data = await response.json();
  document.getElementById("tr-runs-body").innerHTML = data.runs.length
    ? data.runs.map(run => `
        <tr class="border-t border-slate-700/60 ${run.status !== "completed" ? "text-slate-500" : ""}">
          <td class="py-1 pr-2 font-mono">${run.id}</td>
          <td class="pr-2">${run.created_at.slice(0, 16).replace("T", " ")}</td>
          <td class="pr-2">${run.symbol}</td>
          <td class="pr-2">${run.timeframe}</td>
          <td class="pr-2">${run.folds}</td>
          <td class="pr-2">${run.status}</td>
          <td class="pr-2">${run.oos_trades ?? "—"}</td>
          <td class="pr-2">${run.oos_pnl ?? "—"}</td>
          <td>${run.oos_win_rate === null || run.oos_win_rate === undefined ? "—" : (run.oos_win_rate * 100).toFixed(1) + " %"}</td>
        </tr>`).join("")
    : `<tr><td colspan="9" class="py-2 text-slate-500">Aucun entraînement pour l'instant.</td></tr>`;
}

// --- Init ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadDatasets();
  loadRuns();
  initTrainingWebSocket();
  document.getElementById("bt-run").addEventListener("click", runBacktest);
  document.getElementById("tr-run").addEventListener("click", runTraining);
  document.getElementById("tr-cancel").addEventListener("click", cancelTraining);
});
