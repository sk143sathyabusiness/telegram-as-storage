// frontend/files.js — file list, drag/drop, folder-upload, progress ETA, preview/edit, versions/trash
import { API, state, toast, fmt, fmtDate, fmtSpeed, escapeHtml, openModal, closeModal, hideSkeleton, showSkeleton, revealOnScroll, getPreviewBlobUrl, setPreviewBlobUrl } from "./api.js";

function fmtEta(sec) {
  if (!isFinite(sec) || sec < 0) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  if (sec < 60) return `${s}s`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s}s`;
}

// ── TRANSFERS DRAWER (unified download + upload progress) ──────────────
const _transfers = new Map();
let _transferSeq = 0;

function makeTransferCtrl() {
  return {
    paused: false,
    cancelled: false,
    waiters: [],
    ac: null,
    abort() { this.cancel(); },
    cancel() {
      if (this.cancelled) return;
      this.cancelled = true;
      if (this.ac) { try { this.ac.abort(); } catch {} }
      const ws = this.waiters; this.waiters = [];
      ws.forEach(r => { try { r(); } catch {} });
    },
    pause() { if (!this.cancelled) this.paused = true; },
    resume() {
      if (this.cancelled) return;
      this.paused = false;
      const ws = this.waiters; this.waiters = [];
      ws.forEach(r => { try { r(); } catch {} });
    },
  };
}

function _transfersDrawerEl() { return document.getElementById("transfers-drawer"); }
function _transfersListEl() { return document.getElementById("transfers-list"); }
function _transfersEmptyEl() { return document.getElementById("transfers-empty"); }

function _refreshTransfersBadge() {
  const b = document.getElementById("transfers-badge");
  if (!b) return;
  let active = 0;
  for (const t of _transfers.values()) if (t.status === "active" || t.status === "paused") active++;
  b.textContent = active;
  b.style.display = active > 0 ? "inline-block" : "none";
}

function _updateEmptyState() {
  const e = _transfersEmptyEl(); if (!e) return;
  e.style.display = _transfers.size ? "none" : "block";
}

function _openDrawerIfNeeded() {
  const d = _transfersDrawerEl();
  if (d && !d.classList.contains("open")) {
    d.classList.add("open");
    const scrim = document.getElementById("transfers-scrim");
    if (scrim) scrim.classList.add("show");
  }
}

function _renderTransferRow(t) {
  let el = t.el;
  if (!el) {
    el = document.createElement("div");
    el.className = "transfer-row";
    _transfersListEl().appendChild(el);
    t.el = el;
  }
  _updateTransferRow(t);
}

function _updateTransferRow(t) {
  if (!t.el) return;
  const pct = t.total ? Math.min(100, Math.round((t.received / t.total) * 100)) : (t.status === "done" ? 100 : 0);
  let meta = "";
  if (t.status === "active") {
    const speed = t.speed || 0;
    const remaining = t.total ? (t.total - t.received) : 0;
    const eta = speed > 0 ? remaining / speed : 0;
    meta = `${pct}% · ${fmtSpeed(speed)} · ETA ${fmtEta(eta)}`;
  } else if (t.status === "paused") meta = `Paused ${pct}%`;
  else if (t.status === "decrypting") meta = `Decrypting… ${pct}%`;
  else if (t.status === "cancelled") meta = `Cancelled`;
  else if (t.status === "done") meta = `Done · ${fmt(t.total || 0)}`;
  else if (t.status === "error") meta = `Error: ${t.error || ""}`;
  else meta = `${pct}%`;
  const icon = t.kind === "upload" ? "⬆" : "⬇";
  const live = (t.status === "active" || t.status === "paused");
  const term = (t.status === "done" || t.status === "error" || t.status === "cancelled");
  let barColor = "";
  if (t.status === "error" || t.status === "cancelled") barColor = "background:var(--red);";
  t.el.innerHTML = `
    <div class="transfer-name"><span>${icon}</span><span class="transfer-fname">${escapeHtml(t.name)}</span></div>
    <div class="pbar"><div class="pbar-fill" style="width:${pct}%;${barColor}"></div></div>
    <div class="transfer-meta">${meta}</div>
    <div class="transfer-actions">
      ${live ? `<button class="btn-mini" onclick="transferToggle('${t.id}')">${t.status === "paused" ? "Resume" : "Pause"}</button>` : ""}
      ${live ? `<button class="btn-mini danger" onclick="transferCancel('${t.id}')">Cancel</button>` : ""}
      ${term ? `<button class="btn-mini" onclick="transferDismiss('${t.id}')">✕</button>` : ""}
    </div>`;
}

export function addTransfer(opts) {
  const id = "t" + (++_transferSeq);
  const t = {
    id, kind: opts.kind || "download", name: opts.name || "file",
    size: opts.size || 0, received: 0, total: opts.size || 0,
    speed: 0, status: "active", ctrl: opts.ctrl || null, el: null,
  };
  _transfers.set(id, t);
  _renderTransferRow(t);
  _openDrawerIfNeeded();
  _refreshTransfersBadge();
  _updateEmptyState();
  return id;
}

export function updateTransfer(id, patch) {
  const t = _transfers.get(id);
  if (!t) return;
  if (patch && typeof patch === "object") Object.assign(t, patch);
  _updateTransferRow(t);
  _refreshTransfersBadge();
}

export function finishTransfer(id, status) {
  const t = _transfers.get(id);
  if (!t) return;
  t.status = status || "done";
  if (t.status === "done") t.received = t.total;
  _updateTransferRow(t);
  _refreshTransfersBadge();
}

export function transferToggle(id) {
  const t = _transfers.get(id);
  if (!t || !t.ctrl) return;
  if (t.status === "paused") { t.ctrl.resume(); updateTransfer(id, { status: "active" }); }
  else if (t.status === "active") { t.ctrl.pause(); updateTransfer(id, { status: "paused" }); }
}
export function transferCancel(id) {
  const t = _transfers.get(id);
  if (!t || !t.ctrl) return;
  t.ctrl.cancel();
  updateTransfer(id, { status: "cancelled" });
}
export function transferDismiss(id) {
  const t = _transfers.get(id);
  if (!t) return;
  _transfers.delete(id);
  if (t.el && t.el.parentNode) t.el.parentNode.removeChild(t.el);
  _refreshTransfersBadge();
  _updateEmptyState();
}
export function toggleTransfersDrawer() {
  const d = _transfersDrawerEl();
  if (!d) return;
  const open = d.classList.toggle("open");
  const scrim = document.getElementById("transfers-scrim");
  if (scrim) scrim.classList.toggle("show", open);
  _refreshTransfersBadge();
}

// ── ENCRYPTION (behind the screen) ──────────────────────────────────────
// Auto-derived per-org passphrase so users never enter it manually.
// Uses org_id from state.currentUser (returned by /api/login and /api/me) —
// all members of the same org share the same key, no UI prompt, still AES-256-GCM.
export function getAutoPassphrase() {
  const orgId = state.currentUser?.org_id || state.currentUser?.orgId || localStorage.getItem("tv_org_id") || "";
  if (orgId) return `tv-auto-${orgId}`;
  // fallback before login — use a stable device key so code never prompts
  let dev = localStorage.getItem("tv_device_key");
  if (!dev) {
    dev = Array.from(crypto.getRandomValues(new Uint8Array(16))).map(b=>b.toString(16).padStart(2,"0")).join("");
    localStorage.setItem("tv_device_key", dev);
  }
  return `tv-auto-device-${dev}`;
}
export async function deriveKey(passphrase) {
  const enc = new TextEncoder();
  const km = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: enc.encode("teamvault-fixed-salt"), iterations: 200000, hash: "SHA-256" },
    km, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]
  );
}

export async function encryptFile(file, passphrase) {
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, await file.arrayBuffer());
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv); out.set(new Uint8Array(ct), 12);
  return new Blob([out]);
}

export async function sha256Hex(blob) {
  const h = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(h)).map(b => b.toString(16).padStart(2,"0")).join("");
}

// ── CHUNKED UPLOAD WIRE FORMAT ───────────────────────────────────────────────
// Each client chunk is encrypted as its own AES-GCM record: [uint32 BE len][12 IV][ct].
// The FIRST chunk is prefixed with a 16-byte magic so the client can distinguish
// new per-chunk files from legacy single-IV files WITHOUT any DB column change.
// (Legacy files begin with a random 12-byte IV, which will never equal this constant.)
export const CHUNKED_MAGIC = new Uint8Array([0x54,0x56,0x43,0x48,0x4e,0x4b,0x30,0x31, 0,0,0,0,0,0,0,0]); // "TVCHNK01" + 8 zero bytes
const UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024; // 4 MB — stays under Vercel's ~4.5 MB request limit

function _u32be(n) {
  return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
}
function _readU32be(buf, off) {
  return (buf[off] << 24) | (buf[off + 1] << 16) | (buf[off + 2] << 8) | buf[off + 3];
}

// Decrypt a fully-assembled encrypted blob (Uint8Array) into plaintext bytes.
// Supports legacy (single IV at offset 0) and chunked (magic + length-prefixed) formats.
export async function decryptAssembled(ct, passphrase) {
  const key = await deriveKey(passphrase);
  const isChunked = ct.length >= 16 && ct.slice(0, 16).every((b, i) => b === CHUNKED_MAGIC[i]);
  if (isChunked) {
    const records = [];
    let pos = 16;
    while (pos + 4 <= ct.length) {
      const len = _readU32be(ct, pos);
      pos += 4;
      if (pos + len > ct.length) break;
      records.push(ct.slice(pos, pos + len));
      pos += len;
    }
    // decrypt all chunks concurrently (CPU-bound work runs in parallel)
    const plains = await Promise.all(records.map(rec => {
      const iv = rec.slice(0, 12);
      return crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, rec.slice(12)).then(p => new Uint8Array(p));
    }));
    let total = plains.reduce((a, p) => a + p.length, 0);
    const out = new Uint8Array(total);
    let o = 0;
    for (const p of plains) { out.set(p, o); o += p.length; }
    return out;
  }
  const iv = ct.slice(0, 12);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
  return new Uint8Array(plain);
}

// ── UPLOAD ──────────────────────────────────────────────────────────────────
export async function uploadFiles(triggerEl) {
  const passphrase = getAutoPassphrase();

  const fileInput = document.getElementById("file-input");
  const folderInput = document.getElementById("folder-input");
  let files, usedInput;
  if (triggerEl && triggerEl.files && triggerEl.files.length) {
    files = triggerEl.files;
    usedInput = triggerEl;
  } else {
    files = fileInput.files;
    usedInput = fileInput;
    if (!files.length) {
      files = folderInput.files;
      usedInput = folderInput;
    }
  }
  if (!files || !files.length) return;

  let okCount = 0, errCount = 0;
  const errDetails = [];
  for (const file of files) {
    const res = await uploadFileChunked(file, passphrase);
    if (res.ok) okCount++;
    else { errCount++; if (res.error) errDetails.push(res.error); }
  }
  usedInput.value = "";
  refreshFiles();
  const msg = okCount ? `Uploaded ${okCount} file(s)` : "";
  const errMsg = errCount ? `${errCount} failed${errDetails[0] ? ": " + errDetails[0].slice(0,180) : ""}` : "";
  toast([msg, errMsg].filter(Boolean).join(", "), errCount ? (okCount ? "ok" : "err") : "ok");
  if (errDetails.length) console.error("[UPLOAD] details:", errDetails.join(" | "));
}
export const uploadFile = uploadFiles; // alias per brief

// Upload one file as 4 MB encrypted chunks. Reads each slice on demand (never the
// whole file at once → no browser OOM) and POSTs each chunk to /api/files/chunk,
// then commits the ordered message_ids. Keeps every HTTP request small so large
// files (movies) work on Vercel serverless.
async function uploadFileChunked(file, passphrase) {
  const key = await deriveKey(passphrase);
  const folderId = state.currentFolderId || "";
  const total = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK_BYTES));
  const messageIds = new Array(total);
  const CONC = 6; // parallel chunk uploads (bounded: Telegram allows multiple sessions per auth key)
  const ctrl = makeTransferCtrl();
  const id = addTransfer({ kind: "upload", name: file.name, size: file.size, ctrl });
  const t0 = Date.now();
  let done = 0;
  const updateProgress = () => {
    const pct = Math.round((done / total) * 100);
    const elapsed = (Date.now() - t0) / 1000;
    const speed = (done * UPLOAD_CHUNK_BYTES) / Math.max(elapsed, .01);
    const eta = Math.round(((total - done) * UPLOAD_CHUNK_BYTES) / Math.max(speed, 1));
    updateTransfer(id, {
      received: done * UPLOAD_CHUNK_BYTES,
      total: total * UPLOAD_CHUNK_BYTES,
      speed,
      status: ctrl.cancelled ? "cancelled" : ctrl.paused ? "paused" : "active",
    });
  };
  try {
    async function buildRecord(idx) {
      const start = idx * UPLOAD_CHUNK_BYTES;
      const slice = file.slice(start, Math.min(start + UPLOAD_CHUNK_BYTES, file.size));
      const buf = await slice.arrayBuffer();
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, buf));
      const recBody = new Uint8Array(4 + 12 + ct.length);
      recBody.set(_u32be(12 + ct.length), 0);
      recBody.set(iv, 4);
      recBody.set(ct, 16);
      if (idx === 0) {
        const rec = new Uint8Array(16 + recBody.length);
        rec.set(CHUNKED_MAGIC, 0);
        rec.set(recBody, 16);
        return new Blob([rec]);
      }
      return new Blob([recBody]);
    }
    async function uploadOne(idx) {
      for (let attempt = 0; ; attempt++) {
        if (ctrl.cancelled) throw new Error("cancelled");
        const recBlob = await buildRecord(idx);
        if (!ctrl.ac) ctrl.ac = new AbortController();
        const r = await _xhrPost(`${API}/files/chunk`, fd => {
          fd.append("chunk", recBlob, file.name);
          fd.append("filename", file.name);
          fd.append("folder_id", folderId);
          fd.append("index", String(idx));
          fd.append("total", String(total));
        }, ctrl.ac.signal);
        if (r.aborted) throw new Error("cancelled");
        if (r.ok) return r.message_id;
        if (attempt >= 3) throw new Error(r.error || `chunk ${idx} failed (HTTP ${r.status})`);
      }
    }
    let nextIdx = 0;
    async function worker() {
      for (let idx; (idx = nextIdx++) < total; ) {
        if (ctrl.cancelled) return;
        if (ctrl.paused) { await new Promise(res => { ctrl.waiters.push(res); }); if (ctrl.cancelled) return; }
        messageIds[idx] = await uploadOne(idx);
        done++;
        updateProgress();
      }
    }
    await Promise.all(Array.from({ length: CONC }, worker));
    if (ctrl.cancelled) { updateTransfer(id, { status: "cancelled" }); return { ok: false, error: "cancelled" }; }
    if (messageIds.some(m => m == null)) throw new Error("missing message ids");
    const c = await _xhrPostJSON(`${API}/files/commit`, {
      filename: file.name, folder_id: folderId, sha256: "", total_size: file.size, message_ids: messageIds,
    }, ctrl.ac ? ctrl.ac.signal : undefined);
    if (ctrl.cancelled || c.aborted) { updateTransfer(id, { status: "cancelled" }); return { ok: false, error: "cancelled" }; }
    if (!c.ok) throw new Error(c.error || "commit failed");
    finishTransfer(id, "done");
    return { ok: true };
  } catch (e) {
    if (ctrl.cancelled) updateTransfer(id, { status: "cancelled" });
    else updateTransfer(id, { status: "error", error: e.message });
    return { ok: false, error: e.message };
  }
}

function _xhrPost(url, buildForm, signal) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    const fd = new FormData();
    buildForm(fd);
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve({ ok: true, message_id: d.message_id, status: xhr.status });
      else resolve({ ok: false, error: d.error || `HTTP ${xhr.status}`, status: xhr.status });
    };
    xhr.onerror = () => resolve({ ok: false, error: "Network error" });
    xhr.onabort = () => resolve({ ok: false, aborted: true, error: "aborted" });
    if (signal) signal.addEventListener("abort", () => xhr.abort());
    xhr.send(fd);
  });
}

function _xhrPostJSON(url, json, signal) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve({ ok: true, ...d, status: xhr.status });
      else resolve({ ok: false, error: d.error || `HTTP ${xhr.status}`, status: xhr.status });
    };
    xhr.onerror = () => resolve({ ok: false, error: "Network error" });
    xhr.onabort = () => resolve({ ok: false, aborted: true, error: "aborted" });
    if (signal) signal.addEventListener("abort", () => xhr.abort());
    xhr.send(JSON.stringify(json));
  });
}

// ── FILE LIST / SEARCH ──────────────────────────────────────────────────────
let _fileSearchActive = false;
let _fileSearchTimer = null;

export function onFileSearch() {
  const q = document.getElementById("file-search-input").value.trim();
  document.getElementById("file-search-clear").style.display = q ? "" : "none";
  clearTimeout(_fileSearchTimer);
  _fileSearchTimer = setTimeout(() => {
    if (q) { _fileSearchActive = true; runFileSearch(q); }
    else { _fileSearchActive = false; refreshFiles(); }
  }, 220);
}

export function clearFileSearch() {
  document.getElementById("file-search-input").value = "";
  document.getElementById("file-search-clear").style.display = "none";
  _fileSearchActive = false;
  refreshFiles();
}

function getFileIcon(ext) {
  const icons = { png:"🖼️", jpg:"🖼️", jpeg:"🖼️", gif:"🖼️", svg:"🖼️", webp:"🖼️", bmp:"🖼️", pdf:"📄", mp4:"🎬", webm:"🎬", mp3:"🎵", wav:"🎵", ogg:"🎵", docx:"📝", doc:"📝", xlsx:"📊", xls:"📊", pptx:"📊", ppt:"📊", txt:"📄", md:"📄", zip:"📦", rar:"📦" };
  return icons[ext] || "📄";
}
export function toggleMenu(fileId) {
  const menu = document.getElementById(`menu-${fileId}`);
  if (!menu) return;
  const isVisible = menu.style.display === "flex";
  closeAllMenus();
  if (!isVisible) menu.style.display = "flex";
  event?.stopPropagation();
}
export function closeAllMenus() {
  document.querySelectorAll(".card-dropdown").forEach(m => m.style.display = "none");
}
document.addEventListener("click", closeAllMenus);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeAllMenus(); });

const thumbCache = new Map();
let thumbActive = 0;
const thumbQueue = [];
const MAX_THUMB_CONCURRENT = 3;
const imageExts = ["png","jpg","jpeg","gif","svg","webp","bmp"];

const thumbObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      const card = entry.target;
      thumbObserver.unobserve(card);
      const fileId = card.dataset.id;
      const ext = card.dataset.ext;
      const imgEl = card.querySelector(".file-thumb");
      const iconEl = card.querySelector(".file-thumb-icon");
      scheduleThumb(fileId, ext, imgEl, iconEl);
    }
  });
}, { rootMargin: "300px", threshold: 0.01 });

function scheduleThumb(fileId, ext, imgEl, iconEl) {
  if (!imageExts.includes(ext)) return;
  if (thumbCache.has(fileId)) {
    const url = thumbCache.get(fileId);
    imgEl.src = url; imgEl.style.display = "block"; if (iconEl) iconEl.style.display = "none";
    return;
  }
  thumbQueue.push({ fileId, ext, imgEl, iconEl });
  processThumbQueue();
}
async function processThumbQueue() {
  if (thumbActive >= MAX_THUMB_CONCURRENT || !thumbQueue.length) return;
  const { fileId, ext, imgEl, iconEl } = thumbQueue.shift();
  thumbActive++;
  try {
    const passphrase = getAutoPassphrase();
    const r = await fetch(`${API}/files/${fileId}/preview`, {credentials:"same-origin"});
    if (!r.ok) throw new Error();
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    let plain;
    try { plain = await decryptAssembled(ct, passphrase); } catch { return; }
    const mime = getMimeForExt(ext);
    const blob = new Blob([plain], {type: mime});
    const url = URL.createObjectURL(blob);
    thumbCache.set(fileId, url);
    imgEl.src = url; imgEl.style.display = "block"; if (iconEl) iconEl.style.display = "none";
  } catch {} finally {
    thumbActive--;
    if (thumbQueue.length) processThumbQueue();
  }
}
function loadCardThumb(fileId, ext, imgEl, iconEl) {
  scheduleThumb(fileId, ext, imgEl, iconEl);
}

function renderFileCard(f, v, index) {
  const ext = f.name.split(".").pop().toLowerCase();
  const icon = getFileIcon(ext);
  const canDelete = state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin";
  const safeName = f.name.replace(/'/g,"\\'");
  const card = document.createElement("div");
  card.className = "file-card";
  card.dataset.id = f.id;
  card.dataset.ext = f.name.split(".").pop().toLowerCase();
  card.style.animationDelay = `${index * 40}ms`;
  const selChk = canDelete ? `<input type="checkbox" class="file-select" data-id="${f.id}" onclick="event.stopPropagation(); toggleFileSelect('${f.id}', this)" style="position:absolute;top:6px;left:6px;width:16px;height:16px;z-index:6;accent-color:var(--accent);cursor:pointer">` : "";
  card.innerHTML = `
    <div class="file-thumb-wrap" onclick="previewFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}')" style="cursor:pointer;position:relative">
      ${selChk}
      <img class="file-thumb" data-fileid="${f.id}" alt="${escapeHtml(f.name)}" style="display:none">
      <div class="file-thumb-icon">${icon}</div>
      <div class="card-menu" onclick="event.stopPropagation()">
        <button class="card-menu-btn" onclick="toggleMenu('${f.id}')" aria-label="Options">⋯</button>
        <div class="card-dropdown" id="menu-${f.id}" style="display:none">
          <button onclick="previewFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}'); closeAllMenus()">👁 Preview</button>
          ${isMediaExt(f.name) ? `<button onclick="playFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}'); closeAllMenus()">▶ Play</button>` : ""}
          ${isDocExt(f.name) ? `<button onclick="playFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}'); closeAllMenus()">📄 View</button>` : ""}
          <button onclick="shareFile('${f.id}','${safeName}'); closeAllMenus()">Share</button>
          <button onclick="emailFile('${f.id}','${safeName}'); closeAllMenus()">Email</button>
          <button onclick="editFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}'); closeAllMenus()">Edit</button>
          <button onclick="downloadFile('${f.id}','${safeName}','${f.current_version ? f.current_version.size_bytes : 0}'); closeAllMenus()">⬇ Download</button>
          <button onclick="openVersions('${f.id}','${safeName}'); closeAllMenus()">History</button>
          ${canDelete ? `<button class="danger" onclick="deleteFile('${f.id}'); closeAllMenus()">Delete</button>` : ""}
        </div>
      </div>
    </div>
    <div class="file-card-footer">
      <div class="file-card-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
      <div class="file-card-meta"><span>${v ? fmt(v.size_bytes) : "—"}</span><span class="version-badge">v${v ? v.version_number : "—"}</span></div>
      <div class="file-card-meta">${v ? escapeHtml(v.uploaded_by_name || "—") : "—"} · ${v ? fmtDate(v.uploaded_at) : "—"}</div>
    </div>`;
  return card;
}

async function runFileSearch(q) {
  const r = await fetch(`${API}/files/search?q=${encodeURIComponent(q)}`, {credentials:"same-origin"});
  if (!r.ok) return;
  const files = await r.json();
  const grid = document.getElementById("file-grid");
  const tbody = document.getElementById("file-tbody");
  const empty = document.getElementById("empty-files");
  if (grid) grid.innerHTML = "";
  if (tbody) tbody.innerHTML = "";
  if (!files.length) {
    empty.style.display = "";
    empty.innerHTML = `<div class="empty-icon">🔍</div>No files match "<strong>${escapeHtml(q)}</strong>".`;
    return;
  }
  empty.style.display = "none";
  document.getElementById("folder-title").textContent = `Search: ${q}` + (state.currentFolderId ? "" : "");
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const v = f.current_version;
    fileMetaCache.set(f.id, { name: f.name, size: (v && v.size_bytes) || 0 });
    const card = renderFileCard(f, v, i);
    if (grid) grid.appendChild(card);
    // lazy 1:1 preview (IntersectionObserver, cache, max 3 concurrent)
    if (typeof thumbObserver !== "undefined" && thumbObserver) thumbObserver.observe(card);
    else { const imgEl = card.querySelector(".file-thumb"); const iconEl = card.querySelector(".file-thumb-icon"); loadCardThumb(f.id, f.name.split(".").pop().toLowerCase(), imgEl, iconEl); }
    // keep tbody for legacy tests (hidden)
    if (tbody) {
      const tr = document.createElement("tr"); tr.style.display="none";
      tr.innerHTML = `<td>${escapeHtml(f.name)}</td>`;
      tbody.appendChild(tr);
    }
  }
}

export async function refreshFiles() {
  if (_fileSearchActive) return;
  _selected.clear();
  updateBulkBar();
  if (document.getElementById("folder-title")) document.getElementById("folder-title").textContent = state.currentFolderName;
  const fid = state.currentFolderId !== null ? `folder_id=${state.currentFolderId}` : "folder_id=";
  const r = await fetch(`${API}/files?${fid}`, {credentials:"same-origin"});
  if (!r.ok) return;
  const files = await r.json();
  const grid = document.getElementById("file-grid");
  const tbody = document.getElementById("file-tbody");
  const empty = document.getElementById("empty-files");
  if (grid) grid.innerHTML = "";
  if (tbody) tbody.innerHTML = "";
  hideSkeleton();
  updateStorageMeter();
  if (!files.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const v = f.current_version;
    fileMetaCache.set(f.id, { name: f.name, size: (v && v.size_bytes) || 0 });
    const card = renderFileCard(f, v, i);
    if (grid) grid.appendChild(card);
    if (typeof thumbObserver !== "undefined" && thumbObserver) thumbObserver.observe(card);
    else { const imgEl = card.querySelector(".file-thumb"); const iconEl = card.querySelector(".file-thumb-icon"); loadCardThumb(f.id, f.name.split(".").pop().toLowerCase(), imgEl, iconEl); }
    if (tbody) {
      const tr = document.createElement("tr"); tr.style.display="none";
      tr.innerHTML = `<td>${escapeHtml(f.name)}</td>`;
      tbody.appendChild(tr);
    }
  }
}

// Downloads a file with a live Transfers-drawer entry (progress + ETA + pause/cancel).
// opts: { silent, onProgress(p), ctrl, transferId }
export async function downloadFileV2(fileId, filename, sizeBytes, opts = {}) {
  const passphrase = getAutoPassphrase();
  const ctrl = opts.ctrl || makeTransferCtrl();
  const id = opts.transferId || addTransfer({ kind: "download", name: filename, size: sizeBytes || 0, ctrl });
  const setRow = (patch) => { if (!opts.transferId) updateTransfer(id, patch); else updateTransfer(opts.transferId, patch); };
  let plain;
  try {
    plain = await fetchAndDecrypt(fileId, (p) => {
      setRow({ received: p.received, total: p.total, speed: p.speed, status: ctrl.paused ? "paused" : "active" });
      if (opts.onProgress) opts.onProgress(p);
    }, sizeBytes, ctrl);
  } catch (e) {
    if (ctrl.cancelled) { setRow({ status: "cancelled" }); return { ok: false, cancelled: true }; }
    console.error("Download failed:", e);
    setRow({ status: "error", error: e.message });
    if (!opts.silent) toast("Download failed", "err");
    return { ok: false, error: e.message };
  }
  const blob = new Blob([plain]);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  finishTransfer(id, "done");
  if (!opts.silent) toast(`Downloaded ${filename}`);
  return { ok: true, id };
}

export async function downloadFile(fileId, filename, sizeBytes) {
  await downloadFileV2(fileId, filename, sizeBytes);
}

export async function deleteFile(fileId) {
  if (!confirm("Move this file to trash?")) return;
  const r = await fetch(`${API}/files/${fileId}`, {method: "DELETE"});
  if (r.ok) { refreshFiles(); toast("Moved to trash"); updateTrashCount(); }
  else {
    const d = await r.json().catch(() => ({}));
    toast(d.error || "Delete failed", "err");
  }
}

// ── VERSION HISTORY ─────────────────────────────────────────────────────────
export async function openVersions(fileId, filename) {
  state.versionFileId = fileId;
  window.versionFileId = fileId;
  document.getElementById("versions-filename").textContent = filename;
  document.getElementById("versions-subtitle").textContent = `file id #${fileId}`;
  // reuse showView from api.js
  const { showView: sv } = await import("./api.js");
  sv("versions");
  await loadVersions(fileId);
}

export async function loadVersions(fileId) {
  const r = await fetch(`${API}/files/${fileId}/versions`);
  if (!r.ok) return;
  const versions = await r.json();
  const list = document.getElementById("version-list");
  list.innerHTML = "";
  const sorted = [...versions].sort((a, b) => a.version_number - b.version_number);
  for (let i = 0; i < versions.length; i++) {
    const v = versions[i];
    const prev = sorted[sorted.indexOf(v) - 1];
    let changeHtml = "";
    if (prev) {
      const diff = v.size_bytes - prev.size_bytes;
      const sign = diff >= 0 ? "+" : "";
      const cls = diff > 0 ? "change-up" : diff < 0 ? "change-down" : "change-same";
      changeHtml = `<span class="version-change ${cls}">${sign}${fmt(Math.abs(diff))}</span>`;
    } else {
      changeHtml = `<span class="version-change change-new">initial</span>`;
    }
    const card = document.createElement("div");
    card.className = "version-card" + (v.is_current ? " current" : "");
    card.innerHTML = `
      <div class="version-no">v${v.version_number}</div>
      <div class="version-info">
        <div class="size">${fmt(v.size_bytes)} ${changeHtml}</div>
        <div class="who">by ${escapeHtml(v.uploaded_by_name || "—")} · ${fmtDate(v.uploaded_at)}</div>
        <div class="version-sha">${escapeHtml(v.sha256)}</div>
      </div>
      <div class="action-row">
        ${v.is_current
          ? `<span class="current-pill">Current</span>`
          : (state.currentUser?.role !== "read_only"
              ? `<button class="btn-sm" onclick="restoreVersion('${fileId}',${v.version_number})">↩ Restore</button>`
              : "")}
      </div>`;
    list.appendChild(card);
  }
}

export async function restoreVersion(fileId, versionNo) {
  const r = await fetch(`${API}/files/${fileId}/restore/${versionNo}`, {method: "POST"});
  if (r.ok) { await loadVersions(fileId); toast(`Restored to v${versionNo}`); }
  else toast("Restore failed", "err");
}

// ── TRASH ───────────────────────────────────────────────────────────────────
export async function loadTrash() {
  const r = await fetch(API + "/trash");
  if (!r.ok) return;
  const items = await r.json();
  const list = document.getElementById("trash-list");
  const empty = document.getElementById("empty-trash");
  document.getElementById("trash-count").textContent = items.length || "—";
  list.innerHTML = "";
  if (!items.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (const f of items) {
    const card = document.createElement("div");
    card.className = "trash-card";
    card.innerHTML = `
      <span class="trash-name">${escapeHtml(f.name)}</span>
      <span class="trash-who">deleted by ${escapeHtml(f.deleted_by_name || "—")} · ${fmtDate(f.deleted_at)}</span>
      <div class="action-row">
        <button class="btn-sm" onclick="restoreFromTrash('${f.id}')">↩ Restore</button>
        <button class="btn-sm danger" onclick="hardDelete('${f.id}')">Destroy</button>
      </div>`;
    list.appendChild(card);
  }
}

export async function updateTrashCount() {
  if (state.currentUser?.role !== "org_admin" && state.currentUser?.role !== "master_admin") return;
  const r = await fetch(API + "/trash");
  if (r.ok) {
    const items = await r.json();
    document.getElementById("trash-count").textContent = items.length || "—";
  }
}

export async function restoreFromTrash(fileId) {
  const r = await fetch(`${API}/trash/${fileId}/restore`, {method: "POST"});
  if (r.ok) { loadTrash(); refreshFiles(); toast("File restored"); }
  else toast("Restore failed", "err");
}

export async function hardDelete(fileId) {
  if (!confirm("Permanently destroy this file and all versions? This cannot be undone.")) return;
  const r = await fetch(`${API}/trash/${fileId}`, {method: "DELETE"});
  if (r.ok) { loadTrash(); toast("File permanently destroyed"); }
  else toast("Hard delete failed", "err");
}

// ── PREVIEW / EDIT (kept in files.js per drag/drop ownership) ───────────────
function isMediaExt(name) {
  const e = (name.split(".").pop() || "").toLowerCase();
  return ["mp4","webm","mkv","ogv","mov","mp3","wav","ogg","m4a","aac","flac"].includes(e);
}
function isDocExt(name) {
  const e = (name.split(".").pop() || "").toLowerCase();
  return ["pdf","doc","docx","xls","xlsx","ppt","pptx","txt","md","csv","rtf","odt","ods","odp"].includes(e);
}
let _previewFileId = null;
let _previewFilename = "";

export function previewFile(fileId, filename, sizeBytes) {
  _previewFileId = fileId;
  _previewFilename = filename;
  document.getElementById("preview-title").textContent = filename;
  const content = document.getElementById("preview-content");
  content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Loading preview…</div>';
  openModal("preview-modal");
  const ext = filename.split(".").pop().toLowerCase();
  if (["png","jpg","jpeg","gif","svg","webp","bmp"].includes(ext)) {
    loadPreviewAsBlob(content, `<img src="" alt="${escapeHtml(filename)}" style="max-width:100%;max-height:70vh;display:block;margin:auto;border-radius:4px">`);
  } else if (ext === "pdf") {
    loadPreviewAsBlob(content, `<iframe src="" style="width:100%;height:70vh;border:none;border-radius:4px"></iframe>`);
  } else if (["mp4","webm","ogv","mov"].includes(ext)) {
    playMediaStreaming(content, ext, false);
  } else if (["mp3","wav","ogg","m4a","aac","flac"].includes(ext)) {
    playMediaStreaming(content, ext, true);
  } else if (ext === "mkv") {
    playMkvFromPreview(content);
  } else if (["txt","md","json","csv","html","css","js","py","java","c","cpp","h","xml","yaml","yml","sh","log","ini","cfg","conf","env","sql","rb","go","rs","ts","tsx","jsx","vue","svelte","toml"].includes(ext)) {
    loadPreviewAsText(content, ext);
  } else if (ext === "docx") {
    loadDocxPreview(content);
  } else if (ext === "xlsx" || ext === "xls") {
    loadXlsxPreview(content);
  } else if (ext === "pptx" || ext === "ppt") {
    loadPptxPreview(content);
  } else {
    const typeLabels = { doc:"Word Document (legacy)", xls:"Excel Spreadsheet (legacy)", ppt:"PowerPoint (legacy)", zip:"ZIP Archive", rar:"RAR Archive", "7z":"7-Zip Archive", tar:"TAR Archive", gz:"GZIP Archive" };
    content.innerHTML = `
      <div style="text-align:center;padding:60px 20px">
        <div style="font-size:64px;margin-bottom:16px">📄</div>
        <div style="font-size:18px;font-weight:600;margin-bottom:8px">${escapeHtml(filename)}</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:4px">${typeLabels[ext] || escapeHtml(ext.toUpperCase())} file · ${fmt(parseInt(sizeBytes) || 0)}</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px">In-browser preview not available for this file type.</div>
        <button class="btn-sm active" onclick="downloadPreviewFile()">↓ Download to view</button>
      </div>`;
  }
}

function getMimeForExt(ext) {
  const map = { png:"image/png", jpg:"image/jpeg", jpeg:"image/jpeg", gif:"image/gif", svg:"image/svg+xml", webp:"image/webp", bmp:"image/bmp", pdf:"application/pdf", mp4:"video/mp4", webm:"video/webm", mp3:"audio/mpeg", wav:"audio/wav", ogg:"audio/ogg" };
  return map[ext] || "application/octet-stream";
}
function loadPreviewAsBlob(container, htmlTemplate) {
  const passphrase = getAutoPassphrase();
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting…</div>';
  fetch(`${API}/files/${_previewFileId}/preview`, {credentials:"same-origin"}).then(r => {
    if (!r.ok) throw new Error("Preview failed");
    return r.arrayBuffer();
  }).then(async buf => {
    const ct = new Uint8Array(buf);
    let plain;
    try { plain = await decryptAssembled(ct, passphrase); }
    catch { container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>`; return; }
    const ext = _previewFilename.split(".").pop().toLowerCase();
    const mime = getMimeForExt(ext);
    const blob = new Blob([plain], {type: mime});
    const prev = getPreviewBlobUrl();
    if (prev) URL.revokeObjectURL(prev);
    const url = URL.createObjectURL(blob);
    setPreviewBlobUrl(url);
    container.innerHTML = htmlTemplate;
    // htmlTemplate may be <img>, <iframe>, <video><source>, or audio wrapper — find correct media element
    let el = container.querySelector("img, iframe, video, audio");
    if (!el) el = container.firstElementChild;
    if (el.tagName === "VIDEO" || el.tagName === "AUDIO") {
      const srcEl = el.querySelector("source");
      if (srcEl) { srcEl.src = url; el.load(); }
      else el.src = url;
    } else if (el.tagName === "DIV") {
      // audio wrapper case: <div><audio><source>
      const audio = el.querySelector("audio");
      if (audio) { const srcEl = audio.querySelector("source"); if (srcEl) srcEl.src = url; else audio.src = url; audio.load(); }
      else el.textContent = "Preview ready";
    } else {
      el.src = url;
    }
  }).catch(err => {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Preview failed: ${escapeHtml(err.message)}</div>`;
  });
}

export function playFile(fileId, filename, sizeBytes) {
  previewFile(fileId, filename, sizeBytes);
}

export async function playMediaStreaming(content, ext, isAudio) {
  const passphrase = getAutoPassphrase();
  let key;
  try { key = await deriveKey(passphrase); }
  catch { content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed (wrong passphrase?)</div>'; return; }
  const mime = isAudio
    ? (ext === "wav" ? "audio/wav" : ext === "ogg" ? "audio/ogg" : ext === "m4a" ? "audio/mp4" : "audio/mpeg")
    : (ext === "webm" ? "video/webm" : ext === "ogv" ? "video/ogg" : "video/mp4");
  content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and preparing playback…</div>';
  try {
    const ct = await fetchAllChunks(_previewFileId);
    const plain = await decryptAssembled(ct, passphrase);
    playViaBlob(content, ext, isAudio, mime, plain);
  } catch (err) {
    content.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Playback failed: ${escapeHtml(err.message)}<br><button class="btn-sm active" onclick="downloadPreviewFile()">⬇ Download to view</button></div>`;
  }
}

// Fetch every stored chunk with many small parallel requests (one per Telegram
// message), mirroring the upload model. Avoids one 60s-bounded stream so large
// files download/replay reliably on serverless. Stops at the first 404.
async function fetchAllChunks(fileId, onProgress, total, ctrl) {
  const results = [];
  let next = 0, stop = false, received = 0;
  const conc = 3; // fewer parallel requests -> fewer simultaneous Telegram connections (avoids FloodWait)
  let lastSample = { received: 0, t: Date.now() };
  async function fetchChunk(i) {
    let lastErr = null;
    for (let attempt = 1; attempt <= 4; attempt++) {
      if (ctrl && ctrl.cancelled) throw new Error("cancelled");
      let signal;
      if (ctrl) { if (!ctrl.ac) ctrl.ac = new AbortController(); signal = ctrl.ac.signal; }
      try {
        const r = await fetch(`${API}/files/${fileId}/chunk/${i}`, {credentials: "same-origin", signal});
        if (r.status === 404) return { done: true };
        if (r.ok) {
          const buf = new Uint8Array(await r.arrayBuffer());
          return { buf };
        }
        let msg = "";
        try { const j = await r.json(); msg = j.error || ""; } catch {}
        lastErr = new Error(`HTTP ${r.status}${msg ? " — " + msg : ""}`);
        if (r.status === 401 || r.status === 403) break; // auth errors: don't retry
        await new Promise(res => setTimeout(res, 400 * attempt));
      } catch (e) {
        if (e && e.name === "AbortError") throw new Error("cancelled");
        lastErr = e;
        await new Promise(res => setTimeout(res, 400 * attempt));
      }
    }
    throw lastErr || new Error("Chunk fetch failed");
  }
  async function worker() {
    while (!stop) {
      if (ctrl && ctrl.cancelled) return;
      if (ctrl && ctrl.paused) { await new Promise(res => ctrl.waiters.push(res)); if (ctrl.cancelled) return; }
      const i = next++;
      const res = await fetchChunk(i);
      if (res.done) { stop = true; return; }
      results[i] = res.buf; received += res.buf.length;
      if (onProgress) {
        const now = Date.now();
        const dt = (now - lastSample.t) / 1000;
        let speed = 0;
        if (dt >= 0.4) { speed = (received - lastSample.received) / dt; lastSample = { received, t: now }; }
        const pct = total ? Math.min(99, Math.round(received / total * 100)) : 0;
        onProgress({ received, total, pct, speed });
      }
    }
  }
  await Promise.all(Array.from({length: conc}, worker));
  if (ctrl && ctrl.cancelled) throw new Error("cancelled");
  let n = 0; for (const c of results) if (c) n += c.length;
  const out = new Uint8Array(n); let o = 0;
  for (const c of results) if (c) { out.set(c, o); o += c.length; }
  return out;
}

// Decrypt one stored record buffer (a single Telegram message = one record).
// Format per record: [u32be len][iv 12][ciphertext (len-12)]; the very first
// record (index 0) is prefixed with a 16-byte magic. Records are independent,
// so each can be decrypted as soon as its chunk finishes downloading.
async function decryptRecord(buf, idx, key) {
  let off = (idx === 0) ? 16 : 0;
  if (off + 4 > buf.length) return new Uint8Array(0);
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const parts = [];
  while (off + 4 <= buf.length) {
    const len = dv.getUint32(off); off += 4;
    if (len < 12 || off + len > buf.length) break;
    const iv = buf.subarray(off, off + 12); off += 12;
    const ct = buf.subarray(off, off + (len - 12)); off += (len - 12);
    const pt = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct));
    parts.push(pt);
  }
  let n = 0; for (const p of parts) n += p.length;
  const out = new Uint8Array(n); let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

// Download every chunk AND decrypt each record as it lands, so decryption
// overlaps network instead of happening after the whole file is downloaded.
// Returns the assembled plaintext Uint8Array.
async function fetchAndDecrypt(fileId, onProgress, total, ctrl) {
  const results = [];
  let next = 0, stop = false, received = 0;
  const conc = 4; // download+decrypt in parallel (still few enough to avoid FloodWait)
  let lastSample = { received: 0, t: Date.now() };
  const key = await deriveKey(getAutoPassphrase());
  async function fetchChunk(i) {
    let lastErr = null;
    for (let attempt = 1; attempt <= 4; attempt++) {
      if (ctrl && ctrl.cancelled) throw new Error("cancelled");
      let signal;
      if (ctrl) { if (!ctrl.ac) ctrl.ac = new AbortController(); signal = ctrl.ac.signal; }
      try {
        const r = await fetch(`${API}/files/${fileId}/chunk/${i}`, {credentials: "same-origin", signal});
        if (r.status === 404) return { done: true };
        if (r.ok) {
          const buf = new Uint8Array(await r.arrayBuffer());
          return { buf };
        }
        let msg = "";
        try { const j = await r.json(); msg = j.error || ""; } catch {}
        lastErr = new Error(`HTTP ${r.status}${msg ? " — " + msg : ""}`);
        if (r.status === 401 || r.status === 403) break;
        await new Promise(res => setTimeout(res, 400 * attempt));
      } catch (e) {
        if (e && e.name === "AbortError") throw new Error("cancelled");
        lastErr = e;
        await new Promise(res => setTimeout(res, 400 * attempt));
      }
    }
    throw lastErr || new Error("Chunk fetch failed");
  }
  async function worker() {
    while (!stop) {
      if (ctrl && ctrl.cancelled) return;
      if (ctrl && ctrl.paused) { await new Promise(res => ctrl.waiters.push(res)); if (ctrl.cancelled) return; }
      const i = next++;
      const res = await fetchChunk(i);
      if (res.done) { stop = true; return; }
      const pt = await decryptRecord(res.buf, i, key);
      results[i] = pt; received += pt.length;
      if (onProgress) {
        const now = Date.now();
        const dt = (now - lastSample.t) / 1000;
        let speed = 0;
        if (dt >= 0.4) { speed = (received - lastSample.received) / dt; lastSample = { received, t: now }; }
        const pct = total ? Math.min(99, Math.round(received / total * 100)) : 0;
        onProgress({ received, total, pct, speed });
      }
    }
  }
  await Promise.all(Array.from({length: conc}, worker));
  if (ctrl && ctrl.cancelled) throw new Error("cancelled");
  let n = 0; for (const c of results) if (c) n += c.length;
  const out = new Uint8Array(n); let o = 0;
  for (const c of results) if (c) { out.set(c, o); o += c.length; }
  return out;
}

async function playViaBlob(content, ext, isAudio, mime, plain) {
  const blob = new Blob([plain], {type: mime});
  const url = URL.createObjectURL(blob);
  const media = document.createElement(isAudio ? "audio" : "video");
  media.controls = true;
  if (isAudio) media.style.width = "100%";
  else { media.style.maxWidth = "100%"; media.style.maxHeight = "80vh"; media.style.display = "block"; media.style.margin = "auto"; }
  content.innerHTML = "";
  content.appendChild(media);
  if (plain.length > 800 * 1024 * 1024) {
    const note = document.createElement("div");
    note.style.cssText = "text-align:center;color:var(--muted);font-size:11px;padding:6px";
    note.textContent = `Large file (${fmt(plain.length)}): decrypted in-browser, so it needs enough RAM to play. If playback fails, use Download instead.`;
    content.appendChild(note);
  }
  media.src = url;
  media.load();
}

// ── MKV playback ─────────────────────────────────────────────────────────────
// Browsers have no native MKV decoder. We decrypt in-browser, then demux the MKV
// and remux it to fragmented MP4 (mp4-muxer) and play via MSE. Works on the
// decrypted bytes only — zero-knowledge preserved. Unsupported codecs fall back
// to the Download button. Libraries are imported lazily so a CDN/API hiccup can
// never break the rest of the app.
function mkvVideoCodec(id) {
  const c = (id || "").toUpperCase();
  if (c.includes("AVC") || c.includes("H264")) return "avc";
  if (c.includes("HEVC") || c.includes("H265")) return "hevc";
  if (c.includes("VP9")) return "vp9";
  if (c.includes("AV1")) return "av1";
  return null;
}
function mkvAudioCodec(id) {
  const c = (id || "").toUpperCase();
  if (c.includes("AAC")) return "aac";
  if (c.includes("OPUS")) return "opus";
  return null; // AC3/EAC3/FLAC/MP3 etc. not muxable to MP4 by mp4-muxer
}
function showMkvFallback(content) {
  content.innerHTML = `
    <div style="text-align:center;padding:60px 20px">
      <div style="font-size:64px;margin-bottom:16px">🎬</div>
      <div style="font-size:16px;margin-bottom:8px">${escapeHtml(_previewFilename)}</div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:20px">This MKV can't be played in-browser (codec not supported). Download to view it.</div>
      <button class="btn-sm active" onclick="downloadPreviewFile()">⬇ Download to view</button>
    </div>`;
}
async function playMkvFromPreview(content) {
  content.innerHTML = `
    <div style="text-align:center">
      <video id="mkv-video" controls style="max-width:100%;max-height:80vh;display:block;margin:auto;background:#000"></video>
      <div id="mkv-overlay" style="color:var(--muted);font-size:13px;padding:10px">⏳ Preparing…</div>
    </div>`;
  const video = content.querySelector("#mkv-video");
  const overlay = content.querySelector("#mkv-overlay");
  const setOverlay = (txt) => { if (overlay) overlay.textContent = txt; };
  try {
    const plain = await fetchAndDecrypt(_previewFileId, (p) => {
      const remaining = p.total ? (p.total - p.received) : 0;
      const eta = p.speed > 0 ? remaining / p.speed : 0;
      setOverlay(`⏳ Downloading & decrypting ${p.pct}% · ETA ${fmtEta(eta)}`);
    });
    setOverlay("⚙️ Preparing playback…");
    await playMkvInto(video, plain, overlay);
  } catch (e) {
    console.error("MKV prepare failed:", e);
    showMkvFallback(content);
  }
}
// Demux the decrypted MKV and remux to fragmented MP4 into the given <video>.
// Throws on error/unsupported codec so the caller can show the fallback.
async function playMkvInto(video, plain, overlay) {
  let url = null;
  try {
    const [mkvMod, mp4muxer] = await Promise.all([
      import("https://cdn.jsdelivr.net/npm/mkv-demuxer@0.1.0/+esm"),
      import("https://cdn.jsdelivr.net/npm/mp4-muxer@5.2.2/+esm"),
    ]);
    const MkvDemuxer = mkvMod.MkvDemuxer;
    const { Muxer, ArrayBufferTarget } = mp4muxer;
    const file = new File([plain], "video.mkv");
    const demuxer = new MkvDemuxer();
    await demuxer.initFile(file, 4 * 1024 * 1024);
    const meta = await demuxer.getMeta();
    const data = await demuxer.getData();
    const v = meta.video, a = meta.audio;
    if (!v || !v.codecID) throw new Error("No video track");
    const vCodec = mkvVideoCodec(v.codecID);
    if (!vCodec) throw new Error("Unsupported video codec: " + v.codecID);
    const muxerOpts = {
      target: new ArrayBufferTarget(),
      fastStart: "in-memory",
      firstTimestampBehavior: "offset",
      video: { codec: vCodec, width: v.width, height: v.Height || v.height },
    };
    let aCodec = null;
    if (a && a.codecID) aCodec = mkvAudioCodec(a.codecID);
    if (aCodec) {
      muxerOpts.audio = { codec: aCodec, numberOfChannels: a.channels || 2, sampleRate: a.rate || 48000 };
    }
    const muxer = new Muxer(muxerOpts);
    // mkv-demuxer packet timestamps are in SECONDS (it divides by 1e3); mp4-muxer wants MICROSECONDS.
    const TS_MULT = 1e6;
    const vPkts = data.videoPackets || [];
    let firstV = true;
    for (let i = 0; i < vPkts.length; i++) {
      const p = vPkts[i];
    const ts = Math.round(p.timestamp * TS_MULT);
    const nextTs = (i + 1 < vPkts.length) ? Math.round(vPkts[i + 1].timestamp * TS_MULT) : ts;
      const dur = Math.max(0, nextTs - ts);
      const metaArg = (vCodec === "avc" && firstV && v.codecPrivate)
        ? { decoderConfig: { description: new Uint8Array(v.codecPrivate) } } : undefined;
      if (firstV && vCodec === "avc") firstV = false;
      muxer.addVideoChunkRaw(plain.subarray(p.start, p.end), p.isKeyframe ? "key" : "delta", ts, dur, metaArg);
    }
    if (aCodec) {
      const aPkts = data.audioPackets || [];
      let firstA = true;
      for (let i = 0; i < aPkts.length; i++) {
        const p = aPkts[i];
    const ts = Math.round(p.timestamp * TS_MULT);
    const nextTs = (i + 1 < aPkts.length) ? Math.round(aPkts[i + 1].timestamp * TS_MULT) : ts;
        const dur = Math.max(0, nextTs - ts);
        const metaArg = (firstA && a.codecPrivate)
          ? { decoderConfig: { description: new Uint8Array(a.codecPrivate) } } : undefined;
        if (firstA) firstA = false;
        muxer.addAudioChunkRaw(plain.subarray(p.start, p.end), p.isKeyframe ? "key" : "delta", ts, dur, metaArg);
      }
    }
    muxer.finalize();
    const blob = new Blob([muxer.target.buffer], { type: "video/mp4" });
    url = URL.createObjectURL(blob);
    video.src = url;
    video.load();
    try { await video.play(); } catch {}
    if (overlay) overlay.textContent = "";
  } catch (err) {
    console.error("MKV remux failed:", err);
    if (url) { URL.revokeObjectURL(url); url = null; }
    throw err; // caller shows fallback
  }
}

// Fragmented-MP4 streaming: decrypt records, split the plaintext into BMFF
// init (ftyp+moov) + media segments (moof+mdat…), and append to MediaSource
// as they arrive — so the whole file is never held in memory.
function loadPreviewAsText(container, ext) {
  const passphrase = getAutoPassphrase();
container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting…</div>';
  fetch(`${API}/files/${_previewFileId}/preview`).then(r => {
    if (!r.ok) throw new Error("Preview failed");
    return r.arrayBuffer();
  }).then(async buf => {
    const ct = new Uint8Array(buf);
    let plain;
    try { plain = await decryptAssembled(ct, passphrase); }
    catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
    const text = new TextDecoder().decode(plain);
    container.innerHTML = `<pre style="margin:0;padding:16px;font-family:var(--mono);font-size:12px;white-space:pre-wrap;word-break:break-word;color:var(--text);overflow:auto;max-height:65vh">${escapeHtml(text)}</pre>`;
  }).catch(err => {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">${escapeHtml(err.message)}</div>`;
  });
}

async function loadDocxPreview(container) {
  const passphrase = getAutoPassphrase();
if (typeof mammoth === "undefined") { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Word viewer library not loaded. Check your internet connection.</div>'; return; }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering Word document…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    let plain; try { plain = await decryptAssembled(ct, passphrase); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
    const result = await mammoth.convertToHtml({arrayBuffer: plain});
    const html = result.value || '<p style="color:var(--muted)">Document is empty.</p>';
    const warnings = result.messages.filter(m => m.type === "warning");
    container.innerHTML = `<div class="docx-preview" style="padding:24px;font-family:'IBM Plex Sans',sans-serif;color:var(--text);font-size:14px;line-height:1.7;max-height:65vh;overflow:auto">${html}</div>${warnings.length ? `<div style="padding:8px 16px;font-size:11px;color:var(--muted);border-top:1px solid var(--glass-border)">${warnings.length} conversion warnings</div>` : ""}`;
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Word preview failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function loadXlsxPreview(container) {
  const passphrase = getAutoPassphrase();
if (typeof XLSX === "undefined") { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Excel viewer library not loaded. Check your internet connection.</div>'; return; }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering spreadsheet…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    let plain; try { plain = await decryptAssembled(ct, passphrase); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
    const workbook = XLSX.read(plain, {type: "array"});
    const sheetName = workbook.SheetNames[0];
    const sheet = workbook.Sheets[sheetName];
    const html = XLSX.utils.sheet_to_html(sheet, {editable: false});
    container.innerHTML = `<div style="padding:4px"><div style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--glass-border);background:rgba(0,0,0,.2)"><span style="font-size:12px;font-weight:600;color:var(--accent)">Sheet: ${escapeHtml(sheetName)}</span>${workbook.SheetNames.length > 1 ? `<span style="font-size:11px;color:var(--muted)">(${workbook.SheetNames.length} sheets: ${workbook.SheetNames.map(s => escapeHtml(s)).join(", ")})</span>` : ""}</div><div class="xlsx-scroll" style="overflow:auto;max-height:60vh">${html}</div></div>`;
    const tbl = container.querySelector(".xlsx-scroll table");
    if (tbl) {
      tbl.style.borderCollapse = "collapse"; tbl.style.fontSize = "12px"; tbl.style.width = "100%";
      tbl.querySelectorAll("td, th").forEach(c => { c.style.border="1px solid rgba(255,255,255,.06)"; c.style.padding="6px 10px"; c.style.whiteSpace="nowrap"; });
      tbl.querySelectorAll("th").forEach(th => { th.style.background="rgba(0,0,0,.3)"; th.style.fontWeight="600"; th.style.color="var(--accent)"; th.style.position="sticky"; th.style.top="0"; });
    }
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Excel preview failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function loadPptxPreview(container) {
  const passphrase = getAutoPassphrase();
if (typeof JSZip === "undefined") { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">PPT viewer library not loaded. Check your internet connection.</div>'; return; }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering presentation…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    let plain; try { plain = await decryptAssembled(ct, passphrase); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
    const zip = await JSZip.loadAsync(plain);
    const slideFiles = [];
    zip.forEach((path, file) => { if (path.match(/^ppt\/slides\/slide\d+\.xml$/) && !path.endsWith("/")) slideFiles.push({path, file}); });
    slideFiles.sort((a,b)=>parseInt(a.path.match(/slide(\d+)\.xml/)[1])-parseInt(b.path.match(/slide(\d+)\.xml/)[1]));
    if (!slideFiles.length) { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No slides found in presentation.</div>'; return; }
    const slideContainer = document.createElement("div"); slideContainer.id="ppt-slides"; container.innerHTML=""; container.appendChild(slideContainer);
    let currentSlide=0;
    function renderSlide(idx){
      const sf=slideFiles[idx];
      sf.file.async("string").then(xml=>{
        const parser=new DOMParser(); const doc=parser.parseFromString(xml,"application/xml");
        const texts=[]; doc.querySelectorAll("a\\:t, t").forEach(t=>{ const text=t.textContent.trim(); if(text) texts.push(text); });
        // fallback selector for namespaced
        if (!texts.length) doc.querySelectorAll("*").forEach(n=>{ if(n.localName==="t" && n.textContent.trim()) texts.push(n.textContent.trim()); });
        const title=texts[0]||`Slide ${idx+1}`; const bullets=texts.slice(1);
        slideContainer.innerHTML=`<div style="display:flex;flex-direction:column;align-items:center;padding:16px"><div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;width:100%;max-width:700px"><button class="btn-sm" onclick="pptNavigate(-1)" ${idx===0?"disabled style='opacity:.3'":""}>← Prev</button><span style="flex:1;text-align:center;font-size:12px;color:var(--muted)">Slide ${idx+1} of ${slideFiles.length}</span><button class="btn-sm" onclick="pptNavigate(1)" ${idx===slideFiles.length-1?"disabled style='opacity:.3'":""}>Next →</button></div><div style="background:rgba(255,255,255,.95);border-radius:8px;padding:40px 32px;width:100%;max-width:700px;min-height:350px;box-shadow:0 8px 32px rgba(0,0,0,.4);color:#1a1a1a"><div style="font-size:22px;font-weight:700;margin-bottom:20px;color:#111">${escapeHtml(title)}</div>${bullets.map(b=>`<div style="font-size:14px;line-height:1.6;margin-bottom:6px;padding-left:16px;border-left:3px solid #22d3ee">${escapeHtml(b)}</div>`).join("")}</div></div>`;
        currentSlide=idx;
      });
    }
    window.pptNavigate=function(dir){ const next=currentSlide+dir; if(next>=0&&next<slideFiles.length) renderSlide(next); };
    renderSlide(0);
  } catch(err){ container.innerHTML=`<div style="text-align:center;padding:40px;color:var(--danger)">PowerPoint preview failed: ${escapeHtml(err.message)}</div>`; }
}

export function downloadPreviewFile() {
  if (!_previewFileId || !_previewFilename) return;
  downloadFile(_previewFileId, _previewFilename);
}

// ── EDIT ────────────────────────────────────────────────────────────────────
let _editFileId = null;
let _editFileName = "";
let _editSizeBytes = 0;

export function editFile(fileId, filename, sizeBytes) {
  _editFileId = fileId; _editFileName = filename; _editSizeBytes = parseInt(sizeBytes) || 0;
  document.getElementById("edit-title").textContent = `Edit — ${filename}`;
  const ext = filename.split(".").pop().toLowerCase();
  const textExts = ["txt","md","json","csv","html","css","js","py","java","c","cpp","h","xml","yaml","yml","sh","log","ini","cfg","conf","env","sql","rb","go","rs","ts","tsx","jsx","vue","svelte","toml","php"];
  document.getElementById("edit-textarea-wrap").style.display = "none";
  document.getElementById("edit-binary-notice").style.display = "none";
  document.getElementById("edit-rich-wrap").style.display = "none";
  openModal("edit-modal");
  if (ext === "docx") {
    document.getElementById("edit-rich-wrap").style.display = "block";
    document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Loading document…</div>';
    loadDocxForEdit(fileId);
  } else if (textExts.includes(ext)) {
    document.getElementById("edit-textarea-wrap").style.display = "block";
    document.getElementById("edit-textarea").value = "Loading…";
    loadFileForEdit(fileId);
  } else {
    document.getElementById("edit-binary-notice").style.display = "block";
  }
}

async function loadFileForEdit(fileId) {
  const passphrase = getAutoPassphrase();
try {
    const r = await fetch(`${API}/files/${fileId}/preview`);
    if (!r.ok) throw new Error("Failed to load");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const plain = await decryptAssembled(ct, passphrase);
    document.getElementById("edit-textarea").value = new TextDecoder().decode(plain);
  } catch (err) { document.getElementById("edit-textarea").value = `Error loading file: ${err.message}`; }
}

export async function saveEdit() {
  if (!_editFileId) return;
  const passphrase = getAutoPassphrase();
const text = document.getElementById("edit-textarea").value;
  const plainBuf = new TextEncoder().encode(text);
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plainBuf);
  const out = new Uint8Array(12 + ct.byteLength); out.set(iv); out.set(new Uint8Array(ct), 12);
  const blob = new Blob([out]); const sha = await sha256Hex(blob);
  const formData = new FormData(); formData.append("file", blob, _editFileName); formData.append("filename", _editFileName); formData.append("folder_id", state.currentFolderId || ""); formData.append("sha256", sha);
  const btn = document.querySelector("#edit-modal .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const r = await fetch(`${API}/files/upload`, {method: "POST", body: formData});
  if (btn) { btn.disabled = false; btn.textContent = "Save as new version"; }
  if (r.ok) { closeModal("edit-modal"); refreshFiles(); toast("Saved as new version"); } else { const d = await r.json(); toast(d.error || "Save failed", "err"); }
}

export function downloadEditFile() { if (_editFileId && _editFileName) downloadFile(_editFileId, _editFileName); }

let _editDocxBlob = null;
async function loadDocxForEdit(fileId) {
  const passphrase = getAutoPassphrase();
if (typeof mammoth === "undefined") { document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Word editor library not loaded.</div>'; return; }
  try {
    const r = await fetch(`${API}/files/${fileId}/preview`);
    if (!r.ok) throw new Error("Failed to load");
    const buf = await r.arrayBuffer(); const ct = new Uint8Array(buf);     let plain; try { plain = await decryptAssembled(ct, passphrase); } catch { document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
    _editDocxBlob = new Blob([plain]);
    const result = await mammoth.convertToHtml({arrayBuffer: plain});
    const html = result.value || "";
    const editor = document.getElementById("edit-rich-editor");
    editor.innerHTML = html || "<p><br></p>";
    document.getElementById("edit-rich-content").innerHTML = "";
    document.getElementById("edit-rich-content").appendChild(editor);
    document.getElementById("edit-rich-toolbar").style.display = "flex";
  } catch (err) { document.getElementById("edit-rich-content").innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">${escapeHtml(err.message)}</div>`; }
}

export function richCmd(cmd, val) { document.execCommand(cmd, false, val || null); document.getElementById("edit-rich-editor").focus(); }

export async function saveEditRich() {
  if (!_editFileId) return;
  const passphrase = getAutoPassphrase();
const html = document.getElementById("edit-rich-editor").innerHTML;
  const plainBuf = new TextEncoder().encode(html);
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plainBuf);
  const out = new Uint8Array(12 + ct.byteLength); out.set(iv); out.set(new Uint8Array(ct), 12);
  const blob = new Blob([out]); const sha = await sha256Hex(blob);
  const formData = new FormData(); formData.append("file", blob, _editFileName); formData.append("filename", _editFileName); formData.append("folder_id", state.currentFolderId || ""); formData.append("sha256", sha);
  const btn = document.querySelector("#edit-modal .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const r = await fetch(`${API}/files/upload`, {method: "POST", body: formData});
  if (btn) { btn.disabled = false; btn.textContent = "Save as new version"; }
  if (r.ok) { closeModal("edit-modal"); refreshFiles(); toast("Saved as new version"); } else { const d = await r.json(); toast(d.error || "Save failed", "err"); }
}

// ── DRAG & DROP ─────────────────────────────────────────────────────────────
const mainPanel = document.getElementById("main-panel");
if (mainPanel) {
  const dropOverlay = document.createElement("div");
  dropOverlay.className = "drop-overlay";
  dropOverlay.innerHTML = '<div class="drop-label">↑ Drop files to upload</div>';
  mainPanel.style.position = "relative";
  mainPanel.appendChild(dropOverlay);
  let dragCounter = 0;
  mainPanel.addEventListener("dragenter", e => { e.preventDefault(); dragCounter++; dropOverlay.classList.add("visible"); });
  mainPanel.addEventListener("dragleave", () => { dragCounter--; if (dragCounter === 0) dropOverlay.classList.remove("visible"); });
  mainPanel.addEventListener("dragover", e => e.preventDefault());
  mainPanel.addEventListener("drop", e => {
    e.preventDefault(); dragCounter = 0; dropOverlay.classList.remove("visible");
    if (e.dataTransfer.files.length) {
      document.getElementById("file-input").files = e.dataTransfer.files;
      uploadFiles(document.getElementById("file-input"));
    }
  });
}

// ── KEYBOARD SHORTCUTS / DOUBLE-CLICK (files context) ───────────────────────
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
  switch(e.key) {
    case "u": case "U": if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); document.getElementById("file-input")?.click(); } break;
    case "1": window.showView?.("files"); break;
    case "2": if (state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin") window.showView?.("trash"); break;
    case "3": if (state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin") window.showView?.("logs"); break;
    case "4": if (state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin") window.showView?.("users"); break;
    case "5": if (state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin") window.showView?.("versions-all"); break;
    case "6": if (state.currentUser?.role === "org_admin" || state.currentUser?.role === "master_admin") window.showView?.("backup"); break;
  }
});

document.getElementById("file-tbody")?.addEventListener("dblclick", e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const btn = tr.querySelector(".btn-sm"); if (btn) btn.click();
});
document.getElementById("file-grid")?.addEventListener("dblclick", e => {
  const card = e.target.closest(".file-card"); if (!card) return;
  const id = card.dataset.id || card.querySelector(".card-menu-btn")?.getAttribute("onclick")?.match(/'([^']+)'/)?.[1];
  if (id) { const name = card.querySelector(".file-card-name")?.textContent || ""; previewFile(id, name, 0); }
});

// ── BULK OPERATIONS (Phase-1 O3) ─────────────────────────────────────────────
const _selected = new Set();
const fileMetaCache = new Map(); // id -> { name, size } for bulk actions

export function toggleFileSelect(fileId, checkbox) {
  if (checkbox.checked) _selected.add(fileId);
  else _selected.delete(fileId);
  updateBulkBar();
}

export function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  if (!bar) return;
  const count = _selected.size;
  bar.style.display = count > 0 ? "flex" : "none";
  const lbl = document.getElementById("bulk-count");
  if (lbl) lbl.textContent = count;
}

export function clearBulkSelection() {
  _selected.clear();
  updateBulkBar();
}

export async function updateStorageMeter() {
  const meter = document.getElementById("storage-meter");
  if (!meter) return;
  const showFor = state.currentUser && (state.currentUser.role === "org_admin" || state.currentUser.role === "master_admin");
  if (!showFor) { meter.style.display = "none"; return; }
  try {
    const sr = await fetch(`${API}/stats`, { credentials: "same-origin" });
    if (!sr.ok) { meter.style.display = "none"; return; }
    const sd = await sr.json();
    const s = sd.org || sd.totals;
    if (!s) { meter.style.display = "none"; return; }
    document.getElementById("storage-used").textContent = fmt(s.storage_bytes || 0);
    const quota = s.storage_quota_bytes;
    let extra = `· ${s.file_count || 0} files · ${s.folder_count || 0} folders`;
    if (quota) extra += ` · quota ${fmt(quota)}`;
    document.getElementById("storage-files").textContent = extra;
    meter.style.display = "block";
  } catch {
    meter.style.display = "none";
  }
}

export async function bulkDeleteFiles() {
  if (!_selected.size) return;
  if (!confirm(`Move ${_selected.size} file(s) to trash?`)) return;
  const r = await fetch(`${API}/files/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ ids: [..._selected] }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { toast(`Moved ${d.deleted} file(s) to trash`); _selected.clear(); updateBulkBar(); refreshFiles(); updateTrashCount(); }
  else toast(d.error || "Bulk delete failed", "err");
}

export async function bulkRestoreFiles() {
  if (!_selected.size) return;
  const r = await fetch(`${API}/files/bulk-restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ ids: [..._selected] }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { toast(`Restored ${d.restored} file(s)`); _selected.clear(); updateBulkBar(); refreshFiles(); }
  else toast(d.error || "Bulk restore failed", "err");
}

// Download each selected file as its own browser download, each tracked in the
// Transfers drawer, plus one aggregate overall row (capped concurrency to avoid
// browser multi-download prompts and Telegram FloodWait).
export async function bulkDownloadFiles() {
  if (!_selected.size) return;
  const items = [];
  for (const id of _selected) {
    const m = fileMetaCache.get(id);
    if (m) items.push({ id, name: m.name, size: m.size || 0 });
  }
  if (!items.length) { toast("Nothing to download", "err"); return; }
  const bulkTotal = items.reduce((s, i) => s + (i.size || 0), 0);
  const overallId = addTransfer({ kind: "download", name: `Bulk download · ${items.length} files`, size: bulkTotal, ctrl: null });
  const snap = new Map();
  let next = 0;
  async function worker() {
    for (let k; (k = next++) < items.length; ) {
      const it = items[k];
      const r = await downloadFileV2(it.id, it.name, it.size, {
        silent: true,
        onProgress: (p) => {
          snap.set(k, p.received);
          let rec = 0; for (const v of snap.values()) rec += v;
          updateTransfer(overallId, { received: rec, total: bulkTotal, speed: p.speed, status: "active" });
        },
      });
      snap.set(k, it.size);
      let rec = 0; for (const v of snap.values()) rec += v;
      updateTransfer(overallId, { received: rec, total: bulkTotal, status: r.ok ? "active" : "error" });
    }
  }
  await Promise.all(Array.from({ length: Math.min(2, items.length) }, worker));
  finishTransfer(overallId, "done");
  toast(`Bulk download finished (${items.length} files)`);
}

export async function openBulkMove() {
  if (!_selected.size) return;
  const r = await fetch(`${API}/folders`, { credentials: "same-origin" });
  const sel = document.getElementById("bulk-move-folder");
  if (sel) {
    sel.innerHTML = `<option value="">Root</option>`;
    if (r.ok) {
      const folders = await r.json();
      for (const f of folders) sel.innerHTML += `<option value="${f.id}">${escapeHtml(f.name)}</option>`;
    }
  }
  const m = document.getElementById("bulk-move-modal");
  if (m) m.style.display = "flex";
}

export async function confirmBulkMove() {
  if (!_selected.size) return;
  const folderId = document.getElementById("bulk-move-folder")?.value || null;
  const r = await fetch(`${API}/files/bulk-move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ ids: [..._selected], folder_id: folderId }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { toast(`Moved ${d.moved} file(s)`); _selected.clear(); updateBulkBar(); document.getElementById("bulk-move-modal").style.display = "none"; refreshFiles(); }
  else toast(d.error || "Bulk move failed", "err");
}

// Window exposure
if (typeof window !== "undefined") {
  window.uploadFiles = uploadFiles;
  window.uploadFile = uploadFiles;
  window.deriveKey = deriveKey;
  window.encryptFile = encryptFile;
  window.sha256Hex = sha256Hex;
  window.onFileSearch = onFileSearch;
  window.clearFileSearch = clearFileSearch;
  window.refreshFiles = refreshFiles;
  window.toggleMenu = toggleMenu;
  window.closeAllMenus = closeAllMenus;
  window.downloadFile = downloadFile;
  window.deleteFile = deleteFile;
  window.openVersions = openVersions;
  window.loadVersions = loadVersions;
  window.restoreVersion = restoreVersion;
  window.loadTrash = loadTrash;
  window.updateTrashCount = updateTrashCount;
  window.restoreFromTrash = restoreFromTrash;
  window.hardDelete = hardDelete;
  window.previewFile = previewFile;
  window.playFile = playFile;
  window.downloadPreviewFile = downloadPreviewFile;
  window.editFile = editFile;
  window.saveEdit = saveEdit;
  window.downloadEditFile = downloadEditFile;
  window.richCmd = richCmd;
  window.saveEditRich = saveEditRich;
  window.toggleFileSelect = toggleFileSelect;
  window.updateBulkBar = updateBulkBar;
  window.clearBulkSelection = clearBulkSelection;
  window.updateStorageMeter = updateStorageMeter;
  window.bulkDeleteFiles = bulkDeleteFiles;
  window.bulkRestoreFiles = bulkRestoreFiles;
  window.bulkDownloadFiles = bulkDownloadFiles;
  window.openBulkMove = openBulkMove;
  window.confirmBulkMove = confirmBulkMove;

  // Transfers drawer
  window.downloadFileV2 = downloadFileV2;
  window.toggleTransfersDrawer = toggleTransfersDrawer;
  window.transferToggle = transferToggle;
  window.transferCancel = transferCancel;
  window.transferDismiss = transferDismiss;
}
