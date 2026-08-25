// frontend/orgs.js — master_admin Organisations view
import { API, toast, escapeHtml } from "./api.js";

export async function loadOrgs() {
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
  list.innerHTML = "";
  if (!orgs.length) { if (empty) empty.style.display = ""; return; }
  if (empty) empty.style.display = "none";
  for (const o of orgs) {
    const card = document.createElement("div");
    card.className = "fa-card";
    card.style.cssText = "display:flex;align-items:center;gap:12px;padding:14px 16px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:12px;";
    const statusColor = o.status === "active" ? "var(--success)" : o.status === "approved" ? "var(--accent)" : "var(--muted)";
    const backupTxt = o.backup_channel_id ? `backup: ${escapeHtml(String(o.backup_channel_id))}` : "backup: — none —";
    card.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;color:var(--text)">${escapeHtml(o.name)}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${escapeHtml(o.id || "")} · <span style="color:${statusColor}">${escapeHtml(o.status || "—")}</span> · chat: ${escapeHtml(o.telegram_chat_id || "—")}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${backupTxt}</div>
        <div style="font-size:11px;color:var(--muted)">${escapeHtml(o.industry || "")} ${escapeHtml(o.size || "")}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn-sm" onclick="actAsOrg('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Act as</button>
        ${o.status !== "approved" && o.status !== "active" ? `<button class="btn-sm active" onclick="approveOrg('${o.id}')">Approve</button>` : ""}
        ${o.status !== "rejected" ? `<button class="btn-sm danger" onclick="rejectOrg('${o.id}')">Reject</button>` : ""}
        <button class="btn-sm" onclick="showSetBackupModal('${o.id}','${escapeHtml(o.name).replace(/'/g, "\\'")}')">Set backup</button>
      </div>`;
    list.appendChild(card);
  }
  updateMasterBanner();
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
