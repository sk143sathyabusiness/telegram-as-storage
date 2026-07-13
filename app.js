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
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
  });
}

function fmtLogDate(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, {
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
  if (name === "users")        loadUsers();
  if (name === "versions-all") loadAllVersions();
  if (name === "backup")       loadBackups();
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
  item.innerHTML = `<span class="icon">📁</span> ${folder.name}`;
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
  else { toast("Could not create folder", "err"); }
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
async function uploadFiles() {
  const passphrase = document.getElementById("team-key").value;
  if (!passphrase) { toast("Enter team passphrase first", "err"); return; }

  // Check both file inputs for files
  const fileInput = document.getElementById("file-input");
  const folderInput = document.getElementById("folder-input");
  let files = fileInput.files;
  let usedInput = fileInput;
  if (!files.length) {
    files = folderInput.files;
    usedInput = folderInput;
  }
  if (!files.length) return;

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

function uploadOne(file, encBlob, onProgress) {
  return new Promise(async (resolve, reject) => {
    const sha256 = await sha256Hex(encBlob);
    const div = document.createElement("div");
    div.className = "upload-item";
    const pid = "p_" + Math.random().toString(36).slice(2);
    const eid = "e_" + Math.random().toString(36).slice(2);
    div.innerHTML = `<div class="upload-item-name">${file.name} <span class="file-size" style="color:var(--muted);font-size:11px">${fmt(encBlob.size)}</span></div>
      <div class="pbar"><div class="pbar-fill" id="${pid}"></div></div>
      <div class="upload-eta" id="${eid}"></div>`;
    document.getElementById("upload-items").appendChild(div);

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
      else reject(new Error(`HTTP ${xhr.status}`));
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
      <td><span class="file-name">${f.filename}</span></td>
      <td><span class="file-meta">${v ? fmt(v.size_bytes) : "—"}</span></td>
      <td><span class="version-badge">v${v ? v.version_no : "—"}</span></td>
      <td><span class="file-meta">${v ? (v.uploaded_by_name || "—") : "—"}</span></td>
      <td><span class="file-meta">${v ? fmtDate(v.uploaded_at) : "—"}</span></td>
      <td>
        <div class="action-row">
          <button class="btn-sm" onclick="downloadFile(${f.id},'${f.filename.replace(/'/g,"\\'")}')">↓ Download</button>
          <button class="btn-sm" onclick="openVersions(${f.id},'${f.filename.replace(/'/g,"\\'")}')">History</button>
          ${currentUser?.role === "org_admin" || currentUser?.role === "master_admin"
            ? `<button class="btn-sm danger" onclick="deleteFile(${f.id})">Delete</button>`
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
  else toast("Delete failed", "err");
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
  const sorted = [...versions].sort((a, b) => a.version_no - b.version_no);
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
      <div class="version-no">v${v.version_no}</div>
      <div class="version-info">
        <div class="size">${fmt(v.size_bytes)} ${changeHtml}</div>
        <div class="who">by ${v.uploaded_by_name || "—"} · ${fmtDate(v.uploaded_at)}</div>
        <div class="version-sha">${v.sha256}</div>
      </div>
      <div class="action-row">
        ${v.is_current
          ? `<span class="current-pill">Current</span>`
          : (currentUser?.role !== "read_only"
              ? `<button class="btn-sm" onclick="restoreVersion(${fileId},${v.version_no})">↩ Restore</button>`
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
      <div class="version-no">v${v.version_no}</div>
      <div class="version-info">
        <div class="size"><strong>${v.filename}</strong> · ${fmt(v.size_bytes)}</div>
        <div class="who">by ${v.uploaded_by_name || "—"} · ${fmtDate(v.uploaded_at)}</div>
        <div class="version-sha">${v.sha256 || "—"}</div>
      </div>
      <div class="action-row" style="flex-shrink:0">
        ${v.is_current ? `<span class="current-pill">Current</span>` : ""}
        <button class="btn-sm" onclick="openVersions(${v.file_id},'${(v.filename||'').replace(/'/g,"\\'")}')">Open</button>
      </div>`;
    list.appendChild(card);
  }
}

// ── BACKUP ────────────────────────────────────────────────────────────────
async function loadBackups() {
  const r = await fetch(API + "/backup/list");
  if (!r.ok) return;
  const backups = await r.json();
  const list = document.getElementById("backup-list");
  const empty = document.getElementById("empty-backup");
  list.innerHTML = "";
  if (!backups.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  for (const b of backups) {
    const card = document.createElement("div");
    card.className = "trash-card";
    const label = b.name.replace("teamvault_", "").replace(".db", "").replace(/_/g, " ");
    card.innerHTML = `
      <span style="flex:1">
        <strong>${label}</strong>
        <span style="font-size:12px;color:var(--muted);margin-left:10px">${fmt(b.size_bytes)}</span>
      </span>
      <div class="action-row">
        <button class="btn-sm" onclick="downloadBackup('${b.name}')">↓ Download</button>
        <button class="btn-sm" onclick="restoreBackup('${b.name}')">↩ Restore</button>
        <button class="btn-sm danger" onclick="deleteBackup('${b.name}')">🗑</button>
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
  else { const d = await r.json(); toast(d.error || "Backup failed", "err"); }
}

async function restoreBackup(name) {
  if (!confirm(`Restore database from "${name}"? This will replace all current data.`)) return;
  const r = await fetch(`${API}/backup/restore/${encodeURIComponent(name)}`, {method: "POST"});
  if (r.ok) { toast(`Restored from ${name}`); location.reload(); }
  else { const d = await r.json(); toast(d.error || "Restore failed", "err"); }
}

async function downloadBackup(name) {
  const a = document.createElement("a");
  a.href = `${API}/backup/download/${encodeURIComponent(name)}`;
  a.download = name; a.click();
  toast("Downloading backup…");
}

async function deleteBackup(name) {
  if (!confirm(`Delete backup "${name}"?`)) return;
  const r = await fetch(`${API}/backup/delete/${encodeURIComponent(name)}`, {method: "DELETE"});
  if (r.ok) { loadBackups(); toast("Backup deleted"); }
  else { const d = await r.json(); toast(d.error || "Delete failed", "err"); }
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
      <span class="trash-name">${f.filename}</span>
      <span class="trash-who">deleted by ${f.deleted_by_name || "—"} · ${fmtDate(f.deleted_at)}</span>
      <div class="action-row">
        <button class="btn-sm" onclick="restoreFromTrash(${f.id})">↩ Restore</button>
        <button class="btn-sm danger" onclick="hardDelete(${f.id})">Destroy</button>
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

// ── USERS ─────────────────────────────────────────────────────────────────
async function loadUsers() {
  const r = await fetch(API + "/users");
  if (!r.ok) return;
  const users = await r.json();
  const list = document.getElementById("user-list");
  list.innerHTML = "";
  for (const u of users) {
    const card = document.createElement("div");
    card.className = "user-card";
    const isSelf = u.id === currentUser?.id;
    card.innerHTML = `
      <span class="user-name">${u.username}${isSelf ? " <span style='color:var(--muted);font-size:11px'>(you)</span>" : ""}</span>
      <span class="role-pill ${u.role}">${u.role.replace("_", " ")}</span>
      <span style="font-size:12px;color:var(--muted)">since ${fmtDate(u.created_at)}</span>
      ${!isSelf ? `<button class="btn-sm danger" onclick="deleteUser(${u.id},'${u.username}')">Remove</button>` : ""}`;
    list.appendChild(card);
  }
}

async function createUser() {
  const username = document.getElementById("nu-username").value.trim();
  const password = document.getElementById("nu-password").value;
  const role = document.getElementById("nu-role").value;
  if (!username || !password) { toast("Fill in all fields", "err"); return; }
  const r = await fetch(API + "/users", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, password, role})
  });
  if (r.ok) {
    document.getElementById("nu-username").value = "";
    document.getElementById("nu-password").value = "";
    document.getElementById("new-user-form").style.display = "none";
    loadUsers(); toast(`Created user ${username}`);
  } else {
    const d = await r.json(); toast(d.error || "Failed", "err");
  }
}

async function deleteUser(id, name) {
  if (!confirm(`Remove ${name} from the team?`)) return;
  const r = await fetch(`${API}/users/${id}`, {method: "DELETE"});
  if (r.ok) { loadUsers(); toast(`Removed ${name}`); }
  else toast("Failed to remove user", "err");
}

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
    uploadFiles();
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
