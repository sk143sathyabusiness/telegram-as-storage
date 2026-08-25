// frontend/files.js — file list, drag/drop, folder-upload, progress ETA, preview/edit, versions/trash
import { API, state, toast, fmt, fmtDate, fmtSpeed, escapeHtml, openModal, closeModal, hideSkeleton, showSkeleton, revealOnScroll, getPreviewBlobUrl, setPreviewBlobUrl } from "./api.js";

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

  const area = document.getElementById("progress-area");
  const itemsEl = document.getElementById("upload-items");
  area.classList.add("visible");
  itemsEl.innerHTML = "";

  let totalBytes = 0, sentBytes = 0, okCount = 0, errCount = 0;
  const prepared = [];
  for (const file of files) {
    const encrypted = await encryptFile(file, passphrase);
    prepared.push({ file, encrypted });
    totalBytes += encrypted.size;
  }
  const t0 = Date.now();

  const results = await Promise.allSettled(prepared.map(({ file, encrypted }) =>
    uploadOne(file, encrypted, d => {
      sentBytes += d;
      const elapsed = (Date.now() - t0) / 1000;
      const speed = sentBytes / Math.max(elapsed, .01);
      const eta = Math.round((totalBytes - sentBytes) / Math.max(speed, 1));
      document.getElementById("overall-eta").textContent =
        `${fmt(sentBytes)} / ${fmt(totalBytes)}  —  ETA ${eta}s`;
    })
  ));
  const errDetails = [];
  results.forEach(r => {
    if (r.status === "fulfilled") okCount++;
    else { errCount++; if (r.reason?.message) errDetails.push(r.reason.message); console.error("[UPLOAD] failed:", r.reason.message); }
  });

  area.classList.remove("visible");
  usedInput.value = "";
  refreshFiles();
  const msg = okCount ? `Uploaded ${okCount} file(s)` : "";
  const errMsg = errCount ? `${errCount} failed${errDetails[0] ? ": " + errDetails[0].slice(0,180) : ""}` : "";
  toast([msg, errMsg].filter(Boolean).join(", "), errCount ? (okCount ? "ok" : "err") : "ok");
  if (errDetails.length) console.error("[UPLOAD] details:", errDetails.join(" | "));
}
export const uploadFile = uploadFiles; // alias per brief

async function uploadOne(file, encBlob, onProgress) {
    const sha256 = await sha256Hex(encBlob);
    const div = document.createElement("div");
    div.className = "upload-item";
    const pid = "p_" + Math.random().toString(36).slice(2);
    const eid = "e_" + Math.random().toString(36).slice(2);
    div.innerHTML = `<div class="upload-item-name">${escapeHtml(file.name)} <span class="file-size" style="color:var(--muted);font-size:11px">${fmt(encBlob.size)}</span></div>
      <div class="pbar"><div class="pbar-fill" id="${pid}"></div></div>
      <div class="upload-eta" id="${eid}"></div>`;
    document.getElementById("upload-items").appendChild(div);

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const t0 = Date.now();
      let lastLoaded = 0;
      xhr.upload.onprogress = e => {
        const d = e.loaded - lastLoaded; lastLoaded = e.loaded;
        onProgress(d);
        const pct = Math.round(e.loaded / e.total * 100);
        const pidEl = document.getElementById(pid);
        if (pidEl) pidEl.style.width = pct + "%";
        const elapsed = (Date.now() - t0) / 1000;
        const speed = e.loaded / Math.max(elapsed, .01);
        const eta = Math.round((e.total - e.loaded) / Math.max(speed, 1));
        const eidEl = document.getElementById(eid);
        if (eidEl) eidEl.textContent = `${pct}% · ${fmtSpeed(speed)} · ETA ${eta}s`;
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let msg = `HTTP ${xhr.status}`;
          try { const d = JSON.parse(xhr.responseText); if (d.error) msg = d.error; } catch {}
          reject(new Error(msg));
        }
      };
      xhr.onerror = () => reject(new Error("Network error"));
      xhr.open("POST", API + "/files/upload");
      const fd = new FormData();
      fd.append("file", encBlob, file.name);
      fd.append("filename", file.name);
      fd.append("folder_id", state.currentFolderId || "");
      fd.append("sha256", sha256);
      xhr.send(fd);
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
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try { plain = await crypto.subtle.decrypt({ name:"AES-GCM", iv }, key, ct.slice(12)); } catch { return; }
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
          <button onclick="shareFile('${f.id}','${safeName}'); closeAllMenus()">Share</button>
          <button onclick="emailFile('${f.id}','${safeName}'); closeAllMenus()">Email</button>
          <button onclick="editFile('${f.id}','${safeName}','${v ? v.size_bytes : 0}'); closeAllMenus()">Edit</button>
          <button onclick="downloadFile('${f.id}','${safeName}'); closeAllMenus()">⬇ Download</button>
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

export async function downloadFile(fileId, filename) {
  const passphrase = getAutoPassphrase();

  const toastEl = document.getElementById("toast");
  toastEl.className = "toast show";
  toastEl.textContent = "⬇ Downloading 0%";

  const r = await fetch(`${API}/files/${fileId}/download`);
  if (!r.ok) { toast("Download failed", "err"); return; }
  const cl = +r.headers.get("Content-Length") || 0;
  const reader = r.body.getReader();
  const chunks = [];
  let received = 0;
  const t0 = Date.now();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (cl) {
      const pct = Math.round(received / cl * 100);
      const elapsed = (Date.now() - t0) / 1000;
      const speed = received / Math.max(elapsed, .01);
      const eta = Math.round((cl - received) / Math.max(speed, 1));
      toastEl.textContent = `⬇ ${pct}% · ${fmtSpeed(speed)} · ETA ${eta}s`;
    }
  }
  toastEl.textContent = "🔓 Decrypting…";
  const ct = new Uint8Array(received);
  let off = 0;
  for (const c of chunks) { ct.set(c, off); off += c.length; }
  const iv = ct.slice(0, 12);
  const key = await deriveKey(passphrase);
  let plain;
  try {
    plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
  } catch {
    toast("Decryption failed", "err"); return;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([plain]));
  a.download = filename; a.click();
  toast(`Downloaded ${filename}`);
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
  } else if (["mp4","webm","ogg"].includes(ext) && ext !== "ogg") {
    loadPreviewAsBlob(content, `<video controls style="max-width:100%;max-height:70vh;display:block;margin:auto"><source src="" type="video/${ext}"></video>`);
  } else if (["mp3","wav"].includes(ext)) {
    loadPreviewAsBlob(content, `<div style="text-align:center;padding:40px"><div style="font-size:48px;margin-bottom:16px">🎵</div><audio controls style="width:100%"><source src="" type="audio/${ext}"></audio></div>`);
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
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); }
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

function loadPreviewAsText(container, ext) {
  const passphrase = getAutoPassphrase();
container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting…</div>';
  fetch(`${API}/files/${_previewFileId}/preview`).then(r => {
    if (!r.ok) throw new Error("Preview failed");
    return r.arrayBuffer();
  }).then(async buf => {
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); }
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
    const ct = new Uint8Array(buf); const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain; try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
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
    const ct = new Uint8Array(buf); const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain; try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
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
    const ct = new Uint8Array(buf); const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain; try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); } catch { container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
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
    const ct = new Uint8Array(buf); const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
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
    const buf = await r.arrayBuffer(); const ct = new Uint8Array(buf); const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain; try { plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12)); } catch { document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed</div>'; return; }
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
    document.getElementById("storage-files").textContent = `· ${s.file_count || 0} files · ${s.folder_count || 0} folders`;
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
  window.openBulkMove = openBulkMove;
  window.confirmBulkMove = confirmBulkMove;
}
