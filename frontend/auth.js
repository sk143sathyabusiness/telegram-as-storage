// frontend/auth.js — login, sessionLogin, timeout countdown, fetch 401 intercept
import { API, state, toast, showView } from "./api.js";

let SESSION_TIMEOUT_MS = 43200 * 1000;
(function readSessionTimeout() {
  const meta = document.getElementById("session-timeout");
  if (meta && meta.content) SESSION_TIMEOUT_MS = parseInt(meta.content, 10) * 1000;
})();

let _sessionDeadline = 0;
let _sessionWarnTimer = null;
let _tabHidden = false;
let _hiddenRemaining = 0;
let _sessionModalShown = false;

document.addEventListener("visibilitychange", () => {
  _tabHidden = document.hidden;
  if (_tabHidden) {
    _hiddenRemaining = Math.max(0, _sessionDeadline - Date.now());
  } else if (_hiddenRemaining > 0) {
    _sessionDeadline = Date.now() + _hiddenRemaining;
    _hiddenRemaining = 0;
  }
});

export function startSessionTimer() {
  _sessionDeadline = Date.now() + SESSION_TIMEOUT_MS;
  if (_sessionWarnTimer) clearInterval(_sessionWarnTimer);
  const el = document.getElementById("session-timer");
  _sessionWarnTimer = setInterval(() => {
    if (_sessionModalShown) { if (el) el.textContent = ""; return; }
    if (_tabHidden) return;
    const remain = _sessionDeadline - Date.now();
    if (remain <= 0) {
      if (el) el.textContent = "";
      if (!_sessionModalShown) {
        _sessionModalShown = true;
        const { openModal } = awaitImportModal();
        openModal("session-modal");
        const note = document.getElementById("se-note");
        if (note) note.textContent = "Your session was idle too long and has expired.";
        const u = document.getElementById("se-user"), p = document.getElementById("se-pass");
        if (state.currentUser?.username) { u.value = state.currentUser.username; p.focus(); } else u.focus();
      }
      return;
    }
    const m = Math.floor(remain / 60000);
    const s = Math.floor((remain % 60000) / 1000);
    if (el) {
      el.textContent = `⏱ ${m}:${String(s).padStart(2, "0")}`;
      el.style.color = remain < 60000 ? "var(--danger)" : "var(--muted)";
    }
  }, 1000);
}

function awaitImportModal() {
  // Synchronous fallback: api.js openModal already globally available at runtime
  // Import lazily via window to avoid circular dep
  return { openModal: window.openModal || ((id)=>{ const e=document.getElementById(id); if(e) e.style.display="flex"; }) };
}

// Any interaction resets the local countdown
["click", "keydown", "mousemove", "scroll", "touchstart"].forEach(ev =>
  window.addEventListener(ev, () => {
    if (state.currentUser && !_sessionModalShown && !_tabHidden) _sessionDeadline = Date.now() + SESSION_TIMEOUT_MS;
  }, { passive: true })
);

// ── FETCH 401 intercept — also ensure cookies are sent for same-origin API calls
const _origFetch = window.fetch.bind(window);
window.fetch = async function(url, opts) {
  if (typeof url === "string" && url.includes("/api/")) {
    opts = {...(opts||{}), credentials: opts?.credentials || "same-origin"};
  }
  const res = await _origFetch(url, opts);
  if (res.status === 401 && !_sessionModalShown && !String(url).includes("/api/login") && !String(url).includes("/api/me")) {
    let expired = false;
    try { const j = await res.clone().json(); if (j && j.session_expired) expired = true; } catch {}
    _sessionModalShown = true;
    const modalFn = window.openModal || awaitImportModal().openModal;
    modalFn("session-modal");
    const u = document.getElementById("se-user");
    const p = document.getElementById("se-pass");
    const note = document.getElementById("se-note");
    if (note) note.textContent = expired ? "Your session was idle too long and has expired." : "Your session ended. Please sign in again to continue.";
    if (state.currentUser?.username) { u.value = state.currentUser.username; p.focus(); }
    else u.focus();
  }
  return res;
};

// ── AUTH ────────────────────────────────────────────────────────────────────
export async function doLogin() {
  const username = document.getElementById("l-user").value.trim();
  const password = document.getElementById("l-pass").value;
  const r = await fetch(API + "/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    credentials: "same-origin",
    body: JSON.stringify({username, password})
  });
  if (r.ok) {
    state.currentUser = await r.json();
    // Sync window.currentUser for legacy code that reads global
    window.currentUser = state.currentUser;
    if (state.currentUser.org_id) localStorage.setItem("tv_org_id", state.currentUser.org_id);
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("app").classList.add("visible");
    document.getElementById("topbar-user").textContent = state.currentUser.username;
    document.getElementById("topbar-role").textContent = state.currentUser.role;
    if (state.currentUser.role !== "org_admin" && state.currentUser.role !== "master_admin") {
      document.getElementById("nav-trash").style.display = "none";
      document.getElementById("nav-logs").style.display  = "none";
      document.getElementById("nav-users").style.display = "none";
      document.getElementById("nav-versions-all").style.display = "none";
      document.getElementById("nav-backup").style.display = "none";
    }
    const orgsNav = document.getElementById("nav-orgs");
    if (orgsNav) orgsNav.style.display = state.currentUser.role === "master_admin" ? "" : "none";
    const folders = await import("./folders.js");
    await folders.loadFolders();
    const files = await import("./files.js");
    files.refreshFiles();
    startSessionTimer();
  } else {
    let msg = "Invalid credentials";
    try { const d = await r.json(); if (d && d.error) msg = d.error; } catch {}
    if (r.status >= 500) msg = "Server error — check backend configuration (Supabase env vars?).";
    document.getElementById("login-err").textContent = msg;
    document.getElementById("login-err").classList.remove("shake");
    void document.getElementById("login-err").offsetWidth;
    document.getElementById("login-err").classList.add("shake");
  }
}

export async function logout() {
  try { await fetch(API + "/logout", {method: "POST", credentials: "same-origin"}); } catch {}
  // Clear all client state without relying on reload cache
  state.currentUser = null;
  window.currentUser = null;
  localStorage.removeItem("tv_org_id");
  _sessionModalShown = false;
  if (_sessionWarnTimer) { clearInterval(_sessionWarnTimer); _sessionWarnTimer = null; }
  const el = document.getElementById("session-timer");
  if (el) el.textContent = "";
  document.getElementById("app")?.classList.remove("visible");
  const loginScreen = document.getElementById("login-screen");
  if (loginScreen) loginScreen.style.display = "";
  document.getElementById("l-user").value = "";
  document.getElementById("l-pass").value = "";
  document.getElementById("topbar-user").textContent = "";
  document.getElementById("topbar-role").textContent = "";
  // Reset folder cache so next login reloads fresh
  state.currentFolderId = null;
  state.currentFolderName = "~";
  state.folderMap = {};
  // Use hard reload that bypasses cache as fallback, but already showing login
  // location.reload() sometimes restores bfcache with old DOM — force navigation
  window.location.hash = "";
  // Small delay then reload to ensure cookie deletion propagated
  setTimeout(() => location.reload(), 150);
}

export async function sessionLogin() {
  const username = document.getElementById("se-user").value.trim();
  const password = document.getElementById("se-pass").value;
  const errEl = document.getElementById("se-err");
  errEl.textContent = "";
  const r = await fetch(API + "/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    credentials: "same-origin",
    body: JSON.stringify({username, password})
  });
  if (r.ok) {
    state.currentUser = await r.json();
    window.currentUser = state.currentUser;
    if (state.currentUser.org_id) localStorage.setItem("tv_org_id", state.currentUser.org_id);
    _sessionModalShown = false;
    window.closeModal("session-modal");
    toast("Welcome back — session restored");
    const folders = await import("./folders.js");
    await folders.loadFolders();
    const files = await import("./files.js");
    files.refreshFiles();
    startSessionTimer();
  } else {
    errEl.textContent = "Invalid credentials. Try again.";
  }
}

// Keyboard Enter on login screen
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && document.getElementById("login-screen")?.style.display !== "none") {
    doLogin();
  }
});

// Auto-login via /me on page load (mirrors app.js bottom fetch)
fetch(API + "/me", {credentials: "same-origin"}).then(async r => {
  if (r.ok) {
    state.currentUser = await r.json();
    window.currentUser = state.currentUser;
    if (state.currentUser.org_id) localStorage.setItem("tv_org_id", state.currentUser.org_id);
    const loginScreen = document.getElementById("login-screen");
    if (loginScreen) loginScreen.style.display = "none";
    document.getElementById("app")?.classList.add("visible");
    const tu = document.getElementById("topbar-user");
    if (tu) tu.textContent = state.currentUser.username;
    const tr = document.getElementById("topbar-role");
    if (tr) tr.textContent = state.currentUser.role;
    if (state.currentUser.role !== "org_admin" && state.currentUser.role !== "master_admin") {
      document.getElementById("nav-trash").style.display = "none";
      document.getElementById("nav-logs").style.display  = "none";
      document.getElementById("nav-users").style.display = "none";
      document.getElementById("nav-versions-all").style.display = "none";
      document.getElementById("nav-backup").style.display = "none";
    }
    const orgsNav = document.getElementById("nav-orgs");
    if (orgsNav) orgsNav.style.display = state.currentUser.role === "master_admin" ? "" : "none";
    const folders = await import("./folders.js");
    await folders.loadFolders();
    const files = await import("./files.js");
    files.refreshFiles();
    // update trash count lazily
    try { files.updateTrashCount?.(); } catch {}
    startSessionTimer();
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("registered") === "1") {
    const el = document.getElementById("login-success");
    if (el) el.textContent = "✓ Registration successful! You can now log in with the username and password you chose.";
  }
});

// Expose for inline handlers and cross-module legacy
if (typeof window !== "undefined") {
  window.doLogin = doLogin;
  window.logout = logout;
  window.sessionLogin = sessionLogin;
  window.startSessionTimer = startSessionTimer;
  window.currentUser = state.currentUser;
}
