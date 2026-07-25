/**
 * Connexion WebSocket partagée par les pages du dashboard.
 *
 * Une seule raison d'être : le flux temps réel doit SURVIVRE aux coupures.
 * Sans reconnexion, un simple redémarrage du serveur (ou un timeout de proxy
 * sur un VPS) laissait l'indicateur sur « hors ligne » jusqu'au rechargement
 * manuel de la page — et la progression d'entraînement cessait d'arriver.
 *
 * Reconnexion à intervalle croissant (1 s → 30 s) pour ne pas marteler un
 * serveur qui redémarre. L'indicateur `#ws-status` reflète l'état RÉEL de la
 * connexion : vert connecté, ambre en tentative, rouge hors ligne.
 */

"use strict";

const WS_RETRY_MIN_MS = 1000;
const WS_RETRY_MAX_MS = 30000;

function openLiveSocket(onMessage) {
  const statusEl = document.getElementById("ws-status");
  let retryMs = WS_RETRY_MIN_MS;
  let closed = false;

  function setStatus(text, color) {
    if (statusEl) {
      statusEl.textContent = text;
      statusEl.className = `text-xs ${color}`;
    }
  }

  function connect() {
    // wss derrière HTTPS (reverse proxy sur un VPS) — ws:// y serait bloqué.
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    let ws;
    try {
      ws = new WebSocket(`${protocol}://${window.location.host}/ws`);
    } catch (err) {
      scheduleRetry();
      return;
    }
    ws.onopen = () => {
      retryMs = WS_RETRY_MIN_MS; // connexion rétablie : on repart d'un délai court
      setStatus("● temps réel", "text-emerald-400");
    };
    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return; // trame illisible : on ignore plutôt que de casser le flux
      }
      if (onMessage) onMessage(data);
    };
    ws.onclose = () => {
      if (closed) return;
      setStatus("● reconnexion…", "text-amber-400");
      scheduleRetry();
    };
    ws.onerror = () => ws.close();
  }

  function scheduleRetry() {
    setTimeout(() => {
      if (closed) return;
      setStatus("● hors ligne", "text-red-400");
      connect();
    }, retryMs);
    retryMs = Math.min(retryMs * 2, WS_RETRY_MAX_MS);
  }

  connect();
  return { close: () => { closed = true; } };
}
