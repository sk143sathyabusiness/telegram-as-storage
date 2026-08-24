// frontend/api.js — core constants, shared state, fetch wrappers + toast/utils
// Extracted from app.js (91018 bytes) — keep behavior identical, just ES module exports.

export const API = "/api";

// Central shared state — mutable object so all modules see live updates.
// Mirrors app.js globals: currentUser, currentFolderId, currentFolderName, currentView, versionFileId, folderMap
export const state = {
  currentUser: null,
  currentFolderId: null,
  currentFolderName: "~",
  currentView: "files",
  versionFileId: null,
  folderMap: {},
};

// Also expose via window for inline onclick handlers and legacy code
if (typeof window !== "undefined") {
  window.API = API;
  window.state = state;
}

// ── TOAST ───────────────────────────────────────────────────────────────────
export function toast(msg, type = "ok") {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.className = `toast ${type} show`;
  clearTimeout(t._tid);
  t._tid = setTimeout(() => (t.className = "toast"), 2800);
}

// ── FORMAT HELPERS ──────────────────────────────────────────────────────────
export function fmt(bytes) {
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1e6) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1e9) return (bytes / 1e6).toFixed(1) + " MB";
  return (bytes / 1e9).toFixed(2) + " GB";
}

export function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtLogDate(ts) {
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtSpeed(bytesPerSec) {
  if (bytesPerSec < 1000) return bytesPerSec.toFixed(0) + " B/s";
  if (bytesPerSec < 1e6) return (bytesPerSec / 1000).toFixed(1) + " KB/s";
  return (bytesPerSec / 1e6).toFixed(1) + " MB/s";
}

export function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── MODAL UTILS ─────────────────────────────────────────────────────────────
let _previewBlobUrl = null;
export function getPreviewBlobUrl() { return _previewBlobUrl; }
export function setPreviewBlobUrl(v) { _previewBlobUrl = v; }

export function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = "flex";
  const box = el.querySelector(".modal");
  if (box) {
    box.style.animation = "none";
    void box.offsetWidth;
    box.style.animation = "modal-pop .38s cubic-bezier(.16,1,.3,1) both";
  }
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "backdrop-in .3s ease both";
}
export function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const box = el.querySelector(".modal");
  const finish = () => {
    el.style.display = "none";
    if (box) box.style.animation = "";
    el.style.animation = "";
    if (_previewBlobUrl) {
      URL.revokeObjectURL(_previewBlobUrl);
      _previewBlobUrl = null;
    }
  };
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !box) { finish(); return; }
  el.style.animation = "backdrop-out .25s ease both";
  box.style.animation = "modal-pop-out .25s ease both";
  setTimeout(finish, 240);
}
if (typeof document !== "undefined") {
  document.querySelectorAll(".modal-backdrop").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target === el) closeModal(el.id);
    });
  });
}

// ── VIEW SWITCHING ──────────────────────────────────────────────────────────
export function showView(name) {
  state.currentView = name;
  const views = ["files", "versions", "versions-all", "trash", "logs", "users", "backup"];
  views.forEach((v) => {
    const el = document.getElementById(`view-${v}`);
    if (el) {
      el.style.display = v === name ? "" : "none";
      if (v === name) {
        const panel = el.querySelector(".panel");
        if (panel) {
          panel.style.animation = "none";
          void panel.offsetWidth;
          panel.style.animation = "";
        }
      }
    }
  });
  const navs = ["files", "trash", "logs", "users", "versions-all", "backup"];
  navs.forEach((v) => {
    document.getElementById(`nav-${v}`)?.classList.toggle("active", v === name);
  });
  // Lazy-load per view — dynamic import to avoid circular dependency at load time
  if (name === "logs") import("./admin.js").then(m => m.loadLogs?.());
  if (name === "trash") import("./files.js").then(m => m.loadTrash?.());
  if (name === "users") import("./admin.js").then(m => m.loadUserManagement?.());
  if (name === "versions-all") import("./admin.js").then(m => m.loadAllVersions?.());
  if (name === "backup") import("./admin.js").then(m => m.loadBackups?.());
  const panel = document.getElementById(`view-${name}`);
  if (panel) revealOnScroll(panel);
}

// ── SCROLL REVEAL ───────────────────────────────────────────────────────────
export function revealOnScroll(container) {
  const items = container.querySelectorAll(".version-card, .trash-card, .fa-card, .um-stat-card, .log-row");
  if (!items.length) return;
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) { en.target.style.animationPlayState = "running"; io.unobserve(en.target); }
      });
    },
    { threshold: 0.05 }
  );
  items.forEach((it) => { it.style.animationPlayState = "paused"; io.observe(it); });
}

// ── RIPPLE ──────────────────────────────────────────────────────────────────
export function attachRipple() {
  document.querySelectorAll(".btn-primary, .btn-sm, .upload-btn, nav .btn-nav").forEach((btn) => {
    if (btn.dataset.ripple) return;
    btn.dataset.ripple = "1";
    btn.addEventListener("click", (e) => {
      const r = document.createElement("span");
      const rect = btn.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      r.style.cssText = `position:absolute;border-radius:50%;background:rgba(255,255,255,.5);pointer-events:none;width:${size}px;height:${size}px;left:${e.clientX - rect.left - size / 2}px;top:${e.clientY - rect.top - size / 2}px;transform:scale(0);animation:ripple .6s ease-out forwards;`;
      const prev = btn.style.position;
      if (getComputedStyle(btn).position === "static") btn.style.position = "relative";
      btn.style.overflow = "hidden";
      btn.appendChild(r);
      setTimeout(() => r.remove(), 600);
      if (prev) btn.style.position = prev;
    });
  });
}
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", attachRipple);
}

// ── PASSWORD TOGGLE ─────────────────────────────────────────────────────────
export function initPasswordToggles(root = document) {
  root.querySelectorAll(".pw-toggle[data-target]").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      if (input.type === "password") {
        input.type = "text";
        btn.textContent = "🙈";
        btn.setAttribute("aria-label", "Hide password");
      } else {
        input.type = "password";
        btn.textContent = "👁️";
        btn.setAttribute("aria-label", "Show password");
      }
    });
  });
}
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => initPasswordToggles());
}

// ── SKELETON ─────────────────────────────────────────────────────────────────
let _skeletonShown = false;
export function showSkeleton() {
  if (_skeletonShown) return;
  _skeletonShown = true;
  const tbody = document.getElementById("file-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  for (let i = 0; i < 5; i++) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><div class="skeleton-row"><div class="skeleton-cell wide"></div></div></td>
      <td><div class="skeleton-row"><div class="skeleton-cell narrow"></div></div></td>
      <td><div class="skeleton-row"><div class="skeleton-cell tiny"></div></div></td>
      <td><div class="skeleton-row"><div class="skeleton-cell narrow"></div></div></td>
      <td><div class="skeleton-row"><div class="skeleton-cell narrow"></div></div></td>
      <td></td>`;
    tbody.appendChild(tr);
  }
}
export function hideSkeleton() { _skeletonShown = false; }

// ── FETCH WRAPPERS (thin convenience) ───────────────────────────────────────
export async function fetchMe() {
  const r = await fetch(`${API}/me`);
  if (!r.ok) throw new Error("not authenticated");
  return r.json();
}
export async function fetchFolders() {
  const r = await fetch(`${API}/folders`);
  if (!r.ok) throw new Error("failed to load folders");
  return r.json();
}
export async function apiGet(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
  return r.json();
}
export async function apiPost(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) { const d = await r.json().catch(()=>({})); throw new Error(d.error || `POST ${path} failed`); }
  return r.json();
}

// Expose globals for legacy inline handlers (onclick="showView(...) etc")
if (typeof window !== "undefined") {
  window.toast = toast;
  window.fmt = fmt;
  window.fmtDate = fmtDate;
  window.fmtLogDate = fmtLogDate;
  window.fmtSpeed = fmtSpeed;
  window.escapeHtml = escapeHtml;
  window.openModal = openModal;
  window.closeModal = closeModal;
  window.showView = showView;
  window.revealOnScroll = revealOnScroll;
  window.attachRipple = attachRipple;
  window.initPasswordToggles = initPasswordToggles;
  window.showSkeleton = showSkeleton;
  window.hideSkeleton = hideSkeleton;
  window.fetchMe = fetchMe;
  window.API = API;
}
