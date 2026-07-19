const API = "/api";
let currentUser = null;
let currentFolderId = null;
let currentFolderName = "~";
let currentView = "files";
let versionFileId = null;
let folderMap = {};   // id -> {id, name, parent_id}

// ── UTILS ───────────────────────────────────────────────────────────────
function toast(msg, type = "ok") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = `toast ${type} show`;
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.className = "toast", 2800);
}

function fmt(bytes) {
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1e6) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1e9) return (bytes / 1e6).toFixed(1) + " MB";
  return (bytes / 1e9).toFixed(2) + " GB";
}

function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
  });
}

function fmtLogDate(ts) {
  return new Date(ts).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit"
  });
}

// ── AUTH ────────────────────────────────────────────────────────────────
async function doLogin() {
  const username = document.getElementById("l-user").value.trim();
  const password = document.getElementById("l-pass").value;
  const r = await fetch(API + "/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, password})
  });
  if (r.ok) {
    currentUser = await r.json();
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("app").classList.add("visible");
    document.getElementById("topbar-user").textContent = currentUser.username;
    document.getElementById("topbar-role").textContent = currentUser.role;
    if (currentUser.role !== "org_admin" && currentUser.role !== "master_admin") {
      document.getElementById("nav-trash").style.display = "none";
      document.getElementById("nav-logs").style.display  = "none";
      document.getElementById("nav-users").style.display = "none";
      document.getElementById("nav-versions-all").style.display = "none";
      document.getElementById("nav-backup").style.display = "none";
    }
    await loadFolders();
    refreshFiles();
  } else {
    document.getElementById("login-err").textContent = "Invalid credentials";
  }
}

document.addEventListener("keydown", e => {
  if (e.key === "Enter" && document.getElementById("login-screen").style.display !== "none") {
    doLogin();
  }
});

async function logout() {
  await fetch(API + "/logout", {method: "POST"});
  location.reload();
}

// ── VIEW SWITCHING ──────────────────────────────────────────────────────
function showView(name) {
  currentView = name;
  const views = ["files","versions","versions-all","trash","logs","users","backup"];
  views.forEach(v => {
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
  const navs = ["files","trash","logs","users","versions-all","backup"];
  navs.forEach(v => {
    document.getElementById(`nav-${v}`)?.classList.toggle("active", v === name);
  });
  if (name === "logs")         loadLogs();
  if (name === "trash")        loadTrash();
  if (name === "users")        loadUserManagement();
  if (name === "versions-all") loadAllVersions();
  if (name === "backup")       loadBackups();
  const panel = document.getElementById(`view-${name}`);
  if (panel) revealOnScroll(panel);
}

// ── BREADCRUMB ──────────────────────────────────────────────────────────
function buildPath(folderId) {
  if (!folderId) return "~/";
  const parts = [];
  let cur = folderId;
  while (cur && folderMap[cur]) {
    parts.unshift(folderMap[cur].name);
    cur = folderMap[cur].parent_id;
  }
  return "~/" + parts.join("/") + "/";
}

function updateBreadcrumb() {
  const path = buildPath(currentFolderId);
  const bc = document.getElementById("breadcrumb");
  const idx = path.lastIndexOf("/", path.length - 2);
  if (idx <= 0) {
    bc.innerHTML = `<span>${path}</span>`;
  } else {
    bc.innerHTML = `${path.slice(0, idx + 1)}<span>${path.slice(idx + 1)}</span>`;
  }
}

// ── FOLDERS ─────────────────────────────────────────────────────────────
async function loadFolders() {
  const r = await fetch(API + "/folders");
  if (!r.ok) return;
  const folders = await r.json();
  folderMap = {};
  folders.forEach(f => { folderMap[f.id] = f; });
  renderFolderTree(folders);
}

function renderFolderTree(folders) {
  const tree = document.getElementById("folder-tree");
  tree.innerHTML = "";
  const roots = folders.filter(f => !f.parent_id);
  const children = {};
  folders.filter(f => f.parent_id).forEach(f => {
    (children[f.parent_id] = children[f.parent_id] || []).push(f);
  });
  roots.forEach(f => tree.appendChild(makeFolderNode(f, children, 0)));
}

function makeFolderNode(folder, children, depth) {
  const wrap = document.createElement("div");
  const item = document.createElement("div");
  item.className = "sidebar-item" + (currentFolderId === folder.id ? " active" : "");
  item.style.paddingLeft = (8 + depth * 12) + "px";
  item.innerHTML = `<span class="icon">📁</span> <span class="folder-label">${folder.name}</span>`;
  item.onclick = () => navigateFolder(folder.id, folder.name);
  wrap.appendChild(item);
  if (children[folder.id]) {
    children[folder.id].forEach(c => wrap.appendChild(makeFolderNode(c, children, depth + 1)));
  }
  if (currentUser && currentUser.role !== "read_only") {
    const add = document.createElement("div");
    add.className = "folder-add";
    add.style.paddingLeft = (8 + (depth + 1) * 12) + "px";
    add.innerHTML = `<span>＋</span> subfolder`;
    add.onclick = (e) => { e.stopPropagation(); promptNewFolder(folder.id); };
    wrap.appendChild(add);
  }
  if (currentUser && (currentUser.role === "org_admin" || currentUser.role === "master_admin")) {
    const del = document.createElement("div");
    del.className = "folder-delete";
    del.style.paddingLeft = (8 + (depth + 1) * 12) + "px";
    del.innerHTML = `<span>🗑</span> delete`;
    del.onclick = (e) => { e.stopPropagation(); deleteFolder(folder.id, folder.name); };
    wrap.appendChild(del);
  }
  return wrap;
}

function navigateFolder(id, name) {
  currentFolderId = id;
  currentFolderName = name;
  document.getElementById("folder-title").textContent = name;
  updateBreadcrumb();
  document.querySelectorAll(".sidebar-item").forEach(el => el.classList.remove("active"));
  const rootItem = document.getElementById("folder-root");
  if (id === null) {
    rootItem.classList.add("active");
    document.getElementById("nav-files").classList.add("active");
  } else {
    document.getElementById("nav-files").classList.add("active");
  }
  showSkeleton();
  loadFolders();
  showView("files");
  refreshFiles();
}

async function promptNewFolder(parentId) {
  const name = prompt("Folder name:");
  if (!name) return;
  const r = await fetch(API + "/folders", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, parent_id: parentId})
  });
  if (r.ok) { await loadFolders(); toast("Folder created"); }
  else { const d = await r.json().catch(() => ({})); toast(d.error || "Could not create folder", "err"); }
}

async function deleteFolder(folderId, folderName) {
  if (!confirm(`Delete folder "${folderName}"? It must be empty (no files or subfolders).`)) return;
  const r = await fetch(`${API}/folders/${folderId}`, {method: "DELETE"});
  if (r.ok) {
    if (currentFolderId === folderId) {
      currentFolderId = null;
      currentFolderName = "~";
      document.getElementById("folder-title").textContent = "Root";
      updateBreadcrumb();
    }
    await loadFolders();
    refreshFiles();
    toast(`Folder "${folderName}" deleted`);
  } else {
    const d = await r.json().catch(() => ({}));
    toast(d.error || "Could not delete folder", "err");
  }
}

// ── ENCRYPTION ──────────────────────────────────────────────────────────
async function deriveKey(passphrase) {
  const enc = new TextEncoder();
  const km = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: enc.encode("teamvault-fixed-salt"), iterations: 200000, hash: "SHA-256" },
    km, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]
  );
}

async function encryptFile(file, passphrase) {
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, await file.arrayBuffer());
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv); out.set(new Uint8Array(ct), 12);
  return new Blob([out]);
}

async function sha256Hex(blob) {
  const h = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(h)).map(b => b.toString(16).padStart(2,"0")).join("");
}

// ── UPLOAD ──────────────────────────────────────────────────────────────
async function uploadFiles(triggerEl) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) { toast("Enter team passphrase first", "err"); return; }

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
  results.forEach(r => r.status === "fulfilled" ? okCount++ : errCount++);

  area.classList.remove("visible");
  usedInput.value = "";
  refreshFiles();
  const msg = okCount ? `Uploaded ${okCount} file(s)` : "";
  const errMsg = errCount ? `${errCount} failed` : "";
  toast([msg, errMsg].filter(Boolean).join(", "), errCount ? (okCount ? "ok" : "err") : "ok");
}

function fmtSpeed(bytesPerSec) {
  if (bytesPerSec < 1000) return bytesPerSec.toFixed(0) + " B/s";
  if (bytesPerSec < 1e6) return (bytesPerSec / 1000).toFixed(1) + " KB/s";
  return (bytesPerSec / 1e6).toFixed(1) + " MB/s";
}

async function uploadOne(file, encBlob, onProgress) {
    const sha256 = await sha256Hex(encBlob);
    const div = document.createElement("div");
    div.className = "upload-item";
    const pid = "p_" + Math.random().toString(36).slice(2);
    const eid = "e_" + Math.random().toString(36).slice(2);
    div.innerHTML = `<div class="upload-item-name">${file.name} <span class="file-size" style="color:var(--muted);font-size:11px">${fmt(encBlob.size)}</span></div>
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
        document.getElementById(pid).style.width = pct + "%";
        const elapsed = (Date.now() - t0) / 1000;
        const speed = e.loaded / Math.max(elapsed, .01);
        const eta = Math.round((e.total - e.loaded) / Math.max(speed, 1));
        document.getElementById(eid).textContent =
          `${pct}% · ${fmtSpeed(speed)} · ETA ${eta}s`;
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
      fd.append("folder_id", currentFolderId || "");
      fd.append("sha256", sha256);
      xhr.send(fd);
    });
}

// ── FILE LIST ───────────────────────────────────────────────────────────
async function refreshFiles() {
  const fid = currentFolderId !== null ? `folder_id=${currentFolderId}` : "folder_id=";
  const r = await fetch(`${API}/files?${fid}`);
  if (!r.ok) return;
  const files = await r.json();
  const tbody = document.getElementById("file-tbody");
  const empty = document.getElementById("empty-files");
  tbody.innerHTML = "";
  hideSkeleton();
  if (!files.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const v = f.current_version;
    const tr = document.createElement("tr");
    tr.style.animationDelay = `${i * 30}ms`;
    tr.className = "row-enter";
    tr.innerHTML = `
      <td><span class="file-name">${f.name}</span></td>
      <td><span class="file-meta">${v ? fmt(v.size_bytes) : "—"}</span></td>
      <td><span class="version-badge">v${v ? v.version_number : "—"}</span></td>
      <td><span class="file-meta">${v ? (v.uploaded_by_name || "—") : "—"}</span></td>
      <td><span class="file-meta">${v ? fmtDate(v.uploaded_at) : "—"}</span></td>
      <td>
        <div class="action-row">
          <button class="btn-sm" onclick="previewFile('${f.id}','${f.name.replace(/'/g,"\\'")}','${v ? v.size_bytes : 0}')" title="Preview">👁</button>
          <button class="btn-sm" onclick="shareFile('${f.id}','${f.name.replace(/'/g,"\\'")}')">Share</button>
          <button class="btn-sm" onclick="emailFile('${f.id}','${f.name.replace(/'/g,"\\'")}')">Email</button>
          <button class="btn-sm" onclick="editFile('${f.id}','${f.name.replace(/'/g,"\\'")}','${v ? v.size_bytes : 0}')">Edit</button>
          <button class="btn-sm" onclick="downloadFile('${f.id}','${f.name.replace(/'/g,"\\'")}')">↓</button>
          <button class="btn-sm" onclick="openVersions('${f.id}','${f.name.replace(/'/g,"\\'")}')">History</button>
          ${currentUser?.role === "org_admin" || currentUser?.role === "master_admin"
            ? `<button class="btn-sm danger" onclick="deleteFile('${f.id}')">Delete</button>`
            : ""}
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
}

async function downloadFile(fileId, filename) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) { toast("Enter team passphrase first", "err"); return; }

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
    toast("Decryption failed — wrong passphrase?", "err"); return;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([plain]));
  a.download = filename; a.click();
  toast(`Downloaded ${filename}`);
}

async function deleteFile(fileId) {
  if (!confirm("Move this file to trash?")) return;
  const r = await fetch(`${API}/files/${fileId}`, {method: "DELETE"});
  if (r.ok) { refreshFiles(); toast("Moved to trash"); updateTrashCount(); }
  else {
    const d = await r.json().catch(() => ({}));
    toast(d.error || "Delete failed", "err");
  }
}

// ── VERSION HISTORY ─────────────────────────────────────────────────────
async function openVersions(fileId, filename) {
  versionFileId = fileId;
  document.getElementById("versions-filename").textContent = filename;
  document.getElementById("versions-subtitle").textContent = `file id #${fileId}`;
  showView("versions");
  await loadVersions(fileId);
}

async function loadVersions(fileId) {
  const r = await fetch(`${API}/files/${fileId}/versions`);
  if (!r.ok) return;
  const versions = await r.json();
  const list = document.getElementById("version-list");
  list.innerHTML = "";

  // Compute size diffs between consecutive versions
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
    // Use the fixed index for display order (DESC from API)
    const card = document.createElement("div");
    card.className = "version-card" + (v.is_current ? " current" : "");
    card.innerHTML = `
      <div class="version-no">v${v.version_number}</div>
      <div class="version-info">
        <div class="size">${fmt(v.size_bytes)} ${changeHtml}</div>
        <div class="who">by ${v.uploaded_by_name || "—"} · ${fmtDate(v.uploaded_at)}</div>
        <div class="version-sha">${v.sha256}</div>
      </div>
      <div class="action-row">
        ${v.is_current
          ? `<span class="current-pill">Current</span>`
          : (currentUser?.role !== "read_only"
              ? `<button class="btn-sm" onclick="restoreVersion('${fileId}',${v.version_number})">↩ Restore</button>`
              : "")}
      </div>`;
    list.appendChild(card);
  }
}

async function restoreVersion(fileId, versionNo) {
  const r = await fetch(`${API}/files/${fileId}/restore/${versionNo}`, {method: "POST"});
  if (r.ok) { await loadVersions(fileId); toast(`Restored to v${versionNo}`); }
  else toast("Restore failed", "err");
}

// ── ALL VERSIONS ─────────────────────────────────────────────────────────
async function loadAllVersions() {
  const r = await fetch(API + "/versions/all");
  if (!r.ok) return;
  const versions = await r.json();
  const list = document.getElementById("versions-all-list");
  const empty = document.getElementById("empty-versions-all");
  list.innerHTML = "";
  if (!versions.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (const v of versions) {
    const card = document.createElement("div");
    card.className = "version-card" + (v.is_current ? " current" : "");
    card.innerHTML = `
      <div class="version-no">v${v.version_number}</div>
      <div class="version-info">
        <div class="size"><strong>${v.filename}</strong> · ${fmt(v.size_bytes)}</div>
        <div class="who">by ${v.uploaded_by_name || "—"} · ${fmtDate(v.uploaded_at)}</div>
        <div class="version-sha">${v.sha256 || "—"}</div>
      </div>
      <div class="action-row" style="flex-shrink:0">
        ${v.is_current ? `<span class="current-pill">Current</span>` : ""}
        <button class="btn-sm" onclick="openVersions('${v.file_id}','${(v.filename||'').replace(/'/g,"\\'")}')">Open</button>
      </div>`;
    list.appendChild(card);
  }
}

// ── BACKUP ────────────────────────────────────────────────────────────────
async function loadBackups() {
  const r = await fetch(API + "/backup/list");
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    if (d.error) toast(d.error, "err");
    return;
  }
  const backups = await r.json();
  const list = document.getElementById("backup-list");
  const empty = document.getElementById("empty-backup");
  list.innerHTML = "";
  if (!backups.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (const b of backups) {
    const card = document.createElement("div");
    card.className = "trash-card";
    const dt = b.created_at ? fmtDate(b.created_at) : "—";
    card.innerHTML = `
      <span style="flex:1">
        <strong style="font-family:var(--mono);font-size:12px">${escapeHtml(b.name)}</strong>
        <span style="font-size:12px;color:var(--muted);margin-left:10px">${fmt(b.size_bytes)} · ${dt}</span>
      </span>
      <div class="action-row">
        <button class="btn-sm" onclick="downloadBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↓ Download</button>
        <button class="btn-sm" onclick="restoreBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↩ Restore</button>
        <button class="btn-sm danger" onclick="deleteBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">🗑</button>
      </div>`;
    list.appendChild(card);
  }
}

async function createBackup() {
  const btn = document.querySelector("#view-backup .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
  const r = await fetch(API + "/backup/create", {method: "POST"});
  if (btn) { btn.disabled = false; btn.textContent = "＋ Create backup"; }
  if (r.ok) { loadBackups(); toast("Backup created"); }
  else { const d = await r.json().catch(() => ({})); toast(d.error || "Backup failed", "err"); }
}

async function restoreBackup(name) {
  if (!confirm(`Restore from this backup? This will REPLACE all current org data.`)) return;
  const r = await fetch(`${API}/backup/restore/${encodeURIComponent(name)}`, {method: "POST"});
  if (r.ok) { toast("Restored — reloading…"); setTimeout(() => location.reload(), 1000); }
  else { const d = await r.json().catch(() => ({})); toast(d.error || "Restore failed", "err"); }
}

async function downloadBackup(name) {
  window.open(`${API}/backup/download/${encodeURIComponent(name)}`, "_blank");
}

async function deleteBackup(name) {
  if (!confirm(`Delete backup "${name}"?`)) return;
  const r = await fetch(`${API}/backup/delete/${encodeURIComponent(name)}`, {method: "DELETE"});
  if (r.ok) { loadBackups(); toast("Backup deleted"); }
  else { const d = await r.json().catch(() => ({})); toast(d.error || "Delete failed", "err"); }
}

// ── TRASH ────────────────────────────────────────────────────────────────
async function loadTrash() {
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
      <span class="trash-name">${f.name}</span>
      <span class="trash-who">deleted by ${f.deleted_by_name || "—"} · ${fmtDate(f.deleted_at)}</span>
      <div class="action-row">
        <button class="btn-sm" onclick="restoreFromTrash('${f.id}')">↩ Restore</button>
        <button class="btn-sm danger" onclick="hardDelete('${f.id}')">Destroy</button>
      </div>`;
    list.appendChild(card);
  }
}

async function updateTrashCount() {
  if (currentUser?.role !== "org_admin" && currentUser?.role !== "master_admin") return;
  const r = await fetch(API + "/trash");
  if (r.ok) {
    const items = await r.json();
    document.getElementById("trash-count").textContent = items.length || "—";
  }
}

async function restoreFromTrash(fileId) {
  const r = await fetch(`${API}/trash/${fileId}/restore`, {method: "POST"});
  if (r.ok) { loadTrash(); refreshFiles(); toast("File restored"); }
  else toast("Restore failed", "err");
}

async function hardDelete(fileId) {
  if (!confirm("Permanently destroy this file and all versions? This cannot be undone.")) return;
  const r = await fetch(`${API}/trash/${fileId}`, {method: "DELETE"});
  if (r.ok) { loadTrash(); toast("File permanently destroyed"); }
  else toast("Hard delete failed", "err");
}

// ── LOGS ─────────────────────────────────────────────────────────────────
async function loadLogs() {
  const r = await fetch(API + "/logs?limit=300");
  if (!r.ok) return;
  const logs = await r.json();
  const list = document.getElementById("log-list");
  list.innerHTML = "";
  for (const l of logs) {
    const row = document.createElement("div");
    row.className = "log-row";
    row.innerHTML = `
      <span class="log-ts">${fmtLogDate(l.ts)}</span>
      <span class="log-user">${l.username || "—"}</span>
      <span class="log-action log-${l.action}">${l.action}</span>
      <span class="log-detail">${[l.target, l.detail].filter(Boolean).join(" · ")}</span>`;
    list.appendChild(row);
  }
}

// ── USER MANAGEMENT ────────────────────────────────────────────────────
let _umUsers = [];
let _umFolders = [];
let _umEditUserId = null;
let _umPermsUserId = null;
let _currentTeamTab = "users";

function switchTeamTab(tab) {
  _currentTeamTab = tab;
  document.getElementById("team-tab-users").style.display = tab === "users" ? "" : "none";
  document.getElementById("team-tab-folders").style.display = tab === "folders" ? "" : "none";
  document.getElementById("um-tab-users").classList.toggle("active", tab === "users");
  document.getElementById("um-tab-folders").classList.toggle("active", tab === "folders");
  if (tab === "folders") loadFolderAccess();
}

async function loadUserManagement() {
  const [usersRes, statsRes, foldersRes] = await Promise.all([
    fetch(API + "/users"),
    fetch(API + "/users/stats"),
    fetch(API + "/folders"),
  ]);
  if (usersRes.ok) _umUsers = await usersRes.json();
  if (foldersRes.ok) _umFolders = await foldersRes.json();
  if (statsRes.ok) {
    const stats = await statsRes.json();
    renderUserStats(stats);
  }
  renderUserTable(_umUsers);
  if (_currentTeamTab === "folders") loadFolderAccess();
}

function renderUserStats(stats) {
  const el = document.getElementById("um-stats");
  const roles = stats.by_role || {};
  el.innerHTML = `
    <div class="um-stat-card">
      <div class="um-stat-num">${stats.total || 0}</div>
      <div class="um-stat-label">Total Users</div>
    </div>
    <div class="um-stat-card accent">
      <div class="um-stat-num">${roles.org_admin || 0}</div>
      <div class="um-stat-label">Admins</div>
    </div>
    <div class="um-stat-card blue">
      <div class="um-stat-num">${roles.read_write || 0}</div>
      <div class="um-stat-label">Read/Write</div>
    </div>
    <div class="um-stat-card muted">
      <div class="um-stat-num">${roles.read_only || 0}</div>
      <div class="um-stat-label">Read Only</div>
    </div>
    <div class="um-stat-card green">
      <div class="um-stat-num">${stats.active_this_week || 0}</div>
      <div class="um-stat-label">Active (7d)</div>
    </div>
    <div class="um-stat-card violet">
      <div class="um-stat-num">${stats.joined_this_month || 0}</div>
      <div class="um-stat-label">New (30d)</div>
    </div>`;
}

function renderUserTable(users) {
  const tbody = document.getElementById("um-user-tbody");
  const empty = document.getElementById("um-empty");
  tbody.innerHTML = "";
  if (!users.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (let i = 0; i < users.length; i++) {
    const u = users[i];
    const isSelf = u.id === currentUser?.id;
    const roleColors = {org_admin:"var(--warn)", read_write:"#60a5fa", read_only:"var(--muted)"};
    const tr = document.createElement("tr");
    tr.className = "row-enter";
    tr.style.animationDelay = `${i * 30}ms`;
    tr.innerHTML = `
      <td>
        <div class="um-user-cell">
          <div class="um-avatar" style="background:${isSelf ? 'linear-gradient(135deg,var(--accent),var(--violet))' : 'var(--glass-bg)'}">${u.username.charAt(0).toUpperCase()}</div>
          <div>
            <div class="um-user-name">${escapeHtml(u.username)}${isSelf ? ' <span class="um-you-badge">you</span>' : ''}</div>
          </div>
        </div>
      </td>
      <td>
        <span class="role-pill ${u.role}" style="cursor:default">${u.role.replace("_"," ")}</span>
      </td>
      <td><span class="file-meta">${fmtDate(u.created_at)}</span></td>
      <td><span class="file-meta" id="um-activity-${u.id}">—</span></td>
      <td>
        <div class="action-row">
          <button class="btn-sm" onclick="showEditUserModal('${u.id}','${escapeHtml(u.username)}','${u.role}')" title="Edit user">Edit</button>
          <button class="btn-sm" onclick="showUserActivity('${u.id}','${escapeHtml(u.username)}')" title="View activity">Activity</button>
          <button class="btn-sm" onclick="showUserPermissions('${u.id}','${escapeHtml(u.username)}')" title="Manage permissions">Perms</button>
          ${!isSelf ? `<button class="btn-sm danger" onclick="deleteUser('${u.id}','${escapeHtml(u.username)}')" title="Remove user">Remove</button>` : ''}
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
  loadUserActivitySummaries(users);
}

async function loadUserActivitySummaries(users) {
  const weekAgo = new Date(Date.now() - 7*86400000).toISOString();
  for (const u of users) {
    try {
      const r = await fetch(`${API}/users/${u.id}/activity?limit=50`);
      if (!r.ok) continue;
      const logs = await r.json();
      const recent = logs.filter(l => l.ts >= weekAgo);
      const el = document.getElementById(`um-activity-${u.id}`);
      if (el) {
        if (recent.length > 0) {
          el.innerHTML = `<span style="color:var(--success)">${recent.length} actions (7d)</span>`;
        } else {
          el.innerHTML = `<span style="color:var(--muted)">No recent activity</span>`;
        }
      }
    } catch {}
  }
}

function filterUsers() {
  const q = document.getElementById("um-search").value.toLowerCase();
  const roleFilter = document.getElementById("um-role-filter").value;
  let filtered = _umUsers;
  if (q) filtered = filtered.filter(u => u.username.toLowerCase().includes(q));
  if (roleFilter) filtered = filtered.filter(u => u.role === roleFilter);
  renderUserTable(filtered);
}

function showAddUserModal() {
  document.getElementById("au-username").value = "";
  document.getElementById("au-password").value = "";
  document.getElementById("au-role").value = "read_write";
  openModal("add-user-modal");
  setTimeout(() => document.getElementById("au-username").focus(), 100);
}

async function addUser() {
  const username = document.getElementById("au-username").value.trim();
  const password = document.getElementById("au-password").value;
  const role = document.getElementById("au-role").value;
  if (!username) { toast("Username required", "err"); return; }
  if (!password || password.length < 6) { toast("Password must be at least 6 characters", "err"); return; }
  const r = await fetch(API + "/users", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, password, role})
  });
  if (r.ok) {
    closeModal("add-user-modal");
    loadUserManagement();
    toast(`Created user ${username}`);
  } else {
    const d = await r.json();
    toast(d.error || "Failed to create user", "err");
  }
}

function showEditUserModal(userId, username, role) {
  _umEditUserId = userId;
  document.getElementById("eu-username-display").textContent = username;
  document.getElementById("eu-username").value = username;
  document.getElementById("eu-password").value = "";
  document.getElementById("eu-role").value = role;
  openModal("edit-user-modal");
}

async function saveUserEdit() {
  if (!_umEditUserId) return;
  const username = document.getElementById("eu-username").value.trim();
  const password = document.getElementById("eu-password").value;
  const role = document.getElementById("eu-role").value;
  const body = {role};
  if (username) body.username = username;
  if (password) body.password = password;
  const r = await fetch(`${API}/users/${_umEditUserId}`, {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  if (r.ok) {
    closeModal("edit-user-modal");
    loadUserManagement();
    toast("User updated");
  } else {
    const d = await r.json();
    toast(d.error || "Failed to update", "err");
  }
}

async function deleteUser(id, name) {
  if (!confirm(`Remove "${name}" from the team? This will also remove their folder permissions.`)) return;
  const r = await fetch(`${API}/users/${id}`, {method: "DELETE"});
  if (r.ok) { loadUserManagement(); toast(`Removed ${name}`); }
  else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

async function showUserActivity(userId, username) {
  document.getElementById("ua-username-display").textContent = username;
  const list = document.getElementById("user-activity-list");
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">Loading…</div>';
  openModal("user-activity-modal");
  const r = await fetch(`${API}/users/${userId}/activity?limit=100`);
  if (!r.ok) { list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--danger)">Failed to load</div>'; return; }
  const logs = await r.json();
  list.innerHTML = "";
  if (!logs.length) {
    list.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No activity recorded yet.</div>';
    return;
  }
  for (const l of logs) {
    const row = document.createElement("div");
    row.className = "log-row";
    row.innerHTML = `
      <span class="log-ts">${fmtLogDate(l.ts)}</span>
      <span class="log-action log-${l.action}">${l.action}</span>
      <span class="log-detail">${[l.target, l.detail].filter(Boolean).join(" · ")}</span>`;
    list.appendChild(row);
  }
}

async function showUserPermissions(userId, username) {
  _umPermsUserId = userId;
  document.getElementById("up-username-display").textContent = username;
  openModal("user-perms-modal");
  const folderSelect = document.getElementById("up-folder");
  folderSelect.innerHTML = '<option value="">Root (all folders)</option>';
  for (const f of _umFolders) {
    folderSelect.innerHTML += `<option value="${f.id}">${escapeHtml(f.name)}</option>`;
  }
  await loadUserPermissionsList(userId);
}

async function loadUserPermissionsList(userId) {
  const list = document.getElementById("user-perms-list");
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">Loading…</div>';
  const r = await fetch(`${API}/users/${userId}/permissions`);
  if (!r.ok) { list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--danger)">Failed to load</div>'; return; }
  const perms = await r.json();
  list.innerHTML = "";
  if (!perms.length) {
    list.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted)">No folder-specific permissions. User inherits org-wide role.</div>';
    return;
  }
  for (const p of perms) {
    const row = document.createElement("div");
    row.className = "um-perm-row";
    row.innerHTML = `
      <span class="um-perm-folder">📁 ${escapeHtml(p.folder_name)}</span>
      <span class="role-pill ${p.permission_level}" style="font-size:10px">${p.permission_level.replace("_"," ")}</span>
      <button class="btn-sm danger" onclick="removeUserPermission('${_umPermsUserId}','${p.id}')" style="font-size:10px">✕ Remove</button>`;
    list.appendChild(row);
  }
}

async function addUserPermission() {
  if (!_umPermsUserId) return;
  const folderId = document.getElementById("up-folder").value || null;
  const level = document.getElementById("up-level").value;
  const r = await fetch(`${API}/users/${_umPermsUserId}/permissions`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({folder_id: folderId, permission_level: level})
  });
  if (r.ok) {
    loadUserPermissionsList(_umPermsUserId);
    toast("Permission added");
  } else {
    const d = await r.json();
    toast(d.error || "Failed", "err");
  }
}

async function removeUserPermission(userId, permId) {
  const r = await fetch(`${API}/users/${userId}/permissions/${permId}`, {method: "DELETE"});
  if (r.ok) { loadUserPermissionsList(userId); toast("Permission removed"); }
}

// ── FOLDER ACCESS MANAGEMENT ───────────────────────────────────────────
let _faFolders = [];
let _faGrantFolderId = null;

async function loadFolderAccess() {
  const [foldersRes, usersRes] = await Promise.all([
    fetch(API + "/folders/permissions/all"),
    fetch(API + "/folders/all-users"),
  ]);
  if (foldersRes.ok) _faFolders = await foldersRes.json();
  const users = usersRes.ok ? await usersRes.json() : [];
  renderFolderAccessList(_faFolders, users);
}

function renderFolderAccessList(folders, users) {
  const list = document.getElementById("fa-folder-list");
  const empty = document.getElementById("fa-empty");
  list.innerHTML = "";
  if (!folders.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (let i = 0; i < folders.length; i++) {
    const f = folders[i];
    const card = document.createElement("div");
    card.className = "fa-card";
    card.style.animationDelay = `${i * 40}ms`;
    const depth = f.parent_id ? 1 : 0;
    const userCountBadge = f.user_count > 0
      ? `<span class="fa-user-count">${f.user_count} user${f.user_count > 1 ? "s" : ""}</span>`
      : `<span class="fa-user-count fa-user-count-empty">No restrictions</span>`;
    card.innerHTML = `
      <div class="fa-card-header" style="padding-left:${12 + depth * 20}px">
        <span class="fa-folder-icon">${depth ? "└📁" : "📁"}</span>
        <span class="fa-folder-name">${escapeHtml(f.name)}</span>
        ${userCountBadge}
        <button class="btn-sm active" onclick="showGrantAccess('${f.id}','${escapeHtml(f.name)}')" style="font-size:11px">+ Grant Access</button>
      </div>
      <div class="fa-perm-list" id="fa-perms-${f.id}">
        ${f.permissions.length === 0
          ? `<div class="fa-perm-empty">All org members can access (using their org-wide role)</div>`
          : f.permissions.map(p => `
            <div class="fa-perm-row">
              <span class="fa-perm-user">👤 ${escapeHtml(p.username)}</span>
              <span class="role-pill ${p.permission_level}" style="font-size:10px">${p.permission_level.replace("_"," ")}</span>
              <button class="btn-sm danger" onclick="revokeFolderAccess('${f.id}','${p.id}','${escapeHtml(p.username)}')" style="font-size:10px">Revoke</button>
            </div>
          `).join("")
        }
      </div>`;
    list.appendChild(card);
  }
  window._faAllUsers = users;
}

function showGrantAccess(folderId, folderName) {
  _faGrantFolderId = folderId;
  document.getElementById("ga-folder-name").textContent = folderName;
  const select = document.getElementById("ga-user");
  select.innerHTML = '<option value="">Choose a user…</option>';
  const users = window._faAllUsers || [];
  for (const u of users) {
    select.innerHTML += `<option value="${u.id}">${escapeHtml(u.username)} (${u.role.replace("_"," ")})</option>`;
  }
  document.getElementById("ga-level").value = "read_only";
  openModal("grant-access-modal");
}

async function grantFolderAccess() {
  if (!_faGrantFolderId) return;
  const userId = document.getElementById("ga-user").value;
  const level = document.getElementById("ga-level").value;
  if (!userId) { toast("Select a user", "err"); return; }
  const r = await fetch(`${API}/folders/${_faGrantFolderId}/permissions`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({user_id: userId, permission_level: level})
  });
  if (r.ok) {
    closeModal("grant-access-modal");
    loadFolderAccess();
    toast("Access granted");
  } else {
    const d = await r.json();
    toast(d.error || "Failed", "err");
  }
}

async function revokeFolderAccess(folderId, permId, username) {
  if (!confirm(`Revoke ${username}'s access to this folder?`)) return;
  const r = await fetch(`${API}/folders/${folderId}/permissions/${permId}`, {method: "DELETE"});
  if (r.ok) { loadFolderAccess(); toast("Access revoked"); }
  else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

// ── SHARING ────────────────────────────────────────────────────────────
let _shareFileId = null;
let _shareFileName = "";

function shareFile(fileId, filename) {
  _shareFileId = fileId;
  _shareFileName = filename;
  document.getElementById("share-link-input").value = "";
  document.getElementById("share-expiry").value = 7;
  document.getElementById("share-password").value = "";
  document.getElementById("share-existing-links").innerHTML = "";
  openModal("share-modal");
  loadExistingShares(fileId);
}

async function loadExistingShares(fileId) {
  const r = await fetch(`${API}/files/${fileId}/shares`);
  if (!r.ok) return;
  const shares = await r.json();
  const container = document.getElementById("share-existing-links");
  container.innerHTML = "";
  if (!shares.length) return;
  const title = document.createElement("div");
  title.style.cssText = "font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin:12px 0 6px";
  title.textContent = "Existing Links";
  container.appendChild(title);
  for (const s of shares) {
    const row = document.createElement("div");
    row.className = "share-link-item";
    const exp = s.expires_at ? fmtDate(s.expires_at) : "never";
    row.innerHTML = `
      <span style="flex:1;font-family:var(--mono);font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${window.location.origin}/shared/${s.token}</span>
      <span style="font-size:11px;color:var(--muted);min-width:60px">${s.download_count || 0} DLs</span>
      <span style="font-size:11px;color:var(--muted);min-width:80px">${exp}</span>
      <button class="btn-sm danger" onclick="deleteShare('${s.id}','${fileId}')" style="font-size:10px">✕</button>`;
    container.appendChild(row);
  }
}

async function createShareLink() {
  if (!_shareFileId) return;
  const days = document.getElementById("share-expiry").value;
  const password = document.getElementById("share-password").value;
  const r = await fetch(`${API}/files/${_shareFileId}/share`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expires_days: parseInt(days) || 7, password})
  });
  if (r.ok) {
    const d = await r.json();
    const url = `${window.location.origin}/shared/${d.token}`;
    document.getElementById("share-link-input").value = url;
    document.getElementById("share-expires").textContent = d.expires_at ? fmtDate(d.expires_at) : "never";
    toast("Share link created");
    loadExistingShares(_shareFileId);
  } else {
    const d = await r.json();
    toast(d.error || "Failed to create link", "err");
  }
}

function copyShareLink() {
  const input = document.getElementById("share-link-input");
  if (!input.value) return;
  navigator.clipboard.writeText(input.value).then(() => toast("Link copied!")).catch(() => {
    input.select();
    document.execCommand("copy");
    toast("Link copied!");
  });
}

async function deleteShare(shareId, fileId) {
  const r = await fetch(`${API}/files/${fileId}/shares/${shareId}`, {method: "DELETE"});
  if (r.ok) { loadExistingShares(fileId); toast("Link removed"); }
}

// ── EMAIL SHARING ──────────────────────────────────────────────────────
let _emailFileId = null;
let _emailFileName = "";

function emailFile(fileId, filename) {
  _emailFileId = fileId;
  _emailFileName = filename;
  document.getElementById("email-recipients").value = "";
  document.getElementById("email-message").value = "";
  openModal("email-modal");
}

async function sendEmail(event) {
  if (!_emailFileId) return;
  const recipients = document.getElementById("email-recipients").value.trim();
  const message = document.getElementById("email-message").value;
  if (!recipients) { toast("Enter at least one recipient", "err"); return; }
  const btn = event.target;
  btn.disabled = true; btn.textContent = "Sending…";
  const r = await fetch(`${API}/files/${_emailFileId}/email`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({recipients, message})
  });
  btn.disabled = false; btn.textContent = "Send";
  if (r.ok) { closeModal("email-modal"); toast("Email sent!"); }
  else { const d = await r.json(); toast(d.error || "Failed to send", "err"); }
}

// ── PREVIEW ────────────────────────────────────────────────────────────
let _previewFileId = null;
let _previewFilename = "";
let _previewBlobUrl = null;

function previewFile(fileId, filename, sizeBytes) {
  _previewFileId = fileId;
  _previewFilename = filename;
  document.getElementById("preview-title").textContent = filename;
  const content = document.getElementById("preview-content");
  content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Loading preview…</div>';
  openModal("preview-modal");
  const ext = filename.split(".").pop().toLowerCase();
  const mimeMap = {
    png:"image/png", jpg:"image/jpeg", jpeg:"image/jpeg", gif:"image/gif",
    svg:"image/svg+xml", webp:"image/webp", bmp:"image/bmp",
    pdf:"application/pdf", txt:"text/plain", md:"text/markdown",
    json:"application/json", csv:"text/csv", html:"text/html",
    css:"text/css", js:"text/javascript", py:"text/x-python",
    java:"text/x-java", c:"text/x-c", cpp:"text/x-cpp", h:"text/x-c",
    xml:"text/xml", yaml:"text/yaml", yml:"text/yaml", sh:"text/x-shellscript",
    mp4:"video/mp4", webm:"video/webm",
    mp3:"audio/mpeg", wav:"audio/wav", ogg:ext==="ogg"?"audio/ogg":"video/ogg",
  };
  if (["png","jpg","jpeg","gif","svg","webp","bmp"].includes(ext)) {
    loadPreviewAsBlob(content, `<img src="" alt="${filename}" style="max-width:100%;max-height:70vh;display:block;margin:auto;border-radius:4px">`);
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
    const typeLabels = {
      doc:"Word Document (legacy)", xls:"Excel Spreadsheet (legacy)", ppt:"PowerPoint (legacy)",
      zip:"ZIP Archive", rar:"RAR Archive", "7z":"7-Zip Archive",
      tar:"TAR Archive", gz:"GZIP Archive",
    };
    content.innerHTML = `
      <div style="text-align:center;padding:60px 20px">
        <div style="font-size:64px;margin-bottom:16px">📄</div>
        <div style="font-size:18px;font-weight:600;margin-bottom:8px">${filename}</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:4px">${typeLabels[ext] || ext.toUpperCase()} file · ${fmt(parseInt(sizeBytes) || 0)}</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px">In-browser preview not available for this file type.</div>
        <button class="btn-sm active" onclick="downloadPreviewFile()">↓ Download to view</button>
      </div>`;
  }
}

function loadPreviewAsBlob(container, htmlTemplate) {
  fetch(`${API}/files/${_previewFileId}/preview`).then(r => {
    if (!r.ok) throw new Error("Preview failed");
    return r.blob();
  }).then(blob => {
    if (_previewBlobUrl) URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = URL.createObjectURL(blob);
    container.innerHTML = htmlTemplate;
    const el = container.firstElementChild;
    el.src = _previewBlobUrl;
  }).catch(err => {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Preview failed: ${err.message}</div>`;
  });
}

function loadPreviewAsText(container, ext) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Enter the team passphrase to preview text files.</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting…</div>';
  fetch(`${API}/files/${_previewFileId}/preview`).then(r => {
    if (!r.ok) throw new Error("Preview failed");
    return r.arrayBuffer();
  }).then(async buf => {
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    } catch {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed — wrong passphrase?</div>';
      return;
    }
    const text = new TextDecoder().decode(plain);
    const extMap = {md:"markdown",json:"json",js:"javascript",py:"python",html:"html",css:"css",xml:"xml",yaml:"yaml",yml:"yaml",sh:"bash",sql:"sql"};
    container.innerHTML = `<pre style="margin:0;padding:16px;font-family:var(--mono);font-size:12px;white-space:pre-wrap;word-break:break-word;color:var(--text);overflow:auto;max-height:65vh">${escapeHtml(text)}</pre>`;
  }).catch(err => {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">${err.message}</div>`;
  });
}

function escapeHtml(str) {
  return String(str ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ── WORD PREVIEW (DOCX → HTML via mammoth.js) ──────────────────────────
async function loadDocxPreview(container) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Enter the team passphrase to preview Word documents.</div>';
    return;
  }
  if (typeof mammoth === "undefined") {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Word viewer library not loaded. Check your internet connection.</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering Word document…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    } catch {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed — wrong passphrase?</div>';
      return;
    }
    const result = await mammoth.convertToHtml({arrayBuffer: plain});
    const html = result.value || '<p style="color:var(--muted)">Document is empty.</p>';
    const warnings = result.messages.filter(m => m.type === "warning");
    container.innerHTML = `
      <div class="docx-preview" style="padding:24px;font-family:'IBM Plex Sans',sans-serif;color:var(--text);font-size:14px;line-height:1.7;max-height:65vh;overflow:auto">
        ${html}
      </div>
      ${warnings.length ? `<div style="padding:8px 16px;font-size:11px;color:var(--muted);border-top:1px solid var(--glass-border)">${warnings.length} conversion warnings</div>` : ""}`;
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Word preview failed: ${err.message}</div>`;
  }
}

// ── EXCEL PREVIEW (XLSX → TABLE via SheetJS) ───────────────────────────
async function loadXlsxPreview(container) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Enter the team passphrase to preview Excel spreadsheets.</div>';
    return;
  }
  if (typeof XLSX === "undefined") {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Excel viewer library not loaded. Check your internet connection.</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering spreadsheet…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    } catch {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed — wrong passphrase?</div>';
      return;
    }
    const workbook = XLSX.read(plain, {type: "array"});
    const sheetName = workbook.SheetNames[0];
    const sheet = workbook.Sheets[sheetName];
    const html = XLSX.utils.sheet_to_html(sheet, {editable: false});
    container.innerHTML = `
      <div style="padding:4px">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--glass-border);background:rgba(0,0,0,.2)">
          <span style="font-size:12px;font-weight:600;color:var(--accent)">Sheet: ${escapeHtml(sheetName)}</span>
          ${workbook.SheetNames.length > 1 ? `<span style="font-size:11px;color:var(--muted)">(${workbook.SheetNames.length} sheets: ${workbook.SheetNames.map(s => escapeHtml(s)).join(", ")})</span>` : ""}
        </div>
        <div class="xlsx-scroll" style="overflow:auto;max-height:60vh">
          ${html}
        </div>
      </div>`;
    const style = container.querySelector(".xlsx-scroll table");
    if (style) {
      style.style.borderCollapse = "collapse";
      style.style.fontSize = "12px";
      style.style.width = "100%";
      const cells = style.querySelectorAll("td, th");
      cells.forEach(c => {
        c.style.border = "1px solid rgba(255,255,255,.06)";
        c.style.padding = "6px 10px";
        c.style.whiteSpace = "nowrap";
      });
      const ths = style.querySelectorAll("th");
      ths.forEach(th => {
        th.style.background = "rgba(0,0,0,.3)";
        th.style.fontWeight = "600";
        th.style.color = "var(--accent)";
        th.style.position = "sticky";
        th.style.top = "0";
      });
    }
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">Excel preview failed: ${err.message}</div>`;
  }
}

// ── POWERPOINT PREVIEW (PPTX → SLIDES via JSZip) ───────────────────────
async function loadPptxPreview(container) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Enter the team passphrase to preview PowerPoint presentations.</div>';
    return;
  }
  if (typeof JSZip === "undefined") {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">PPT viewer library not loaded. Check your internet connection.</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Decrypting and rendering presentation…</div>';
  try {
    const r = await fetch(`${API}/files/${_previewFileId}/preview`);
    if (!r.ok) throw new Error("Preview failed");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    } catch {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed — wrong passphrase?</div>';
      return;
    }
    const zip = await JSZip.loadAsync(plain);
    const slideFiles = [];
    zip.forEach((path, file) => {
      if (path.match(/^ppt\/slides\/slide\d+\.xml$/) && !path.endsWith("/")) {
        slideFiles.push({path, file});
      }
    });
    slideFiles.sort((a, b) => {
      const na = parseInt(a.path.match(/slide(\d+)\.xml/)[1]);
      const nb = parseInt(b.path.match(/slide(\d+)\.xml/)[1]);
      return na - nb;
    });
    if (!slideFiles.length) {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No slides found in presentation.</div>';
      return;
    }
    const slideContainer = document.createElement("div");
    slideContainer.id = "ppt-slides";
    container.innerHTML = "";
    container.appendChild(slideContainer);
    let currentSlide = 0;
    function renderSlide(idx) {
      const sf = slideFiles[idx];
      sf.file.async("string").then(xml => {
        const parser = new DOMParser();
        const doc = parser.parseFromString(xml, "application/xml");
        const texts = [];
        doc.querySelectorAll("a:t").forEach(t => {
          const text = t.textContent.trim();
          if (text) texts.push(text);
        });
        const relationsPath = sf.path.replace("ppt/slides/", "ppt/slides/_rels/slide").replace(".xml", ".xml.rels");
        const title = texts[0] || `Slide ${idx + 1}`;
        const bullets = texts.slice(1);
        slideContainer.innerHTML = `
          <div style="display:flex;flex-direction:column;align-items:center;padding:16px">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;width:100%;max-width:700px">
              <button class="btn-sm" onclick="pptNavigate(-1)" ${idx === 0 ? "disabled style='opacity:.3'" : ""}>← Prev</button>
              <span style="flex:1;text-align:center;font-size:12px;color:var(--muted)">Slide ${idx + 1} of ${slideFiles.length}</span>
              <button class="btn-sm" onclick="pptNavigate(1)" ${idx === slideFiles.length - 1 ? "disabled style='opacity:.3'" : ""}>Next →</button>
            </div>
            <div style="background:rgba(255,255,255,.95);border-radius:8px;padding:40px 32px;width:100%;max-width:700px;min-height:350px;box-shadow:0 8px 32px rgba(0,0,0,.4);color:#1a1a1a">
              <div style="font-size:22px;font-weight:700;margin-bottom:20px;color:#111">${escapeHtml(title)}</div>
              ${bullets.map(b => `<div style="font-size:14px;line-height:1.6;margin-bottom:6px;padding-left:16px;border-left:3px solid #22d3ee">${escapeHtml(b)}</div>`).join("")}
              ${!bullets.length && texts.length > 1 ? `<div style="font-size:14px;line-height:1.6;color:#333">${texts.slice(1).map(t => escapeHtml(t)).join("<br>")}</div>` : ""}
            </div>
          </div>`;
        currentSlide = idx;
      });
    }
    window.pptNavigate = function(dir) {
      const next = currentSlide + dir;
      if (next >= 0 && next < slideFiles.length) renderSlide(next);
    };
    renderSlide(0);
  } catch (err) {
    container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">PowerPoint preview failed: ${err.message}</div>`;
  }
}

function downloadPreviewFile() {
  if (!_previewFileId || !_previewFilename) return;
  downloadFile(_previewFileId, _previewFilename);
}

// ── EDIT ───────────────────────────────────────────────────────────────
let _editFileId = null;
let _editFileName = "";
let _editSizeBytes = 0;

function editFile(fileId, filename, sizeBytes) {
  _editFileId = fileId;
  _editFileName = filename;
  _editSizeBytes = parseInt(sizeBytes) || 0;
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
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    document.getElementById("edit-textarea").value = "Enter the team passphrase to edit text files.";
    return;
  }
  try {
    const r = await fetch(`${API}/files/${fileId}/preview`);
    if (!r.ok) throw new Error("Failed to load");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    document.getElementById("edit-textarea").value = new TextDecoder().decode(plain);
  } catch (err) {
    document.getElementById("edit-textarea").value = `Error loading file: ${err.message}`;
  }
}

async function saveEdit() {
  if (!_editFileId) return;
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) { toast("Enter the team passphrase first", "err"); return; }
  const text = document.getElementById("edit-textarea").value;
  const encoder = new TextEncoder();
  const plainBuf = encoder.encode(text);
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plainBuf);
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv); out.set(new Uint8Array(ct), 12);
  const blob = new Blob([out]);
  const sha = await sha256Hex(blob);
  const formData = new FormData();
  formData.append("file", blob, _editFileName);
  formData.append("filename", _editFileName);
  formData.append("folder_id", currentFolderId || "");
  formData.append("sha256", sha);
  const btn = document.querySelector("#edit-modal .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const r = await fetch(`${API}/files/upload`, {method: "POST", body: formData});
  if (btn) { btn.disabled = false; btn.textContent = "Save as new version"; }
  if (r.ok) {
    closeModal("edit-modal");
    refreshFiles();
    toast("Saved as new version");
  } else {
    const d = await r.json();
    toast(d.error || "Save failed", "err");
  }
}

function downloadEditFile() {
  if (_editFileId && _editFileName) downloadFile(_editFileId, _editFileName);
}

// ── DOCX RICH TEXT EDITOR ──────────────────────────────────────────────
let _editDocxBlob = null;
let _editDocxParagraphs = [];

async function loadDocxForEdit(fileId) {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) {
    document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">Enter the team passphrase to edit Word documents.</div>';
    return;
  }
  if (typeof mammoth === "undefined") {
    document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Word editor library not loaded.</div>';
    return;
  }
  try {
    const r = await fetch(`${API}/files/${fileId}/preview`);
    if (!r.ok) throw new Error("Failed to load");
    const buf = await r.arrayBuffer();
    const ct = new Uint8Array(buf);
    const iv = ct.slice(0, 12);
    const key = await deriveKey(passphrase);
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct.slice(12));
    } catch {
      document.getElementById("edit-rich-content").innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">Decryption failed — wrong passphrase?</div>';
      return;
    }
    _editDocxBlob = new Blob([plain]);
    const result = await mammoth.convertToHtml({arrayBuffer: plain});
    const html = result.value || "";
    const editor = document.getElementById("edit-rich-editor");
    editor.innerHTML = html || "<p><br></p>";
    document.getElementById("edit-rich-content").innerHTML = "";
    document.getElementById("edit-rich-content").appendChild(editor);
    document.getElementById("edit-rich-toolbar").style.display = "flex";
  } catch (err) {
    document.getElementById("edit-rich-content").innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger)">${err.message}</div>`;
  }
}

function richCmd(cmd, val) {
  document.execCommand(cmd, false, val || null);
  document.getElementById("edit-rich-editor").focus();
}

async function saveEditRich() {
  if (!_editFileId) return;
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) { toast("Enter the team passphrase first", "err"); return; }
  const html = document.getElementById("edit-rich-editor").innerHTML;
  const encoder = new TextEncoder();
  const plainBuf = encoder.encode(html);
  const key = await deriveKey(passphrase);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plainBuf);
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv); out.set(new Uint8Array(ct), 12);
  const blob = new Blob([out]);
  const sha = await sha256Hex(blob);
  const formData = new FormData();
  formData.append("file", blob, _editFileName);
  formData.append("filename", _editFileName);
  formData.append("folder_id", currentFolderId || "");
  formData.append("sha256", sha);
  const btn = document.querySelector("#edit-modal .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const r = await fetch(`${API}/files/upload`, {method: "POST", body: formData});
  if (btn) { btn.disabled = false; btn.textContent = "Save as new version"; }
  if (r.ok) {
    closeModal("edit-modal");
    refreshFiles();
    toast("Saved as new version");
  } else {
    const d = await r.json();
    toast(d.error || "Save failed", "err");
  }
}

// ── MODAL UTILS ────────────────────────────────────────────────────────
function openModal(id) {
  const el = document.getElementById(id);
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
function closeModal(id) {
  const el = document.getElementById(id);
  const box = el.querySelector(".modal");
  const finish = () => {
    el.style.display = "none";
    if (box) box.style.animation = "";
    el.style.animation = "";
    if (_previewBlobUrl) { URL.revokeObjectURL(_previewBlobUrl); _previewBlobUrl = null; }
  };
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !box) { finish(); return; }
  el.style.animation = "backdrop-out .25s ease both";
  box.style.animation = "modal-pop-out .25s ease both";
  setTimeout(finish, 240);
}
document.querySelectorAll(".modal-backdrop").forEach(el => {
  el.addEventListener("click", e => {
    if (e.target === el) closeModal(el.id);
  });
});

// Scroll-reveal for lists (used by panels on render)
function revealOnScroll(container) {
  const items = container.querySelectorAll(".version-card, .trash-card, .fa-card, .um-stat-card, .log-row");
  if (!items.length) return;
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach(en => {
      if (en.isIntersecting) { en.target.style.animationPlayState = "running"; io.unobserve(en.target); }
    });
  }, { threshold: .05 });
  items.forEach(it => { it.style.animationPlayState = "paused"; io.observe(it); });
}

// Ripple effect on buttons
function attachRipple() {
  document.querySelectorAll(".btn-primary, .btn-sm, .upload-btn, .nav .btn-nav").forEach(btn => {
    if (btn.dataset.ripple) return;
    btn.dataset.ripple = "1";
    btn.addEventListener("click", e => {
      const r = document.createElement("span");
      const rect = btn.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      r.style.cssText = `position:absolute;border-radius:50%;background:rgba(255,255,255,.5);pointer-events:none;width:${size}px;height:${size}px;left:${e.clientX-rect.left-size/2}px;top:${e.clientY-rect.top-size/2}px;transform:scale(0);animation:ripple .6s ease-out forwards;`;
      const prev = btn.style.position;
      if (getComputedStyle(btn).position === "static") btn.style.position = "relative";
      btn.style.overflow = "hidden";
      btn.appendChild(r);
      setTimeout(() => r.remove(), 600);
      if (prev) btn.style.position = prev;
    });
  });
}
document.addEventListener("DOMContentLoaded", attachRipple);

// ── DRAG & DROP UPLOAD ──────────────────────────────────────────────────
const mainPanel = document.getElementById("main-panel");
const dropOverlay = document.createElement("div");
dropOverlay.className = "drop-overlay";
dropOverlay.innerHTML = '<div class="drop-label">↑ Drop files to upload</div>';
mainPanel.style.position = "relative";
mainPanel.appendChild(dropOverlay);

let dragCounter = 0;
mainPanel.addEventListener("dragenter", e => {
  e.preventDefault();
  dragCounter++;
  dropOverlay.classList.add("visible");
});
mainPanel.addEventListener("dragleave", () => {
  dragCounter--;
  if (dragCounter === 0) dropOverlay.classList.remove("visible");
});
mainPanel.addEventListener("dragover", e => e.preventDefault());
mainPanel.addEventListener("drop", e => {
  e.preventDefault();
  dragCounter = 0;
  dropOverlay.classList.remove("visible");
  if (e.dataTransfer.files.length) {
    document.getElementById("file-input").files = e.dataTransfer.files;
    uploadFiles(document.getElementById("file-input"));
  }
});

// ── KEYBOARD SHORTCUTS ─────────────────────────────────────────────────
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
  switch(e.key) {
    case "u": case "U":
      if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); document.getElementById("file-input").click(); }
      break;
    case "1": showView("files"); break;
    case "2": if (currentUser?.role === "org_admin" || currentUser?.role === "master_admin") showView("trash"); break;
    case "3": if (currentUser?.role === "org_admin" || currentUser?.role === "master_admin") showView("logs"); break;
    case "4": if (currentUser?.role === "org_admin" || currentUser?.role === "master_admin") showView("users"); break;
    case "5": if (currentUser?.role === "org_admin" || currentUser?.role === "master_admin") showView("versions-all"); break;
    case "6": if (currentUser?.role === "org_admin" || currentUser?.role === "master_admin") showView("backup"); break;
    case "/": e.preventDefault(); document.getElementById("team-key").focus(); break;
  }
});



// ── DOUBLE-CLICK TO DOWNLOAD ───────────────────────────────────────────
document.getElementById("file-tbody").addEventListener("dblclick", e => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  const btn = tr.querySelector(".btn-sm");
  if (btn) btn.click();
});

// ── LOGIN ERROR SHAKE ──────────────────────────────────────────────────
const _origDoLogin = doLogin;
doLogin = async function() {
  const errEl = document.getElementById("login-err");
  errEl.classList.remove("shake");
  await _origDoLogin();
  if (errEl.textContent) {
    void errEl.offsetWidth;
    errEl.classList.add("shake");
  }
};

// ── LOADING SKELETON ───────────────────────────────────────────────────
let _skeletonShown = false;
function showSkeleton() {
  if (_skeletonShown) return;
  _skeletonShown = true;
  const tbody = document.getElementById("file-tbody");
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
function hideSkeleton() {
  _skeletonShown = false;
}

// ── INIT ─────────────────────────────────────────────────────────────────
updateBreadcrumb();

const params = new URLSearchParams(window.location.search);
if (params.get("registered") === "1") {
  document.getElementById("login-success").textContent =
    "✓ Registration successful! You can now log in with the username and password you chose.";
}

fetch(API + "/me").then(async r => {
  if (r.ok) {
    currentUser = await r.json();
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("app").classList.add("visible");
    document.getElementById("topbar-user").textContent = currentUser.username;
    document.getElementById("topbar-role").textContent = currentUser.role;
    if (currentUser.role !== "org_admin" && currentUser.role !== "master_admin") {
      document.getElementById("nav-trash").style.display = "none";
      document.getElementById("nav-logs").style.display  = "none";
      document.getElementById("nav-users").style.display = "none";
      document.getElementById("nav-versions-all").style.display = "none";
      document.getElementById("nav-backup").style.display = "none";
    }
    await loadFolders();
    refreshFiles();
    updateTrashCount();
  }
});
