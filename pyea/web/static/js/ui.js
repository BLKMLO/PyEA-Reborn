/**
 * Socle d'interface partagé par les trois pages de PyEA.
 *
 * Raison d'être : `backtest.js` et `training.js` ré-écrivaient les mêmes
 * helpers (remplissage de <select>, lecture d'une erreur FastAPI, carte de
 * statistique…) et chaque page réinventait ses formats de nombre. Tout ce
 * qui n'est pas propre à UNE page vit ici, exposé sous `window.PyEA`.
 *
 * Contenu :
 *  - formatage (nombres, pourcentages, durées, dates UTC) ;
 *  - helpers de formulaire (`fillSelect`, mémorisation des champs) ;
 *  - `prefs` : préférences d'interface persistées en localStorage
 *    (NAVIGATEUR uniquement — aucune donnée de compte, aucun secret) ;
 *  - tables triables + export CSV de ce qui est AFFICHÉ ;
 *  - horloge UTC du header (le moteur raisonne en UTC : journée de risque,
 *    clôture de fin de semaine ISO — l'heure locale induirait en erreur) ;
 *  - raccourcis clavier + aide « ? » ;
 *  - registre des fenêtres modales pour la touche Échap.
 */

"use strict";

// --- Préférences d'interface (localStorage) ---------------------------------
// Uniquement du confort d'affichage : symbole actif, tri de la watchlist,
// hauteur du panneau bas, derniers paramètres de formulaire. Jamais de
// données de compte ni de secret. Un navigateur sans localStorage
// (navigation privée verrouillée) dégrade proprement vers les défauts.

const PREFS_PREFIX = "pyea:";

const prefs = {
  get(key, fallback = null) {
    try {
      const raw = window.localStorage.getItem(PREFS_PREFIX + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(PREFS_PREFIX + key, JSON.stringify(value));
    } catch (err) {
      /* quota plein ou stockage refusé : l'interface marche sans mémoire */
    }
  },
};

// --- Formatage --------------------------------------------------------------

// Décimales selon l'ordre de grandeur : 5 pour le forex (0.8xxxx), 2 pour
// JPY / métaux / indices (>= 100). Évite « 1823.40000 » comme « 0.86 » tronqué.
function formatPrice(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(2) : value.toFixed(5);
}

function num2(value) {
  return value == null || Number.isNaN(value) ? "—" : Number(value).toFixed(2);
}

function pct1(value) {
  return value == null || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(1)} %`;
}

function signed(value, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
}

// Classe de couleur d'un montant : vert positif, rouge négatif.
function pnlClass(value) {
  return value >= 0 ? "text-emerald-400" : "text-red-400";
}

// Durée lisible d'un chrono d'exécution (backtest, entraînement).
function formatDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes} min ${String(seconds).padStart(2, "0")} s` : `${seconds} s`;
}

// Horodatage serveur → date/heure locale. Le serveur envoie un fuseau
// explicite ; on tolère une valeur sans fuseau (base écrite par une version
// antérieure) en la lisant comme de l'UTC — jamais comme du local.
function utcDate(iso) {
  if (!iso) return null;
  const stamped = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(stamped);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatUtcDate(iso) {
  const date = utcDate(iso);
  return date ? date.toLocaleDateString() : "";
}

function formatUtcDateTime(iso) {
  const date = utcDate(iso);
  return date ? `${date.toLocaleDateString()} ${date.toLocaleTimeString()}` : "";
}

// « 2026-07-25T14:30:00+00:00 » → « 2026-07-25 14:30 » (tables denses).
function shortStamp(iso) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

// --- Formulaires ------------------------------------------------------------

function fillSelect(id, values, selected) {
  const select = document.getElementById(id);
  if (!select) return;
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selected) option.selected = true;
    select.appendChild(option);
  }
}

/**
 * Mémorise les champs d'un formulaire d'une visite à l'autre.
 *
 * Relancer le même backtest en changeant un seul paramètre était pénible :
 * tout était à re-saisir à chaque rechargement. `ids` = identifiants des
 * champs ; la valeur n'est restaurée que si elle existe encore dans le
 * <select> (un symbole supprimé de `data/history/` ne ressuscite pas).
 */
function rememberForm(namespace, ids) {
  const stored = prefs.get(`form:${namespace}`, {}) || {};
  for (const id of ids) {
    const field = document.getElementById(id);
    if (!field) continue;
    const value = stored[id];
    if (value != null && value !== "") {
      if (field.tagName === "SELECT") {
        if ([...field.options].some((option) => option.value === value)) field.value = value;
      } else {
        field.value = value;
      }
    }
    field.addEventListener("change", () => {
      const current = prefs.get(`form:${namespace}`, {}) || {};
      current[id] = field.value;
      prefs.set(`form:${namespace}`, current);
    });
  }
}

// Erreur API lisible : FastAPI renvoie soit une chaîne (HTTPException), soit
// un tableau d'objets (validation 422) — sans ce garde-fou, l'utilisateur
// voyait « [object Object] ».
function apiErrorText(payload) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => `${(e.loc || []).join(".")} : ${e.msg}`).join(" ; ");
  }
  return "erreur inattendue du serveur.";
}

// --- Cartes de statistique --------------------------------------------------

/**
 * Carte « chiffre clé ». `hint` alimente une infobulle (title) : les
 * métriques d'un backtest ne parlent pas d'elles-mêmes (SQN, profit factor).
 */
function statCard(label, value, options = {}) {
  const { colored = false, hint = "" } = options;
  const display = value == null || value === "" ? "—" : value;
  const color = colored && typeof value === "number"
    ? pnlClass(value)
    : "text-slate-100";
  const title = hint ? ` title="${hint.replace(/"/g, "&quot;")}"` : "";
  const cursor = hint ? " cursor-help" : "";
  return `
    <div class="rounded-lg border border-slate-700/60 bg-slate-800 p-3${cursor}"${title}>
      <div class="text-[11px] uppercase tracking-wide text-slate-500">${label}</div>
      <div class="mt-1 text-lg font-semibold ${color}">${display}</div>
    </div>`;
}

// --- Tables triables --------------------------------------------------------

/**
 * Rend une table triable au clic sur ses en-têtes.
 *
 * Convention : un `<th>` portant `data-sort="num"` (ou `"text"`) devient
 * cliquable ; la valeur triée est `data-v` de la cellule si présent, sinon
 * son texte. On trie le DOM en place — la table affichée EST la source, donc
 * aucun risque de divergence entre ce qu'on voit et ce qu'on trie.
 */
function makeSortable(table) {
  if (!table || table.dataset.sortable === "on") return;
  table.dataset.sortable = "on";
  const headers = [...table.querySelectorAll("th[data-sort]")];
  headers.forEach((th, column) => {
    th.classList.add("cursor-pointer", "select-none", "hover:text-slate-200");
    th.addEventListener("click", () => {
      const numeric = th.dataset.sort === "num";
      const ascending = th.dataset.dir !== "asc";
      headers.forEach((other) => {
        if (other !== th) {
          delete other.dataset.dir;
          other.querySelector("[data-arrow]")?.remove();
        }
      });
      th.dataset.dir = ascending ? "asc" : "desc";
      th.querySelector("[data-arrow]")?.remove();
      const arrow = document.createElement("span");
      arrow.dataset.arrow = "1";
      arrow.className = "ml-0.5 text-slate-500";
      arrow.textContent = ascending ? "▲" : "▼";
      th.appendChild(arrow);

      const body = table.querySelector("tbody");
      const rows = [...body.querySelectorAll("tr")].filter(
        (row) => row.children.length === headers.length
      );
      rows.sort((a, b) => {
        const cellA = a.children[column];
        const cellB = b.children[column];
        const rawA = cellA?.dataset.v ?? cellA?.textContent ?? "";
        const rawB = cellB?.dataset.v ?? cellB?.textContent ?? "";
        if (numeric) {
          const numA = Number.parseFloat(rawA);
          const numB = Number.parseFloat(rawB);
          const safeA = Number.isNaN(numA) ? -Infinity : numA;
          const safeB = Number.isNaN(numB) ? -Infinity : numB;
          return ascending ? safeA - safeB : safeB - safeA;
        }
        return ascending
          ? String(rawA).localeCompare(String(rawB), "fr")
          : String(rawB).localeCompare(String(rawA), "fr");
      });
      rows.forEach((row) => body.appendChild(row));
    });
  });
}

// --- Export CSV -------------------------------------------------------------

/**
 * Exporte une table HTML telle qu'elle est AFFICHÉE (en-têtes + lignes).
 *
 * Séparateur « ; » : c'est celui qu'attend Excel en configuration française
 * (une virgule y collerait tout dans une seule colonne). Le contenu est
 * échappé selon RFC 4180 (guillemets doublés), et un BOM UTF-8 est ajouté
 * pour qu'Excel n'abîme pas les accents.
 */
function exportTableCsv(table, filename) {
  if (!table) return;
  const lines = [];
  for (const row of table.querySelectorAll("tr")) {
    const cells = [...row.children].map((cell) => {
      const text = (cell.dataset.v ?? cell.textContent ?? "").trim().replace(/\s+/g, " ");
      return `"${text.replace(/"/g, '""')}"`;
    });
    if (cells.length) lines.push(cells.join(";"));
  }
  if (!lines.length) return;
  const blob = new Blob(["﻿" + lines.join("\r\n")], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

// --- Bandeau d'état du header ----------------------------------------------
// Le mode (PAPER/LIVE), l'état du broker et le kill-switch de stratégie
// n'étaient affichés que sur la page Live : on pouvait lancer un entraînement
// sans voir que le broker venait de tomber. Le bandeau est désormais servi
// par ce socle et donc identique sur les trois pages.

/**
 * @param {object} status  charge utile de /api/status
 * @param {?Function} onBrokerClick  si fourni, le badge broker devient un
 *   bouton (page Live : ouvre la fenêtre de connexion) ; sinon c'est un lien
 *   vers la page Live, seule page qui pilote la connexion.
 */
function renderStatusBadges(status, onBrokerClick) {
  const container = document.getElementById("header-status");
  if (!container || !status) return;
  const live = status.trading_mode === "live";
  const modePill = live ? "bg-amber-600 text-white" : "bg-sky-700 text-sky-100";
  const brokerDot = status.broker_connected ? "bg-emerald-400" : "bg-red-500";
  const brokerText = status.broker_connected ? "connecté" : "déconnecté";
  const brokerTone = status.broker_connected ? "text-emerald-400" : "text-red-400";
  const strategyColor = status.strategy_enabled ? "text-emerald-400" : "text-slate-500";
  // Badge DÉMO franc quand les données de marché ne sont pas réelles : le
  // graphique et les prix de la watchlist sont simulés — pas de tromperie.
  const demoBadge = status.market_data_live
    ? ""
    : `<span class="rounded bg-purple-700 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-purple-100"` +
      ` title="Données de marché simulées (aucun flux broker connecté)">démo</span>`;
  const brokerInner =
    `<span class="h-1.5 w-1.5 rounded-full ${brokerDot}"></span>${status.broker}` +
    `<span class="text-[10px] ${brokerTone}">${brokerText}</span>`;
  const brokerBadge = onBrokerClick
    ? `<button id="broker-badge" type="button" title="Connexion au broker"` +
      ` class="inline-flex items-center gap-1 rounded px-1 hover:bg-slate-700">${brokerInner}</button>`
    : `<a href="/" title="La connexion au broker se pilote depuis la page Live"` +
      ` class="inline-flex items-center gap-1 rounded px-1 hover:bg-slate-700">${brokerInner}</a>`;
  container.innerHTML =
    `<span class="inline-flex items-center gap-2">` +
    `<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${modePill}"` +
    ` title="Mode de trading (config broker.trading_mode)">${status.trading_mode}</span>` +
    brokerBadge +
    `<span class="text-slate-500">·</span>` +
    `<span class="${strategyColor}" title="Stratégie active — ${status.strategy_enabled
      ? "kill-switch ON" : "kill-switch OFF : aucune paire ne tradera"}">${status.strategy}</span>` +
    demoBadge +
    `</span>`;
  if (onBrokerClick) {
    document.getElementById("broker-badge").addEventListener("click", onBrokerClick);
  }
}

/** Charge /api/status et peint le bandeau. Renvoie le statut (ou null). */
async function loadHeaderStatus(onBrokerClick) {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) return null;
    const status = await response.json();
    renderStatusBadges(status, onBrokerClick);
    return status;
  } catch (err) {
    return null; // serveur injoignable : le bandeau garde son état précédent
  }
}

// --- Fenêtres modales -------------------------------------------------------
// Registre commun : Échap ferme la fenêtre ouverte, quelle que soit la page.

const overlays = [];

function registerOverlay(element, close) {
  overlays.push({ element, close });
}

// --- Horloge UTC ------------------------------------------------------------
// PyEA raisonne en UTC (repère de perte journalière, clôture de fin de semaine
// ISO, horodatage des trades). Afficher l'heure UTC évite à l'utilisateur de
// convertir de tête au moment où ça compte.

function startUtcClock() {
  const element = document.getElementById("utc-clock");
  if (!element) return;
  const tick = () => {
    const now = new Date();
    const hh = String(now.getUTCHours()).padStart(2, "0");
    const mm = String(now.getUTCMinutes()).padStart(2, "0");
    const ss = String(now.getUTCSeconds()).padStart(2, "0");
    element.textContent = `${hh}:${mm}:${ss} UTC`;
  };
  tick();
  setInterval(tick, 1000);
}

// --- Raccourcis clavier -----------------------------------------------------
// Volontairement SANS raccourci destructeur : rien qui arme une paire, lance
// un ordre ou annule un job. Uniquement de la navigation et de l'affichage.

const SHORTCUTS = [
  ["1", "Page Live"],
  ["2", "Page Backtest"],
  ["3", "Page Entraînement"],
  ["f", "Rechercher un symbole (page Live)"],
  ["Ctrl + Entrée", "Lancer le backtest / l'entraînement"],
  ["Échap", "Fermer la fenêtre ouverte"],
  ["?", "Afficher cette aide"],
];

function buildShortcutHelp() {
  const overlay = document.createElement("div");
  overlay.id = "shortcut-help";
  overlay.className =
    "fixed inset-0 z-50 hidden items-center justify-center bg-slate-950/70 p-4";
  const rows = SHORTCUTS.map(
    ([key, label]) => `
      <div class="flex items-center justify-between gap-6 py-1">
        <span class="text-slate-400">${label}</span>
        <kbd class="rounded border border-slate-600 bg-slate-900 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">${key}</kbd>
      </div>`
  ).join("");
  overlay.innerHTML = `
    <div class="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl">
      <div class="mb-3 flex items-center justify-between">
        <h3 class="text-sm font-semibold">Raccourcis clavier</h3>
        <button type="button" data-close class="text-slate-400 hover:text-slate-200" aria-label="Fermer">✕</button>
      </div>
      <div class="text-xs">${rows}</div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => {
    overlay.classList.add("hidden");
    overlay.classList.remove("flex");
  };
  overlay.querySelector("[data-close]").addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  registerOverlay(overlay, close);
  return overlay;
}

// Un raccourci ne doit JAMAIS se déclencher pendant une saisie.
function isTyping(target) {
  return (
    target &&
    (target.tagName === "INPUT" ||
      target.tagName === "SELECT" ||
      target.tagName === "TEXTAREA" ||
      target.isContentEditable)
  );
}

function initShortcuts() {
  const help = buildShortcutHelp();
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      for (const overlay of overlays) {
        if (!overlay.element.classList.contains("hidden")) overlay.close();
      }
      return;
    }
    // Ctrl+Entrée reste actif dans un champ : c'est le geste « valider ce
    // formulaire » attendu, et il n'est jamais destructeur.
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      const primary = document.querySelector("[data-primary-action]");
      if (primary && !primary.disabled) {
        event.preventDefault();
        primary.click();
      }
      return;
    }
    if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "?") {
      help.classList.toggle("hidden");
      help.classList.toggle("flex");
      return;
    }
    const pages = { 1: "/", 2: "/backtest", 3: "/training" };
    if (pages[event.key]) {
      window.location.href = pages[event.key];
      return;
    }
    if (event.key === "f") {
      const search = document.getElementById("symbol-search");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    }
  });
}

// --- Init -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  startUtcClock();
  initShortcuts();
});

window.PyEA = {
  prefs,
  formatPrice,
  num2,
  pct1,
  signed,
  pnlClass,
  formatDuration,
  formatUtcDate,
  formatUtcDateTime,
  shortStamp,
  fillSelect,
  rememberForm,
  apiErrorText,
  statCard,
  renderStatusBadges,
  loadHeaderStatus,
  makeSortable,
  exportTableCsv,
  registerOverlay,
};
