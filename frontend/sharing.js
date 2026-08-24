// frontend/sharing.js — share modal, email sharing
import { API, state, toast, fmtDate, escapeHtml, openModal } from "./api.js";

let _shareFileId = null;
let _shareFileName = "";

export function shareFile(fileId, filename) {
  _shareFileId = fileId;
  _shareFileName = filename;
  document.getElementById("share-link-input").value = "";
  document.getElementById("share-expiry").value = 7;
  document.getElementById("share-password").value = "";
  document.getElementById("share-existing-links").innerHTML = "";
  openModal("share-modal");
  loadExistingShares(fileId);
}

export async function loadExistingShares(fileId) {
  const r = await fetch(`${API}/files/${fileId}/shares`, {credentials: "same-origin"});
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    if (r.status !== 401) toast(d.error || "Failed to load links", "err");
    return;
  }
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
      <span style="flex:1;font-family:var(--mono);font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(window.location.origin)}/shared/${escapeHtml(s.token)}</span>
      <span style="font-size:11px;color:var(--muted);min-width:60px">${s.download_count || 0} DLs</span>
      <span style="font-size:11px;color:var(--muted);min-width:80px">${escapeHtml(exp)}</span>
      <button class="btn-sm danger" onclick="deleteShare('${s.id}','${fileId}')" style="font-size:10px">✕</button>`;
    container.appendChild(row);
  }
}

export async function createShareLink() {
  if (!_shareFileId) return;
  const daysEl = document.getElementById("share-expiry");
  let days = parseInt(daysEl.value, 10);
  if (isNaN(days) || days < 1) days = 7;
  if (days > 365) days = 365;
  daysEl.value = days;
  const password = document.getElementById("share-password").value;
  if (password && password.length > 128) { toast("Password too long (max 128)", "err"); return; }
  const btn = document.querySelector("#share-modal .btn-primary");
  if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
  try {
    const r = await fetch(`${API}/files/${_shareFileId}/share`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({expires_days: days, password})
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.token) {
      const url = `${window.location.origin}/shared/${d.token}`;
      document.getElementById("share-link-input").value = url;
      document.getElementById("share-expires").textContent = d.expires_at ? fmtDate(d.expires_at) : "never";
      const dl = document.getElementById("share-downloads");
      if (dl) dl.textContent = "0";
      toast("Share link created");
      loadExistingShares(_shareFileId);
    } else {
      toast(d.error || `Failed to create link (HTTP ${r.status})`, "err");
    }
  } catch (e) {
    toast("Network error creating link", "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Generate Link"; }
  }
}

export function copyShareLink() {
  const input = document.getElementById("share-link-input");
  if (!input.value) return;
  navigator.clipboard.writeText(input.value).then(() => toast("Link copied!")).catch(() => {
    input.select();
    document.execCommand("copy");
    toast("Link copied!");
  });
}

export async function deleteShare(shareId, fileId) {
  const r = await fetch(`${API}/files/${fileId}/shares/${shareId}`, {method: "DELETE", credentials: "same-origin"});
  if (r.ok) { loadExistingShares(fileId); toast("Link removed"); }
  else { const d = await r.json().catch(()=>({})); toast(d.error||"Failed to remove link","err"); }
}

// ── EMAIL SHARING ───────────────────────────────────────────────────────────
let _emailFileId = null;
let _emailFileName = "";

export function emailFile(fileId, filename) {
  _emailFileId = fileId;
  _emailFileName = filename;
  document.getElementById("email-recipients").value = "";
  document.getElementById("email-message").value = "";
  openModal("email-modal");
}

export async function sendEmail(event) {
  if (!_emailFileId) return;
  const recipients = document.getElementById("email-recipients").value.trim();
  const message = document.getElementById("email-message").value;
  if (!recipients) { toast("Enter at least one recipient", "err"); return; }
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  const r = await fetch(`${API}/files/${_emailFileId}/email`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    credentials: "same-origin",
    body: JSON.stringify({recipients, message})
  });
  if (btn) { btn.disabled = false; btn.textContent = "Send"; }
  if (r.ok) { window.closeModal("email-modal"); toast("Email sent!"); }
  else { const d = await r.json(); toast(d.error || "Failed to send", "err"); }
}

if (typeof window !== "undefined") {
  window.shareFile = shareFile;
  window.loadExistingShares = loadExistingShares;
  window.createShareLink = createShareLink;
  window.copyShareLink = copyShareLink;
  window.deleteShare = deleteShare;
  window.emailFile = emailFile;
  window.sendEmail = sendEmail;
}
