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
      <span style="flex:1;font-family:var(--mono);font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(window.location.origin)}/shared/${escapeHtml(s.token)}</span>
      <span style="font-size:11px;color:var(--muted);min-width:60px">${s.download_count || 0} DLs</span>
      <span style="font-size:11px;color:var(--muted);min-width:80px">${escapeHtml(exp)}</span>
      <button class="btn-sm danger" onclick="deleteShare('${s.id}','${fileId}')" style="font-size:10px">✕</button>`;
    container.appendChild(row);
  }
}

export async function createShareLink() {
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
    const dl = document.getElementById("share-downloads");
    if (dl) dl.textContent = "0";
    toast("Share link created");
    loadExistingShares(_shareFileId);
  } else {
    const d = await r.json();
    toast(d.error || "Failed to create link", "err");
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
  const r = await fetch(`${API}/files/${fileId}/shares/${shareId}`, {method: "DELETE"});
  if (r.ok) { loadExistingShares(fileId); toast("Link removed"); }
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
