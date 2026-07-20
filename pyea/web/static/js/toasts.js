/**
 * Notifications « toast » — retour visuel des actions importantes.
 *
 * Chargé sur toutes les pages (base.html). Usage :
 *   showToast("Backtest terminé", "success");
 *   showToast("Broker déconnecté", "error");
 *   showToast("Entraînement lancé…", "info");
 *
 * Volontairement sans dépendance : un conteneur fixe en bas à droite,
 * des toasts empilés qui disparaissent seuls (les erreurs restent plus
 * longtemps). Zéro état global, rien à initialiser.
 */

"use strict";

const TOAST_STYLES = {
  success: { border: "border-emerald-500", icon: "✓", iconColor: "text-emerald-400" },
  error: { border: "border-red-500", icon: "✕", iconColor: "text-red-400" },
  info: { border: "border-sky-500", icon: "ℹ", iconColor: "text-sky-400" },
};

function toastContainer() {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.className = "fixed bottom-4 right-4 z-50 flex flex-col gap-2";
    document.body.appendChild(container);
  }
  return container;
}

function showToast(message, type = "info", duration = null) {
  const style = TOAST_STYLES[type] || TOAST_STYLES.info;
  const toast = document.createElement("div");
  toast.className =
    `flex max-w-sm items-start gap-2 rounded-md border-l-4 ${style.border} ` +
    "bg-slate-800 px-4 py-2 text-sm text-slate-100 shadow-lg " +
    "transition-all duration-300 opacity-0 translate-x-4";
  toast.innerHTML =
    `<span class="${style.iconColor} font-bold">${style.icon}</span>` +
    `<span class="flex-1">${message}</span>`;
  toastContainer().appendChild(toast);

  // Entrée animée (au prochain frame pour déclencher la transition).
  requestAnimationFrame(() => {
    toast.classList.remove("opacity-0", "translate-x-4");
  });

  const ttl = duration ?? (type === "error" ? 6000 : 3500);
  setTimeout(() => {
    toast.classList.add("opacity-0", "translate-x-4");
    setTimeout(() => toast.remove(), 300);
  }, ttl);
}

// Exposé en global pour les autres scripts de page.
window.showToast = showToast;
