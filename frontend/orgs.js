// frontend/orgs.js — master_admin Organisations view
import { API, toast, escapeHtml, state, fmt } from "./api.js";

export async function loadOrgs() {
  const isMaster = state.currentUser?.role === "master_admin";
  const ms = document.getElementById("master-search");
  if (ms) ms.style.display = isMaster ? "block" : "none";
  const mub = document.getElementById("master-users-btn");
  if (mub) mub.style.display = isMaster ? "" : "none";
  const r = await fetch(`${API}/orgs`, {credentials: "same-origin"});
  const list = document.getElementById("orgs-list");
  const empty = document.getElementById("empty-orgs");
  if (!r.ok) {
    const d = await r.json().catch(()=>({}));
    if (r.status === 403) {
      list.innerHTML = `<div style="text-align:center;padding:24px;color:var(--muted)">Master admin only</div>`;
      if (empty) empty.style.display = "none";
      return;
    }
    toast(d.error || "Failed to load organisations", "err");
    return;
  }
  const orgs = await r.json();

  // Master: fetch aggregate usage stats and merge into cards + totals
  let statsMap = {};
  let totals = null;
  if (isMaster) {
    try {
      const sr = await fetch(`${API}/stats`, {credentials: "same-origin"});
      if (sr.ok) {
        const sd = await sr.json();
        for (const s of sd.orgs || []) statsMap[s.org_id] = s;
        totals = sd.totals;
      }
    } catch {}
    renderMasterStats(totals, statsMap);
  } else if (document.getElementById("orgs-stats")) {
    document.getElementById("orgs-stats").style.display = "none";
  }

  list.innerHTML = "";
  if (!orgs.length) { if (empty) empty.style.display = ""; return; }
  if (empty) empty.style.display = "none";
  for (const o of orgs) {
    const card = document.createElement("div");
    card.className = "fa-card";
    card.style.cssText = "display:flex;align-items:center;gap:12px;padding:14px 16px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:12px;";
    const statusColor = o.status === "active" ? "var(--success)" : o.status === "approved" ? "var(--accent)" : "var(--muted)";
    const backupTxt = o.backup_channel_id ? `backup: ${escapeHtml(String(o.backup_channel_id))}` : "backup: — none —";
    const st = statsMap[o.id] || {};
    const statLine = isMaster
      ? `<div style="font-size:11px;color:var(--muted);font-family:var(--mono)">📦 ${fmt(st.storage_bytes || 0)} · 📄 ${st.file_count || 0} · 👥 ${st.user_count || 0} · 🗓 ${st.last_backup ? "last backup " + new Date(st.last_backup).toLocaleDateString() : "no backup"}</div>`
      : "";
    card.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;color:var(--text)">${escapeHtml(o.name)}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${escapeHtml(o.id || "")} · <span style="color:${statusColor}">${escapeHtml(o.status || "—")}</span> · chat: ${escapeHtml(o.telegram_chat_id || "—")}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${backupTxt}</div>
        ${statLine}
        <div style="font-size:11px;color:var(--muted)">${escapeHtml(o.industry || "")} ${escapeHtml(o.size || "")}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn-sm" onclick="actAsOrg('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Act as</button>
        ${o.status !== "approved" && o.status !== "active" ? `<button class="btn-sm active" onclick="approveOrg('${o.id}')">Approve</button>` : ""}
        ${o.status !== "rejected" ? `<button class="btn-sm danger" onclick="rejectOrg('${o.id}')">Reject</button>` : ""}
        <button class="btn-sm" onclick="showSetBackupModal('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Set backup</button>
        ${isMaster ? `<button class="btn-sm" onclick="openEditOrg('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}','${escapeHtml(String(o.telegram_chat_id || ""))}','${escapeHtml(o.industry || "")}','${escapeHtml(o.size || "")}','${escapeHtml(o.status || "active")}','${escapeHtml(String(o.storage_quota_bytes || ""))}')">Edit</button>` : ""}
        ${isMaster ? `<button class="btn-sm" onclick="resetOrgAdmin('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Reset admin</button>` : ""}
        ${isMaster ? (o.status === "deleted" ? "" : `<button class="btn-sm" onclick="toggleSuspendOrg('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}','${escapeHtml(o.status || "active")}')">${o.status === "suspended" ? "Reactivate" : "Suspend"}</button>`) : ""}
        ${isMaster ? (o.status === "deleted" ? "" : `<button class="btn-sm danger" onclick="deleteOrg('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Delete</button>`) : ""}
      </div>`;
    list.appendChild(card);
  }
  updateMasterBanner();
}

function renderMasterStats(totals, statsMap) {
  const el = document.getElementById("orgs-stats");
  if (!el) return;
  if (!totals) { el.style.display = "none"; return; }
  el.style.display = "grid";
  el.innerHTML = `
    <div class="stat-card"><div class="stat-val">${totals.orgs ?? 0}</div><div class="stat-label">Organisations</div></div>
    <div class="stat-card"><div class="stat-val">${fmt(totals.storage_bytes || 0)}</div><div class="stat-label">Total storage</div></div>
    <div class="stat-card"><div class="stat-val">${totals.file_count ?? 0}</div><div class="stat-label">Files</div></div>
    <div class="stat-card"><div class="stat-val">${totals.user_count ?? 0}</div><div class="stat-label">Users</div></div>
    <div class="stat-card"><div class="stat-val">${totals.folder_count ?? 0}</div><div class="stat-label">Folders</div></div>`;
}

export async function approveOrg(orgId) {
  const r = await fetch(`${API}/orgs/${orgId}/approve`, {method:"POST", credentials:"same-origin"});
  if (r.ok) { toast("Organisation approved"); loadOrgs(); }
  else { const d = await r.json().catch(()=>({})); toast(d.error||"Failed","err"); }
}

export async function rejectOrg(orgId) {
  if (!confirm("Reject this organisation?")) return;
  const r = await fetch(`${API}/orgs/${orgId}/reject`, {method:"POST", credentials:"same-origin"});
  if (r.ok) { toast("Organisation rejected"); loadOrgs(); }
  else { const d = await r.json().catch(()=>({})); toast(d.error||"Failed","err"); }
}

export async function actAsOrg(orgId, orgName) {
  const r = await fetch(`${API}/master/switch-org`, {method:"POST", headers:{"Content-Type":"application/json"}, credentials:"same-origin", body: JSON.stringify({org_id: orgId})});
  const d = await r.json().catch(()=>({}));
  if (r.ok) {
    toast(`Acting as ${orgName || orgId}`);
    // Persist for getAutoPassphrase and show banner
    localStorage.setItem("tv_org_id", orgId);
    localStorage.setItem("tv_act_org_name", orgName || orgId);
    updateMasterBanner();
    // Refresh files/folders as that org
    const folders = await import("./folders.js");
    await folders.loadFolders();
    const files = await import("./files.js");
    files.refreshFiles();
    window.showView?.("files");
  } else toast(d.error||"Failed to act as org","err");
}

export async function clearActAs() {
  const r = await fetch(`${API}/master/clear`, {method:"POST", credentials:"same-origin"});
  if (r.ok) {
    toast("Back to master");
    localStorage.removeItem("tv_act_org_name");
    // restore real org (master has none, so clear)
    const me = await fetch(`${API}/me`, {credentials:"same-origin"}).then(r=>r.json()).catch(()=>null);
    if (me?.org_id) localStorage.setItem("tv_org_id", me.org_id); else localStorage.removeItem("tv_org_id");
    updateMasterBanner();
    window.showView?.("orgs");
  }
}

export async function updateMasterBanner() {
  try {
    const r = await fetch(`${API}/master/context`, {credentials:"same-origin"});
    if (!r.ok) return;
    const d = await r.json();
    const banner = document.getElementById("master-act-banner");
    const nameEl = document.getElementById("act-org-name");
    if (!banner || !nameEl) return;
    if (d.act_as_org_id) {
      banner.style.display = "flex";
      nameEl.textContent = localStorage.getItem("tv_act_org_name") || d.act_as_org_id;
    } else {
      banner.style.display = "none";
    }
  } catch {}
}

if (typeof window !== "undefined") {
  window.loadOrgs = loadOrgs;
  window.approveOrg = approveOrg;
  window.rejectOrg = rejectOrg;
  window.actAsOrg = actAsOrg;
  window.clearActAs = clearActAs;
  window.updateMasterBanner = updateMasterBanner;
  window.showCreateOrgModal = showCreateOrgModal;
  window.createOrg = createOrg;
  window.showSetBackupModal = showSetBackupModal;
  window.setBackupChannel = setBackupChannel;
  window.openEditOrg = openEditOrg;
  window.saveEditOrg = saveEditOrg;
  window.resetOrgAdmin = resetOrgAdmin;
  window.toggleSuspendOrg = toggleSuspendOrg;
  window.deleteOrg = deleteOrg;
  window.onMasterSearch = onMasterSearch;
  window.masterSearch = masterSearch;
  window.openMasterUsers = openMasterUsers;
  window.loadMasterUsers = loadMasterUsers;
  window.masterResetUserPassword = masterResetUserPassword;
}

export async function openEditOrg(id, name, chatId, industry, size, status, quota) {
  const m = document.getElementById("edit-org-modal");
  if (!m) return;
  document.getElementById("eo-id").value = id;
  document.getElementById("eo-name").value = name || "";
  document.getElementById("eo-chat-id").value = chatId || "";
  document.getElementById("eo-industry").value = industry || "";
  document.getElementById("eo-size").value = size || "";
  document.getElementById("eo-status").value = status || "active";
  document.getElementById("eo-quota").value = quota ?? "";
  m.style.display = "flex";
}

export async function saveEditOrg() {
  const id = document.getElementById("eo-id").value;
  const name = document.getElementById("eo-name").value.trim();
  const chat_id = document.getElementById("eo-chat-id").value.trim();
  const industry = document.getElementById("eo-industry").value.trim();
  const size = document.getElementById("eo-size").value.trim();
  const status = document.getElementById("eo-status").value.trim();
  if (!name || !chat_id) { toast("Name and Channel ID are required", "err"); return; }
  const quotaRaw = document.getElementById("eo-quota").value.trim();
  const body = { name, telegram_chat_id: chat_id, industry, size, status };
  if (quotaRaw !== "") body.storage_quota_bytes = parseInt(quotaRaw, 10);
  else body.storage_quota_bytes = null;
  const r = await fetch(`${API}/orgs/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) {
    toast("Organisation updated", "ok");
    document.getElementById("edit-org-modal").style.display = "none";
    loadOrgs();
  } else toast(d.error || "Update failed", "err");
}

export async function resetOrgAdmin(id, name) {
  const pw = prompt(`Reset admin password for "${name}"\n\nEnter new password (min 6 characters):`);
  if (!pw) return;
  const r = await fetch(`${API}/orgs/${id}/reset-admin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ password: pw }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) toast(`Admin password reset for ${d.username || name}`, "ok");
  else toast(d.error || "Reset failed", "err");
}

export async function toggleSuspendOrg(id, name, status) {
  const suspend = status !== "suspended";
  if (!confirm(`${suspend ? "Suspend" : "Reactivate"} organisation "${name}"?`)) return;
  const r = await fetch(`${API}/orgs/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ status: suspend ? "suspended" : "active" }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { toast(suspend ? `Suspended ${name}` : `Reactivated ${name}`, "ok"); loadOrgs(); }
  else toast(d.error || "Action failed", "err");
}

export async function deleteOrg(id, name) {
  if (!confirm(`Soft-delete organisation "${name}"? Its users will no longer be able to log in. This can be reversed by setting status back to active.`)) return;
  const r = await fetch(`${API}/orgs/${id}`, { method: "DELETE", credentials: "same-origin" });
  const d = await r.json().catch(() => ({}));
  if (r.ok) { toast(`Deleted ${name}`, "ok"); loadOrgs(); }
  else toast(d.error || "Delete failed", "err");
}

// ── MASTER GLOBAL SEARCH (M5) ──────────────────────────────────────────────
let _searchTimer = null;
export function onMasterSearch() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(masterSearch, 250);
}

export async function masterSearch() {
  const box = document.getElementById("master-search-input");
  const out = document.getElementById("master-search-results");
  if (!out) return;
  const q = box ? box.value.trim() : "";
  if (q.length < 2) { out.style.display = "none"; out.innerHTML = ""; return; }
  const r = await fetch(`${API}/master/search?q=${encodeURIComponent(q)}`, { credentials: "same-origin" });
  if (!r.ok) { out.style.display = "none"; return; }
  const d = await r.json();
  let html = "";
  if (d.files.length) {
    html += `<div style="font-size:11px;font-weight:700;color:var(--accent);margin:8px 0 4px;text-transform:uppercase">📄 Files (${d.files.length})</div>`;
    for (const f of d.files) {
      html += `<div style="padding:6px 8px;border-radius:8px;background:var(--glass-bg);margin-bottom:4px;font-size:13px">${escapeHtml(f.name)} <span style="color:var(--muted);font-size:11px">· ${escapeHtml(f.org_name)}</span></div>`;
    }
  }
  if (d.users.length) {
    html += `<div style="font-size:11px;font-weight:700;color:var(--accent);margin:8px 0 4px;text-transform:uppercase">👥 Users (${d.users.length})</div>`;
    for (const u of d.users) {
      html += `<div style="padding:6px 8px;border-radius:8px;background:var(--glass-bg);margin-bottom:4px;font-size:13px">${escapeHtml(u.username)} <span class="role-pill ${u.role}" style="cursor:default">${u.role.replace("_"," ")}</span> <span style="color:var(--muted);font-size:11px">· ${escapeHtml(u.org_name)}</span></div>`;
    }
  }
  if (!d.files.length && !d.users.length) html = `<div style="padding:10px;color:var(--muted)">No matches.</div>`;
  out.innerHTML = html;
  out.style.display = "block";
}

// ── MASTER USER MANAGEMENT (M6) ─────────────────────────────────────────────
export async function openMasterUsers() {
  const m = document.getElementById("master-users-modal");
  if (m) m.style.display = "flex";
  await loadMasterUsers();
}

export async function loadMasterUsers() {
  const list = document.getElementById("master-users-list");
  if (!list) return;
  const r = await fetch(`${API}/users`, { credentials: "same-origin" });
  if (!r.ok) { list.innerHTML = `<div style="color:var(--muted)">Master only</div>`; return; }
  const users = await r.json();
  list.innerHTML = "";
  if (!users.length) { list.innerHTML = `<div style="color:var(--muted)">No users.</div>`; return; }
  const isMaster = users.some(u => u.org_name);
  if (isMaster) {
    const byOrg = {};
    for (const u of users) (byOrg[u.org_name || "—"] = byOrg[u.org_name || "—"] || []).push(u);
    for (const [org, list2] of Object.entries(byOrg)) {
      const h = document.createElement("div");
      h.style.cssText = "font-size:11px;font-weight:700;color:var(--accent);margin:10px 0 4px;text-transform:uppercase";
      h.textContent = `🏢 ${org} — ${list2.length}`;
      list.appendChild(h);
      for (const u of list2) list.appendChild(masterUserRow(u));
    }
  } else {
    for (const u of users) list.appendChild(masterUserRow(u));
  }
}

function masterUserRow(u) {
  const row = document.createElement("div");
  row.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:10px;margin-bottom:6px";
  row.innerHTML = `
    <div style="flex:1;min-width:0">
      <div style="font-weight:600;color:var(--text)">${escapeHtml(u.username)}</div>
      <div style="font-size:11px;color:var(--muted)"><span class="role-pill ${u.role}" style="cursor:default">${u.role.replace("_"," ")}</span></div>
    </div>
    <button class="btn-sm" onclick="masterResetUserPassword('${u.id}','${escapeHtml(u.username).replace(/'/g, "\\'")}')">Reset password</button>`;
  return row;
}

export async function masterResetUserPassword(id, username) {
  const pw = prompt(`Reset password for "${username}"\n\nEnter new password (min 6 characters):`);
  if (!pw) return;
  const r = await fetch(`${API}/users/${id}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ password: pw }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) toast(`Password reset for ${d.username || username}`, "ok");
  else toast(d.error || "Reset failed", "err");
}

export async function showCreateOrgModal() {
  const m = document.getElementById("create-org-modal");
  if (m) m.style.display = "flex";
}

export async function createOrg() {
  const name = document.getElementById("co-name")?.value.trim() || "";
  const chatId = document.getElementById("co-chat-id")?.value.trim() || "";
  const username = document.getElementById("co-admin-username")?.value.trim() || "";
  const password = document.getElementById("co-admin-password")?.value || "";
  const industry = document.getElementById("co-industry")?.value.trim() || "";
  const size = document.getElementById("co-size")?.value.trim() || "";
  const backupChannelId = document.getElementById("co-backup-channel-id")?.value.trim() || "";

  if (!name || !chatId || !username || !password) {
    toast("Org name, Channel ID, Admin username and password are required", "err");
    return;
  }
  if (password.length < 6) {
    toast("Admin password must be at least 6 characters", "err");
    return;
  }

  const body = {
    org_name: name,
    chat_id: chatId,
    username,
    password,
    industry,
    size,
  };
  if (backupChannelId) body.backup_channel_id = backupChannelId;

  const r = await fetch(`${API}/orgs/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) {
    let msg = `Organisation "${name}" created`;
    if (d.backup_channel_id) msg += ` · backup channel ${d.backup_channel_id}`;
    if (d.warning) msg += ` · ⚠ ${d.warning}`;
    toast(msg, d.warning ? "warn" : "ok");
    // reset form + close
    ["co-name", "co-chat-id", "co-admin-username", "co-admin-password", "co-industry", "co-size", "co-backup-channel-id"]
      .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
    const m = document.getElementById("create-org-modal");
    if (m) m.style.display = "none";
    loadOrgs();
  } else {
    toast(d.error || "Failed to create organisation", "err");
  }
}

export async function showSetBackupModal(orgId, orgName) {
  const existing = prompt(`Set backup channel for "${orgName}"\n\nEnter a numeric Telegram channel ID (leave blank to clear):`);
  if (existing === null) return; // cancelled
  const r = await fetch(`${API}/orgs/${orgId}/backup-channel`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ backup_channel_id: existing.trim() }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) {
    toast(`Backup channel ${d.backup_channel_id ? "set to " + d.backup_channel_id : "cleared"}`, "ok");
    loadOrgs();
  } else {
    toast(d.error || "Failed to set backup channel", "err");
  }
}

// Kept for backwards-compat with inline onclick (uses prompt above)
export async function setBackupChannel(orgId) {
  showSetBackupModal(orgId, "");
}
