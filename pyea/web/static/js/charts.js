/**
 * Initialisation des graphiques Chart.js du dashboard.
 *
 * Règle du projet : tout graphique est créé ici (jamais inline dans les
 * templates) et se nourrit d'un endpoint JSON de /api/charts/*.
 * Le WebSocket (/ws) mettra à jour les courbes en live plus tard.
 */

async function initPriceChart() {
  const response = await fetch("/api/charts/price-history?symbol=DEMO&points=60");
  const data = await response.json();

  const ctx = document.getElementById("price-chart");
  if (!ctx) return;

  new Chart(ctx, {
    type: "line",
    data: {
      labels: data.labels,
      datasets: [{
        label: data.symbol,
        data: data.prices,
        borderColor: "#34d399",
        backgroundColor: "rgba(52, 211, 153, 0.1)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#cbd5e1" } } },
      scales: {
        x: { ticks: { color: "#94a3b8" }, grid: { color: "#334155" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "#334155" } },
      },
    },
  });
}

function initWebSocket() {
  const statusEl = document.getElementById("ws-status");
  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.onopen = () => { statusEl.textContent = "WebSocket : connecté"; };
  ws.onclose = () => { statusEl.textContent = "WebSocket : déconnecté"; };
  ws.onmessage = (event) => {
    // Plus tard : dispatcher par topic (market.tick, strategy.signal, …)
    // pour mettre à jour les graphiques et le statut en live.
    console.debug("WS", JSON.parse(event.data));
  };
}

document.addEventListener("DOMContentLoaded", () => {
  initPriceChart();
  initWebSocket();
});
