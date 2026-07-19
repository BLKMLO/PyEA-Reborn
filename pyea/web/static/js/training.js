/**
 * Page Entraînement — walk-forward out-of-sample.
 *
 * POST /api/training/run → job en arrière-plan ; progression temps réel par
 * le WebSocket (topic training.progress) + polling de secours. Le rendu
 * met en avant l'out-of-sample : cartes OOS, courbe d'équité OOS, table des
 * plis avec l'AUC in-sample en regard (écart = surapprentissage), et la
 * définition figée du modèle (lecture seule, servie par l'API).
 *
 * Règle du projet : graphiques initialisés dans static/js/, jamais inline.
 */

"use strict";

let currentJobId = null;
let pollTimer = null;
let oosEquityChart = null;

// --- Helpers ---------------------------------------------------------------

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

function statCard(label, value, colored) {
  const color = !colored ? "text-slate-100"
    : value >= 0 ? "text-emerald-400" : "text-red-400";
  return `
    <div class="rounded-lg bg-slate-800 p-3">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">${label}</div>
      <div class="mt-1 text-lg font-semibold ${color}">${value ?? "—"}</div>
    </div>`;
}

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

// --- Formulaire + définition du modèle -------------------------------------

async function loadDatasets() {
  const response = await fetch("/api/backtest/datasets");
  const data = await response.json();
  const message = document.getElementById("tr-message");
  if (!data.datasets.length) {
    message.textContent =
      "Aucun historique local — lancer `python download_history.py` d'abord.";
    document.getElementById("tr-run").disabled = true;
    return;
  }
  fillSelect("tr-symbol", data.datasets.map(d => d.symbol));
  fillSelect("tr-timeframe", data.timeframes, "H1");
  fillSelect("tr-strategy", data.strategies);
  message.textContent = "";
}

const DEF_LABELS = {
  n_features: v => ["Features", v],
  barrier_atr_mult: v => ["Barrières", `±${v} · ATR`],
  max_hold_days: v => ["Horizon max", `${v} j`],
  objective: v => ["Objectif", v],
};

async function loadModelDefinition(strategy) {
  const container = document.getElementById("tr-model-def");
  const response = await fetch(`/api/training/definition/${strategy}`);
  if (!response.ok) { container.innerHTML = ""; return; }
  const def = (await response.json()).definition;
  if (!def) {
    container.innerHTML = `<div class="text-slate-500">Aucune définition exposée.</div>`;
    return;
  }
  const rows = Object.entries(def)
    .filter(([key]) => key in DEF_LABELS)
    .map(([key, value]) => {
      const [label, text] = DEF_LABELS[key](value);
      return `<div class="flex justify-between gap-2"><dt>${label}</dt><dd class="text-slate-300">${text}</dd></div>`;
    });
  if (def.enter_long_threshold != null && def.enter_short_threshold != null) {
    rows.push(`<div class="flex justify-between gap-2"><dt>Seuils</dt>
      <dd class="text-slate-300">long ≥ ${def.enter_long_threshold} · short ≤ ${def.enter_short_threshold}</dd></div>`);
  }
  container.innerHTML = rows.join("");
}

// --- Exécution -------------------------------------------------------------

async function runTraining() {
  const button = document.getElementById("tr-run");
  const message = document.getElementById("tr-message");
  button.disabled = true;
  message.textContent = "";
  // Retour visuel IMMÉDIAT : le serveur charge l'historique avant de
  // répondre (plusieurs secondes sur un gros M1) — sans cela, le clic
  // semblait ne rien faire.
  setProgress({ message: "Chargement de l'historique…" });
  const body = {
    symbol: document.getElementById("tr-symbol").value,
    timeframe: document.getElementById("tr-timeframe").value,
    strategy: document.getElementById("tr-strategy").value,
    folds: Math.min(20, Math.max(1, parseInt(document.getElementById("tr-folds").value, 10) || 4)),
  };
  const start = document.getElementById("tr-start").value;
  const end = document.getElementById("tr-end").value;
  if (start) body.start = start;
  if (end) body.end = end;

  try {
    const response = await fetch("/api/training/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      message.textContent = `Erreur : ${apiErrorText(await response.json())}`;
      button.disabled = false;
      document.getElementById("tr-progress-wrap").classList.add("hidden");
      return;
    }
    currentJobId = (await response.json()).job_id;
  } catch (error) {
    message.textContent = `Erreur réseau : ${error.message}`;
    button.disabled = false;
    document.getElementById("tr-progress-wrap").classList.add("hidden");
    return;
  }
  setProgress({ message: "Démarrage…" });
  startPolling();
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 2000);
}

// Au chargement de la page : si un entraînement tourne déjà (page rechargée
// ou rouverte en plein run), on se RÉ-ATTACHE — progression, annulation et
// résultat restent accessibles au lieu d'un bouton muet répondant « un
// entraînement est déjà en cours ».
async function resumeRunningJob() {
  try {
    const response = await fetch("/api/training/current-job");
    if (!response.ok) return;
    const job = (await response.json()).job;
    if (!job || job.status !== "running") return;
    currentJobId = job.id;
    document.getElementById("tr-run").disabled = true;
    setProgress(
      Object.keys(job.progress || {}).length
        ? job.progress
        : { message: "Entraînement en cours…" }
    );
    startPolling();
  } catch {
    // Serveur injoignable : la page reste utilisable, l'utilisateur relancera.
  }
}

async function pollJob() {
  let job;
  try {
    const response = await fetch(`/api/training/jobs/${currentJobId}`);
    if (!response.ok) return;
    job = await response.json();
  } catch {
    return; // erreur réseau passagère : on retentera au prochain tick
  }
  if (job.status === "running") return;
  clearInterval(pollTimer);
  pollTimer = null;
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
  if (!currentJobId) return;
  document.getElementById("tr-progress-text").textContent = "Annulation demandée…";
  await fetch(`/api/training/jobs/${currentJobId}`, { method: "DELETE" });
}

// --- Progression (WebSocket + polling) -------------------------------------

function setProgress(payload) {
  const wrap = document.getElementById("tr-progress-wrap");
  // Le message final « done » (fin de job) ne doit pas RÉ-AFFICHER la barre
  // que pollJob vient de masquer — c'est pollJob qui rend le résultat.
  if (payload.phase === "done") {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const percent = payload.total
    ? Math.round(((payload.fold - (payload.phase === "train" ? 1 : 0.5)) / payload.total) * 100)
    : 3; // pas encore de plis : barre « amorcée », pas pleine
  document.getElementById("tr-progress-bar").style.width = `${Math.max(3, percent)}%`;
  document.getElementById("tr-progress-text").textContent = payload.message || payload.phase || "";
}

function initTrainingWebSocket() {
  const statusEl = document.getElementById("ws-status");
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);
  ws.onopen = () => { statusEl.textContent = "WS : connecté"; };
  ws.onclose = () => { statusEl.textContent = "WS : déconnecté"; };
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.topic === "training.progress" && data.payload.job_id === currentJobId) {
      setProgress(data.payload);
    }
  };
}

// --- Rendu -----------------------------------------------------------------

function renderTraining(report) {
  const stats = report.oos_stats;
  document.getElementById("tr-empty").classList.add("hidden");
  const results = document.getElementById("tr-results");
  results.classList.remove("hidden");
  results.classList.add("flex");

  document.getElementById("tr-stats").innerHTML =
    statCard("Trades OOS", stats.trades) +
    statCard("P&L OOS", stats.total_pnl, true) +
    statCard("Taux de gain OOS", stats.win_rate === null ? null : `${(stats.win_rate * 100).toFixed(1)} %`) +
    statCard("Drawdown max OOS", stats.max_drawdown);

  const curve = report.oos_equity_curve || [];
  if (oosEquityChart) oosEquityChart.destroy();
  oosEquityChart = new Chart(document.getElementById("tr-equity"), {
    type: "line",
    data: {
      labels: curve.map(p => p.time.slice(0, 16).replace("T", " ")),
      datasets: [{
        label: "Équité OOS",
        data: curve.map(p => p.equity),
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56, 189, 248, 0.08)",
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

  document.getElementById("tr-folds-body").innerHTML = report.folds.map(fold => {
    const tr = fold.train_report || {};
    const aucIs = tr.train_auc != null ? tr.train_auc.toFixed(3) : "—";
    return `
    <tr class="border-t border-slate-700/60">
      <td class="py-1 pr-2">${fold.index}</td>
      <td class="pr-2">${fold.train_bars}</td>
      <td class="pr-2 text-slate-400">${aucIs}</td>
      <td class="pr-2">${fold.test_start.slice(0, 10)} → ${fold.test_end.slice(0, 10)}</td>
      <td class="pr-2">${fold.test_stats.trades}</td>
      <td class="pr-2 ${fold.test_stats.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"}">${fold.test_stats.total_pnl}</td>
      <td class="pr-2">${fold.test_stats.win_rate === null ? "—" : (fold.test_stats.win_rate * 100).toFixed(1) + " %"}</td>
      <td>${fold.test_stats.max_drawdown}</td>
    </tr>`;
  }).join("");
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

document.addEventListener("DOMContentLoaded", async () => {
  await loadDatasets();
  loadModelDefinition(document.getElementById("tr-strategy").value);
  loadRuns();
  initTrainingWebSocket();
  document.getElementById("tr-run").addEventListener("click", runTraining);
  document.getElementById("tr-cancel").addEventListener("click", cancelTraining);
  document.getElementById("tr-strategy").addEventListener("change", (e) => loadModelDefinition(e.target.value));
  await resumeRunningJob(); // page rechargée pendant un run → ré-attachement
});
