// frontend/admin.js — users/logs, folder permissions, backups, versions-all
import { API, state, toast, fmt, fmtDate, fmtLogDate, escapeHtml, openModal, closeModal, revealOnScroll } from "./api.js";

// ── LOGS ───────────────────────────────────────────────────────────────────
export async function loadLogs() {
  const r = await fetch(API + "/logs?limit=300", {credentials: "same-origin"});
  if (!r.ok) return;
  const logs = await r.json();
  const list = document.getElementById("log-list");
  if (!list) return;
  list.innerHTML = "";
  // Group by org if master global (has org_name)
  const isMaster = logs.some(l => l.org_name);
  if (isMaster && logs.length) {
    const byOrg = {};
    for (const l of logs) { const k = l.org_name || "—"; (byOrg[k] = byOrg[k] || []).push(l); }
    for (const [org, items] of Object.entries(byOrg)) {
      const header = document.createElement("div");
      header.style.cssText = "font-size:11px;font-weight:700;color:var(--accent);margin:14px 0 6px;text-transform:uppercase;letter-spacing:.06em";
      header.textContent = `🏢 ${org} — ${items.length} events`;
      list.appendChild(header);
      for (const l of items) {
        const row = document.createElement("div");
        row.className = "log-row";
        row.innerHTML = `
          <span class="log-ts">${fmtLogDate(l.ts)}</span>
          <span class="log-user">${escapeHtml(l.username || "—")}</span>
          <span class="log-action log-${escapeHtml(l.action)}">${escapeHtml(l.action)}</span>
          <span class="log-detail">${escapeHtml([l.target, l.detail].filter(Boolean).join(" · "))}</span>`;
        list.appendChild(row);
      }
    }
    return;
  }
  for (const l of logs) {
    const row = document.createElement("div");
    row.className = "log-row";
    row.innerHTML = `
      <span class="log-ts">${fmtLogDate(l.ts)}</span>
      <span class="log-user">${escapeHtml(l.username || "—")}</span>
      <span class="log-action log-${escapeHtml(l.action)}">${escapeHtml(l.action)}</span>
      <span class="log-detail">${escapeHtml([l.target, l.detail].filter(Boolean).join(" · "))}</span>`;
    list.appendChild(row);
  }
}

// ── USER MANAGEMENT ─────────────────────────────────────────────────────────
let _umUsers = [];
let _umFolders = [];
let _umEditUserId = null;
let _umPermsUserId = null;
let _currentTeamTab = "users";

export function switchTeamTab(tab) {
  _currentTeamTab = tab;
  document.getElementById("team-tab-users").style.display = tab === "users" ? "" : "none";
  document.getElementById("team-tab-folders").style.display = tab === "folders" ? "" : "none";
  document.getElementById("um-tab-users").classList.toggle("active", tab === "users");
  document.getElementById("um-tab-folders").classList.toggle("active", tab === "folders");
  if (tab === "folders") loadFolderAccess();
}

export async function loadUserManagement() {
  const [usersRes, statsRes, foldersRes] = await Promise.all([
    fetch(API + "/users", {credentials:"same-origin"}),
    fetch(API + "/users/stats", {credentials:"same-origin"}),
    fetch(API + "/folders", {credentials:"same-origin"}),
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

export function renderUserStats(stats) {
  const el = document.getElementById("um-stats");
  if (!el) return;
  const roles = stats.by_role || {};
  el.innerHTML = `
    <div class="um-stat-card"><div class="um-stat-num">${stats.total || 0}</div><div class="um-stat-label">Total Users</div></div>
    <div class="um-stat-card accent"><div class="um-stat-num">${roles.org_admin || 0}</div><div class="um-stat-label">Admins</div></div>
    <div class="um-stat-card blue"><div class="um-stat-num">${roles.read_write || 0}</div><div class="um-stat-label">Read/Write</div></div>
    <div class="um-stat-card muted"><div class="um-stat-num">${roles.read_only || 0}</div><div class="um-stat-label">Read Only</div></div>
    <div class="um-stat-card green"><div class="um-stat-num">${stats.active_this_week || 0}</div><div class="um-stat-label">Active (7d)</div></div>
    <div class="um-stat-card violet"><div class="um-stat-num">${stats.joined_this_month || 0}</div><div class="um-stat-label">New (30d)</div></div>`;
}

export function renderUserTable(users) {
  const tbody = document.getElementById("um-user-tbody");
  const empty = document.getElementById("um-empty");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!users.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  const isMaster = users.some(u => u.org_name);
  if (isMaster) {
    const byOrg = {};
    for (const u of users) { const k = u.org_name || "—"; (byOrg[k] = byOrg[k] || []).push(u); }
    for (const [org, list] of Object.entries(byOrg)) {
      const header = document.createElement("tr");
      header.innerHTML = `<td colspan="5" style="font-size:11px;font-weight:700;color:var(--accent);padding:10px 0 6px;text-transform:uppercase">🏢 ${escapeHtml(org)} — ${list.length} users</td>`;
      tbody.appendChild(header);
      for (let i = 0; i < list.length; i++) {
        const u = list[i];
        const isSelf = u.id === state.currentUser?.id;
        const tr = document.createElement("tr");
        tr.className = "row-enter";
        tr.style.animationDelay = `${i * 30}ms`;
        tr.innerHTML = `
          <td><div class="um-user-cell"><div class="um-avatar" style="background:${isSelf ? 'linear-gradient(135deg,var(--accent),var(--violet))' : 'var(--glass-bg)'}">${escapeHtml(u.username.charAt(0).toUpperCase())}</div><div><div class="um-user-name">${escapeHtml(u.username)}${isSelf ? ' <span class="um-you-badge">you</span>' : ''}</div></div></div></td>
          <td><span class="role-pill ${escapeHtml(u.role)}" style="cursor:default">${escapeHtml(u.role.replace("_"," "))}</span></td>
          <td><span class="file-meta">${fmtDate(u.created_at)}</span></td>
          <td><span class="file-meta" id="um-activity-${u.id}">—</span></td>
          <td><div class="action-row">
            <button class="btn-sm" onclick="showEditUserModal('${u.id}','${escapeHtml(u.username)}','${u.role}')" title="Edit user">Edit</button>
            <button class="btn-sm" onclick="showUserActivity('${u.id}','${escapeHtml(u.username)}')" title="View activity">Activity</button>
            <button class="btn-sm" onclick="showUserPermissions('${u.id}','${escapeHtml(u.username)}')" title="Manage permissions">Perms</button>
            ${!isSelf ? `<button class="btn-sm danger" onclick="deleteUser('${u.id}','${escapeHtml(u.username)}')" title="Remove user">Remove</button>` : ''}
          </div></td>`;
        tbody.appendChild(tr);
      }
    }
    loadUserActivitySummaries(users);
    return;
  }
  for (let i = 0; i < users.length; i++) {
    const u = users[i];
    const isSelf = u.id === state.currentUser?.id;
    const tr = document.createElement("tr");
    tr.className = "row-enter";
    tr.style.animationDelay = `${i * 30}ms`;
    tr.innerHTML = `
      <td><div class="um-user-cell"><div class="um-avatar" style="background:${isSelf ? 'linear-gradient(135deg,var(--accent),var(--violet))' : 'var(--glass-bg)'}">${escapeHtml(u.username.charAt(0).toUpperCase())}</div><div><div class="um-user-name">${escapeHtml(u.username)}${isSelf ? ' <span class="um-you-badge">you</span>' : ''}</div></div></div></td>
      <td><span class="role-pill ${escapeHtml(u.role)}" style="cursor:default">${escapeHtml(u.role.replace("_"," "))}</span></td>
      <td><span class="file-meta">${fmtDate(u.created_at)}</span></td>
      <td><span class="file-meta" id="um-activity-${u.id}">—</span></td>
      <td><div class="action-row">
        <button class="btn-sm" onclick="showEditUserModal('${u.id}','${escapeHtml(u.username)}','${u.role}')" title="Edit user">Edit</button>
        <button class="btn-sm" onclick="showUserActivity('${u.id}','${escapeHtml(u.username)}')" title="View activity">Activity</button>
        <button class="btn-sm" onclick="showUserPermissions('${u.id}','${escapeHtml(u.username)}')" title="Manage permissions">Perms</button>
        ${!isSelf ? `<button class="btn-sm danger" onclick="deleteUser('${u.id}','${escapeHtml(u.username)}')" title="Remove user">Remove</button>` : ''}
      </div></td>`;
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
        el.innerHTML = recent.length > 0 ? `<span style="color:var(--success)">${recent.length} actions (7d)</span>` : `<span style="color:var(--muted)">No recent activity</span>`;
      }
    } catch {}
  }
}

export function filterUsers() {
  const q = document.getElementById("um-search").value.toLowerCase();
  const roleFilter = document.getElementById("um-role-filter").value;
  let filtered = _umUsers;
  if (q) filtered = filtered.filter(u => u.username.toLowerCase().includes(q));
  if (roleFilter) filtered = filtered.filter(u => u.role === roleFilter);
  renderUserTable(filtered);
}

export function showAddUserModal() {
  document.getElementById("au-username").value = "";
  document.getElementById("au-password").value = "";
  document.getElementById("au-role").value = "read_write";
  openModal("add-user-modal");
  setTimeout(() => document.getElementById("au-username").focus(), 100);
}

export async function addUser() {
  const username = document.getElementById("au-username").value.trim();
  const password = document.getElementById("au-password").value;
  const role = document.getElementById("au-role").value;
  if (!username) { toast("Username required", "err"); return; }
  if (!password || password.length < 6) { toast("Password must be at least 6 characters", "err"); return; }
  const r = await fetch(API + "/users", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({username, password, role}) });
  if (r.ok) { closeModal("add-user-modal"); loadUserManagement(); toast(`Created user ${username}`); }
  else { const d = await r.json(); toast(d.error || "Failed to create user", "err"); }
}

export function showEditUserModal(userId, username, role) {
  _umEditUserId = userId;
  document.getElementById("eu-username-display").textContent = username;
  document.getElementById("eu-username").value = username;
  document.getElementById("eu-password").value = "";
  document.getElementById("eu-role").value = role;
  openModal("edit-user-modal");
}

export async function saveUserEdit() {
  if (!_umEditUserId) return;
  const username = document.getElementById("eu-username").value.trim();
  const password = document.getElementById("eu-password").value;
  const role = document.getElementById("eu-role").value;
  const body = {role};
  if (username) body.username = username;
  if (password) body.password = password;
  const r = await fetch(`${API}/users/${_umEditUserId}`, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  if (r.ok) { closeModal("edit-user-modal"); loadUserManagement(); toast("User updated"); }
  else { const d = await r.json(); toast(d.error || "Failed to update", "err"); }
}

export async function deleteUser(id, name) {
  if (!confirm(`Remove "${name}" from the team? This will also remove their folder permissions.`)) return;
  const r = await fetch(`${API}/users/${id}`, {method: "DELETE"});
  if (r.ok) { loadUserManagement(); toast(`Removed ${name}`); }
  else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

export async function showUserActivity(userId, username) {
  document.getElementById("ua-username-display").textContent = username;
  const list = document.getElementById("user-activity-list");
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">Loading…</div>';
  openModal("user-activity-modal");
  const r = await fetch(`${API}/users/${userId}/activity?limit=100`);
  if (!r.ok) { list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--danger)">Failed to load</div>'; return; }
  const logs = await r.json();
  list.innerHTML = "";
  if (!logs.length) { list.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No activity recorded yet.</div>'; return; }
  for (const l of logs) {
    const row = document.createElement("div");
    row.className = "log-row";
    row.innerHTML = `<span class="log-ts">${fmtLogDate(l.ts)}</span><span class="log-action log-${escapeHtml(l.action)}">${escapeHtml(l.action)}</span><span class="log-detail">${escapeHtml([l.target, l.detail].filter(Boolean).join(" · "))}</span>`;
    list.appendChild(row);
  }
}

export async function showUserPermissions(userId, username) {
  _umPermsUserId = userId;
  document.getElementById("up-username-display").textContent = username;
  openModal("user-perms-modal");
  const folderSelect = document.getElementById("up-folder");
  folderSelect.innerHTML = '<option value="">Root (all folders)</option>';
  for (const f of _umFolders) { folderSelect.innerHTML += `<option value="${f.id}">${escapeHtml(f.name)}</option>`; }
  await loadUserPermissionsList(userId);
}

export async function loadUserPermissionsList(userId) {
  const list = document.getElementById("user-perms-list");
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">Loading…</div>';
  const r = await fetch(`${API}/users/${userId}/permissions`);
  if (!r.ok) { list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--danger)">Failed to load</div>'; return; }
  const perms = await r.json();
  list.innerHTML = "";
  if (!perms.length) { list.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted)">No folder-specific permissions. User inherits org-wide role.</div>'; return; }
  for (const p of perms) {
    const row = document.createElement("div");
    row.className = "um-perm-row";
    row.innerHTML = `<span class="um-perm-folder">📁 ${escapeHtml(p.folder_name)}</span><span class="role-pill ${escapeHtml(p.permission_level)}" style="font-size:10px">${escapeHtml(p.permission_level.replace("_"," "))}</span><button class="btn-sm danger" onclick="removeUserPermission('${_umPermsUserId}','${p.id}')" style="font-size:10px">✕ Remove</button>`;
    list.appendChild(row);
  }
}

export async function addUserPermission() {
  if (!_umPermsUserId) return;
  const folderId = document.getElementById("up-folder").value || null;
  const level = document.getElementById("up-level").value;
  const r = await fetch(`${API}/users/${_umPermsUserId}/permissions`, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({folder_id: folderId, permission_level: level}) });
  if (r.ok) { loadUserPermissionsList(_umPermsUserId); toast("Permission added"); } else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

export async function removeUserPermission(userId, permId) {
  const r = await fetch(`${API}/users/${userId}/permissions/${permId}`, {method: "DELETE"});
  if (r.ok) { loadUserPermissionsList(userId); toast("Permission removed"); }
}

// ── FOLDER ACCESS ───────────────────────────────────────────────────────────
let _faFolders = [];
let _faGrantFolderId = null;

export async function loadFolderAccess() {
  const [foldersRes, usersRes] = await Promise.all([ fetch(API + "/folders/permissions/all"), fetch(API + "/folders/all-users") ]);
  if (foldersRes.ok) _faFolders = await foldersRes.json();
  const users = usersRes.ok ? await usersRes.json() : [];
  renderFolderAccessList(_faFolders, users);
}

export function renderFolderAccessList(folders, users) {
  const list = document.getElementById("fa-folder-list");
  const empty = document.getElementById("fa-empty");
  if (!list) return;
  list.innerHTML = "";
  if (!folders.length) { if (empty) empty.style.display = ""; return; }
  if (empty) empty.style.display = "none";
  for (let i = 0; i < folders.length; i++) {
    const f = folders[i];
    const card = document.createElement("div");
    card.className = "fa-card";
    card.style.animationDelay = `${i * 40}ms`;
    const depth = f.parent_id ? 1 : 0;
    const userCountBadge = f.user_count > 0 ? `<span class="fa-user-count">${f.user_count} user${f.user_count > 1 ? "s" : ""}</span>` : `<span class="fa-user-count fa-user-count-empty">No restrictions</span>`;
    card.innerHTML = `<div class="fa-card-header" style="padding-left:${12 + depth * 20}px"><span class="fa-folder-icon">${depth ? "└📁" : "📁"}</span><span class="fa-folder-name">${escapeHtml(f.name)}</span>${userCountBadge}<button class="btn-sm active" onclick="showGrantAccess('${f.id}','${escapeHtml(f.name)}')" style="font-size:11px">+ Grant Access</button></div><div class="fa-perm-list" id="fa-perms-${f.id}">${f.permissions.length === 0 ? `<div class="fa-perm-empty">All org members can access (using their org-wide role)</div>` : f.permissions.map(p => `<div class="fa-perm-row"><span class="fa-perm-user">👤 ${escapeHtml(p.username)}</span><span class="role-pill ${escapeHtml(p.permission_level)}" style="font-size:10px">${escapeHtml(p.permission_level.replace("_"," "))}</span><button class="btn-sm danger" onclick="revokeFolderAccess('${f.id}','${p.id}','${escapeHtml(p.username)}')" style="font-size:10px">Revoke</button></div>`).join("")}</div>`;
    list.appendChild(card);
  }
  window._faAllUsers = users;
}

export function showGrantAccess(folderId, folderName) {
  _faGrantFolderId = folderId;
  document.getElementById("ga-folder-name").textContent = folderName;
  const select = document.getElementById("ga-user");
  select.innerHTML = '<option value="">Choose a user…</option>';
  const users = window._faAllUsers || [];
  for (const u of users) { select.innerHTML += `<option value="${u.id}">${escapeHtml(u.username)} (${escapeHtml(u.role.replace("_"," "))})</option>`; }
  document.getElementById("ga-level").value = "read_only";
  openModal("grant-access-modal");
}

export async function grantFolderAccess() {
  if (!_faGrantFolderId) return;
  const userId = document.getElementById("ga-user").value;
  const level = document.getElementById("ga-level").value;
  if (!userId) { toast("Select a user", "err"); return; }
  const r = await fetch(`${API}/folders/${_faGrantFolderId}/permissions`, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({user_id: userId, permission_level: level}) });
  if (r.ok) { closeModal("grant-access-modal"); loadFolderAccess(); toast("Access granted"); } else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

export async function revokeFolderAccess(folderId, permId, username) {
  if (!confirm(`Revoke ${username}'s access to this folder?`)) return;
  const r = await fetch(`${API}/folders/${folderId}/permissions/${permId}`, {method: "DELETE"});
  if (r.ok) { loadFolderAccess(); toast("Access revoked"); } else { const d = await r.json(); toast(d.error || "Failed", "err"); }
}

// ── BACKUP ──────────────────────────────────────────────────────────────────
export async function loadBackups() {
  const dbBtn = document.getElementById("daily-backup-btn");
  if (dbBtn && state.currentUser?.role === "master_admin") dbBtn.style.display = "";
  const r = await fetch(API + "/backup/list", {credentials:"same-origin"});
  if (!r.ok) { const d = await r.json().catch(() => ({})); if (d.error) toast(d.error, "err"); return; }
  const backups = await r.json();
  const list = document.getElementById("backup-list");
  const empty = document.getElementById("empty-backup");
  if (!list) return;
  list.innerHTML = "";
  if (!backups.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  const isMaster = backups.some(b => b.org_name);
  if (isMaster) {
    const byOrg = {};
    for (const b of backups) { const k = b.org_name || "—"; (byOrg[k] = byOrg[k] || []).push(b); }
    for (const [org, items] of Object.entries(byOrg)) {
      const header = document.createElement("div");
      header.style.cssText = "font-size:11px;font-weight:700;color:var(--accent);margin:14px 0 6px;text-transform:uppercase";
      header.textContent = `🏢 ${org} — ${items.length} backups`;
      list.appendChild(header);
      for (const b of items) {
        const card = document.createElement("div");
        card.className = "trash-card";
        const dt = b.created_at ? fmtDate(b.created_at) : "—";
        card.innerHTML = `<span style="flex:1"><strong style="font-family:var(--mono);font-size:12px">${escapeHtml(b.name)}</strong><span style="font-size:12px;color:var(--muted);margin-left:10px">${fmt(b.size_bytes)} · ${escapeHtml(dt)}</span></span><div class="action-row"><button class="btn-sm" onclick="downloadBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↓ Download</button><button class="btn-sm" onclick="restoreBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↩ Restore</button><button class="btn-sm danger" onclick="deleteBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">🗑</button></div>`;
        list.appendChild(card);
      }
    }
    return;
  }
  for (const b of backups) {
    const card = document.createElement("div");
    card.className = "trash-card";
    const dt = b.created_at ? fmtDate(b.created_at) : "—";
    card.innerHTML = `<span style="flex:1"><strong style="font-family:var(--mono);font-size:12px">${escapeHtml(b.name)}</strong><span style="font-size:12px;color:var(--muted);margin-left:10px">${fmt(b.size_bytes)} · ${escapeHtml(dt)}</span></span><div class="action-row"><button class="btn-sm" onclick="downloadBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↓ Download</button><button class="btn-sm" onclick="restoreBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">↩ Restore</button><button class="btn-sm danger" onclick="deleteBackup('${escapeHtml(b.name).replace(/'/g,"\\'")}')">🗑</button></div>`;
    list.appendChild(card);
  }
}

export async function createBackup() {
  const btn = document.querySelector("#view-backup .btn-sm.active");
  if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
  const r = await fetch(API + "/backup/create", {method: "POST"});
  if (btn) { btn.disabled = false; btn.textContent = "＋ Create backup"; }
  if (r.ok) { loadBackups(); toast("Backup created"); } else { const d = await r.json().catch(() => ({})); toast(d.error || "Backup failed", "err"); }
}

export async function restoreBackup(name) {
  if (!confirm(`Restore from this backup? This will REPLACE all current org data.`)) return;
  const r = await fetch(`${API}/backup/restore/${encodeURIComponent(name)}`, {method: "POST"});
  if (r.ok) { toast("Restored — reloading…"); setTimeout(() => location.reload(), 1000); } else { const d = await r.json().catch(() => ({})); toast(d.error || "Restore failed", "err"); }
}

export async function downloadBackup(name) { window.open(`${API}/backup/download/${encodeURIComponent(name)}`, "_blank"); }

export async function deleteBackup(name) {
  if (!confirm(`Delete backup "${name}"?`)) return;
  const r = await fetch(`${API}/backup/delete/${encodeURIComponent(name)}`, {method: "DELETE"});
  if (r.ok) { loadBackups(); toast("Backup deleted"); } else { const d = await r.json().catch(() => ({})); toast(d.error || "Delete failed", "err"); }
}

// ── VERSIONS ALL ────────────────────────────────────────────────────────────
export async function loadAllVersions() {
  const r = await fetch(API + "/versions/all", {credentials:"same-origin"});
  if (!r.ok) return;
  const versions = await r.json();
  const list = document.getElementById("versions-all-list");
  const empty = document.getElementById("empty-versions-all");
  if (!list) return;
  list.innerHTML = "";
  if (!versions.length) { empty.style.display = ""; return; }
  empty.style.display = "none";
  const isMaster = versions.some(v => v.org_name);
  if (isMaster) {
    const byOrg = {};
    for (const v of versions) { const k = v.org_name || "—"; (byOrg[k] = byOrg[k] || []).push(v); }
    for (const [org, items] of Object.entries(byOrg)) {
      const header = document.createElement("div");
      header.style.cssText = "font-size:11px;font-weight:700;color:var(--accent);margin:14px 0 6px;text-transform:uppercase";
      header.textContent = `🏢 ${org} — ${items.length} versions`;
      list.appendChild(header);
      for (const v of items) {
        const card = document.createElement("div");
        card.className = "version-card" + (v.is_current ? " current" : "");
        card.innerHTML = `<div class="version-no">v${v.version_number}</div><div class="version-info"><div class="size"><strong>${escapeHtml(v.filename)}</strong> · ${fmt(v.size_bytes)}</div><div class="who">by ${escapeHtml(v.uploaded_by_name || "—")} · ${fmtDate(v.uploaded_at)}</div><div class="version-sha">${escapeHtml(v.sha256 || "—")}</div></div><div class="action-row" style="flex-shrink:0">${v.is_current ? `<span class="current-pill">Current</span>` : ""}<button class="btn-sm" onclick="openVersions('${v.file_id}','${escapeHtml((v.filename||'')).replace(/'/g,"\\'")}')">Open</button></div>`;
        list.appendChild(card);
      }
    }
    return;
  }
  for (const v of versions) {
    const card = document.createElement("div");
    card.className = "version-card" + (v.is_current ? " current" : "");
    card.innerHTML = `<div class="version-no">v${v.version_number}</div><div class="version-info"><div class="size"><strong>${escapeHtml(v.filename)}</strong> · ${fmt(v.size_bytes)}</div><div class="who">by ${escapeHtml(v.uploaded_by_name || "—")} · ${fmtDate(v.uploaded_at)}</div><div class="version-sha">${escapeHtml(v.sha256 || "—")}</div></div><div class="action-row" style="flex-shrink:0">${v.is_current ? `<span class="current-pill">Current</span>` : ""}<button class="btn-sm" onclick="openVersions('${v.file_id}','${escapeHtml((v.filename||'')).replace(/'/g,"\\'")}')">Open</button></div>`;
    list.appendChild(card);
    // delegate openVersions to files.js at runtime
    card.querySelector("button")?.addEventListener("click", () => {
      import("./files.js").then(m => m.openVersions(v.file_id, v.filename));
    });
  }
}

if (typeof window !== "undefined") {
  window.loadLogs = loadLogs;
  window.switchTeamTab = switchTeamTab;
  window.loadUserManagement = loadUserManagement;
  window.renderUserStats = renderUserStats;
  window.renderUserTable = renderUserTable;
  window.filterUsers = filterUsers;
  window.showAddUserModal = showAddUserModal;
  window.addUser = addUser;
  window.showEditUserModal = showEditUserModal;
  window.saveUserEdit = saveUserEdit;
  window.deleteUser = deleteUser;
  window.showUserActivity = showUserActivity;
  window.showUserPermissions = showUserPermissions;
  window.loadUserPermissionsList = loadUserPermissionsList;
  window.addUserPermission = addUserPermission;
  window.removeUserPermission = removeUserPermission;
  window.loadFolderAccess = loadFolderAccess;
  window.renderFolderAccessList = renderFolderAccessList;
  window.showGrantAccess = showGrantAccess;
  window.grantFolderAccess = grantFolderAccess;
  window.revokeFolderAccess = revokeFolderAccess;
  window.loadBackups = loadBackups;
  window.createBackup = createBackup;
  window.restoreBackup = restoreBackup;
  window.downloadBackup = downloadBackup;
  window.deleteBackup = deleteBackup;
  window.loadAllVersions = loadAllVersions;
  window.openChangePassword = openChangePassword;
  window.changeMyPassword = changeMyPassword;
  window.runDailyBackup = runDailyBackup;
  window.loadShares = loadShares;
  window.revokeShare = revokeShare;
  window.copyText = copyText;
  window.loadOrgSettings = loadOrgSettings;
}

// ── DAILY (ESSENTIAL-ONLY) BACKUP (Phase-1 M8) ────────────────────────────
export async function runDailyBackup() {
  if (state.currentUser?.role !== "master_admin") { toast("Master admin only", "err"); return; }
  if (!confirm("Run daily backup of all essential folders across active organisations?")) return;
  toast("Daily backup running…");
  const r = await fetch(`${API}/backup/daily`, { method: "POST", credentials: "same-origin" });
  const d = await r.json().catch(() => ({}));
  if (r.ok) toast(`Daily backup complete: ${d.summary || d}`, "ok");
  else toast(d.error || "Daily backup failed", "err");
}

// ── SELF-SERVICE PASSWORD CHANGE (Phase-1 O2) ─────────────────────────────
export async function openChangePassword() {
  const m = document.getElementById("change-password-modal");
  if (!m) return;
  document.getElementById("cp-current").value = "";
  document.getElementById("cp-new").value = "";
  document.getElementById("cp-confirm").value = "";
  m.style.display = "flex";
}

export async function changeMyPassword() {
  const cur = document.getElementById("cp-current").value;
  const pw = document.getElementById("cp-new").value;
  const conf = document.getElementById("cp-confirm").value;
  if (!cur || !pw) { toast("Both fields are required", "err"); return; }
  if (pw.length < 6) { toast("New password must be at least 6 characters", "err"); return; }
  if (pw !== conf) { toast("New passwords do not match", "err"); return; }
  const r = await fetch(`${API}/users/me/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ current_password: cur, new_password: pw }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) {
    toast("Password changed", "ok");
    document.getElementById("change-password-modal").style.display = "none";
  } else toast(d.error || "Change failed", "err");
}

// ── SHARED LINKS (Phase-1 O6) ────────────────────────────────────────────────
export async function loadShares() {
  const list = document.getElementById("shares-list");
  const empty = document.getElementById("empty-shares");
  if (!list) return;
  const r = await fetch(API + "/shares", {credentials: "same-origin"});
  if (!r.ok) { list.innerHTML = `<div style="color:var(--muted)">Failed to load shares</div>`; return; }
  const shares = await r.json();
  list.innerHTML = "";
  if (!shares.length) { if (empty) empty.style.display = ""; return; }
  if (empty) empty.style.display = "none";
  for (const s of shares) {
    const card = document.createElement("div");
    card.className = "fa-card";
    card.style.cssText = "display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:12px;margin-bottom:8px";
    const exp = s.expires_at ? new Date(s.expires_at).toLocaleDateString() : "never";
    const created = s.created_at ? new Date(s.created_at).toLocaleDateString() : "—";
    card.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;color:var(--text)">${escapeHtml(s.file_name || "file")}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">token: ${escapeHtml(String(s.token).slice(0,12))}… · created ${created} · expires ${exp} · downloads ${s.download_count || 0}${s.has_password ? " · 🔒" : ""}</div>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn-sm" onclick="copyText('${escapeHtml(s.token)}')">Copy link</button>
        <button class="btn-sm danger" onclick="revokeShare('${s.id}')">Revoke</button>
      </div>`;
    list.appendChild(card);
  }
}

export async function revokeShare(id) {
  if (!confirm("Revoke this share link?")) return;
  const r = await fetch(`${API}/shares/${id}`, {method: "DELETE", credentials: "same-origin"});
  if (r.ok) { toast("Share revoked", "ok"); loadShares(); }
  else { const d = await r.json().catch(()=>({})); toast(d.error || "Revoke failed", "err"); }
}

export async function copyText(txt) {
  try { await navigator.clipboard.writeText(`${location.origin}/shared/${txt}`); toast("Link copied", "ok"); }
  catch { toast("Copy failed", "err"); }
}

// ── ORG SETTINGS (Phase-1 O7) ─────────────────────────────────────────────────
export async function loadOrgSettings() {
  const el = document.getElementById("settings-content");
  if (!el) return;
  const orgsRes = await fetch(API + "/orgs", {credentials: "same-origin"});
  const orgs = orgsRes.ok ? await orgsRes.json() : [];
  const org = Array.isArray(orgs) ? orgs.find(o => o.id === state.currentUser?.org_id) : (orgs.id ? orgs : null);
  const statsRes = await fetch(API + "/stats", {credentials: "same-origin"});
  const stats = statsRes.ok ? await statsRes.json() : {};
  const s = stats.org || stats.totals || {};
  if (!org) { el.innerHTML = `<div style="color:var(--muted)">No organisation context.</div>`; return; }
  const used = s.storage_bytes || 0;
  const quota = s.storage_quota_bytes;
  let quotaBar = "";
  if (quota) {
    const pct = Math.min(100, Math.round((used / quota) * 100));
    const over = used > quota;
    quotaBar = `
      <div style="margin-top:6px;height:8px;background:var(--glass-bg);border-radius:6px;overflow:hidden">
        <div style="width:${pct}%;height:100%;background:${over ? "var(--danger)" : "var(--accent)"}"></div>
      </div>
      <div style="font-size:11px;color:${over ? "var(--danger)" : "var(--muted)"};margin-top:4px">${fmt(used)} / ${fmt(quota)} (${pct}%)</div>`;
  } else {
    quotaBar = `<div style="font-size:11px;color:var(--muted);margin-top:4px">${fmt(used)} used · no quota set</div>`;
  }
  el.innerHTML = `
    <div class="fa-card" style="padding:16px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:12px">
      <div style="font-size:13px;color:var(--muted)">Organisation</div>
      <div style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:10px">${escapeHtml(org.name)}</div>
      <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Telegram channel: ${escapeHtml(org.telegram_chat_id || "—")}</div>
      <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Backup channel: ${escapeHtml(org.backup_channel_id || "— none —")}</div>
      <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Status: ${escapeHtml(org.status || "—")}</div>
      <hr style="border:none;border-top:1px solid var(--glass-border);margin:12px 0">
      <div style="font-size:13px;color:var(--muted)">Storage</div>
      ${quotaBar}
      <div style="font-size:12px;color:var(--muted);margin-top:6px">📄 ${s.file_count || 0} files · 👥 ${s.user_count || 0} users · 🗂 ${s.folder_count || 0} folders</div>
      <div style="font-size:12px;color:var(--muted);margin-top:4px">${s.last_backup ? "Last backup " + new Date(s.last_backup).toLocaleDateString() : "No backup yet"}</div>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button class="btn-sm" onclick="loadShares()">View share links</button>
        <button class="btn-sm" onclick="openChangePassword()">Change password</button>
      </div>
    </div>`;
}
