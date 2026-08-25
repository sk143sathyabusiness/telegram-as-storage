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
    card.innerHTML = `
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;color:var(--text)">${escapeHtml(o.name)}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--mono)">${escapeHtml(o.id || "")} · <span style="color:${statusColor}">${escapeHtml(o.status || "—")}</span> · chat: ${escapeHtml(o.telegram_chat_id || "—")}</div>
        <div style="font-size:11px;color:var(--muted)">${escapeHtml(o.industry || "")} ${escapeHtml(o.size || "")}</div>
      </div>
      <div style="display:flex;gap:6px">
        ${o.status !== "approved" && o.status !== "active" ? `<button class="btn-sm active" onclick="approveOrg('${o.id}')">Approve</button>` : ""}
        ${o.status !== "rejected" ? `<button class="btn-sm danger" onclick="rejectOrg('${o.id}')">Reject</button>` : ""}
      </div>`;
    list.appendChild(card);
  }
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

if (typeof window !== "undefined") {
  window.loadOrgs = loadOrgs;
  window.approveOrg = approveOrg;
  window.rejectOrg = rejectOrg;
}
