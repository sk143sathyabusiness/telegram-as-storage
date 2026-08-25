// frontend/folders.js — sidebar CRUD, breadcrumb, navigation
import { API, state, toast, showSkeleton } from "./api.js";

export function buildPath(folderId) {
  if (!folderId) return "~/";
  const parts = [];
  let cur = folderId;
  while (cur && state.folderMap[cur]) {
    parts.unshift(state.folderMap[cur].name);
    cur = state.folderMap[cur].parent_id;
  }
  return "~/" + parts.join("/") + "/";
}

export function updateBreadcrumb() {
  const path = buildPath(state.currentFolderId);
  const bc = document.getElementById("breadcrumb");
  if (!bc) return;
  const idx = path.lastIndexOf("/", path.length - 2);
  if (idx <= 0) {
    bc.innerHTML = `<span>${path}</span>`;
  } else {
    bc.innerHTML = `${path.slice(0, idx + 1)}<span>${path.slice(idx + 1)}</span>`;
  }
}

export async function loadFolders() {
  const r = await fetch(API + "/folders");
  if (!r.ok) return;
  const folders = await r.json();
  // reset and repopulate live map
  Object.keys(state.folderMap).forEach(k => delete state.folderMap[k]);
  folders.forEach(f => { state.folderMap[f.id] = f; });
  window.folderMap = state.folderMap;
  renderFolderTree(folders);
}

export function renderFolderTree(folders) {
  const tree = document.getElementById("folder-tree");
  if (!tree) return;
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
  item.className = "sidebar-item" + (state.currentFolderId === folder.id ? " active" : "");
  item.style.paddingLeft = (8 + depth * 12) + "px";
  const canDelete = state.currentUser && (state.currentUser.role === "org_admin" || state.currentUser.role === "master_admin");
  const isEssential = folder.is_essential;
  item.innerHTML = `<span class="icon">📁</span> <span class="folder-label">${folder.name}</span>`
    + (isEssential ? `<span title="Essential (daily backup)" style="margin-left:auto;font-size:11px">★</span>` : ``)
    + (canDelete ? `<button type="button" class="folder-del-btn" title="Delete folder" data-fid="${folder.id}" data-fname="${folder.name.replace(/"/g,'&quot;')}">🗑</button>` : ``)
    + (canDelete ? `<button type="button" class="folder-ess-btn" title="${isEssential ? 'Remove from essential (daily backup)' : 'Mark as essential (daily backup)'}" data-fid="${folder.id}">${isEssential ? "☆" : "★"}</button>` : ``);
  item.onclick = () => navigateFolder(folder.id, folder.name);
  wrap.appendChild(item);
  if (children[folder.id]) {
    children[folder.id].forEach(c => wrap.appendChild(makeFolderNode(c, children, depth + 1)));
  }
  if (state.currentUser && state.currentUser.role !== "read_only") {
    const add = document.createElement("div");
    add.className = "folder-add";
    add.style.paddingLeft = (8 + (depth + 1) * 12) + "px";
    add.innerHTML = `<span>＋</span> subfolder`;
    add.onclick = (e) => { e.stopPropagation(); promptNewFolder(folder.id); };
    wrap.appendChild(add);
  }
  return wrap;
}

// Event delegation for inline delete buttons
document.addEventListener("click", e => {
  const del = e.target.closest(".folder-del-btn");
  if (del) {
    e.stopPropagation();
    deleteFolder(del.dataset.fid, del.dataset.fname);
    return;
  }
  const ess = e.target.closest(".folder-ess-btn");
  if (ess) {
    e.stopPropagation();
    toggleFolderEssential(ess.dataset.fid);
  }
});

export async function toggleFolderEssential(folderId) {
  const r = await fetch(`${API}/folders/${folderId}/essential`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({}),
  });
  const d = await r.json().catch(() => ({}));
  if (r.ok) {
    toast(d.is_essential ? "Marked essential (daily backup)" : "Removed from essential", "ok");
    await loadFolders();
  } else toast(d.error || "Could not update folder", "err");
}

export function navigateFolder(id, name) {
  state.currentFolderId = id;
  state.currentFolderName = name;
  window.currentFolderId = id;
  window.currentFolderName = name;
  const titleEl = document.getElementById("folder-title");
  if (titleEl) titleEl.textContent = name;
  updateBreadcrumb();
  document.querySelectorAll(".sidebar-item").forEach(el => el.classList.remove("active"));
  const rootItem = document.getElementById("folder-root");
  if (id === null) {
    rootItem?.classList.add("active");
    document.getElementById("nav-files")?.classList.add("active");
  } else {
    document.getElementById("nav-files")?.classList.add("active");
  }
  showSkeleton();
  loadFolders();
  showView(state.currentView || "files");
  // delegate refreshFiles to files.js
  import("./files.js").then(m => m.refreshFiles());
}

export async function promptNewFolder(parentId) {
  const name = prompt("Folder name:");
  if (!name) return;
  const r = await fetch(API + "/folders", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, parent_id: parentId})
  });
  if (r.ok) { await loadFolders(); toast("Folder created"); }
  else { const d = await r.json().catch(() => ({})); toast(d.error || "Could not create folder", "err"); }
}

export async function deleteFolder(folderId, folderName) {
  if (!confirm(`Delete folder "${folderName}"? It must be empty (no files or subfolders).`)) return;
  const r = await fetch(`${API}/folders/${folderId}`, {method: "DELETE"});
  if (r.ok) {
    if (state.currentFolderId === folderId) {
      state.currentFolderId = null;
      state.currentFolderName = "~";
      window.currentFolderId = null;
      window.currentFolderName = "~";
      const titleEl = document.getElementById("folder-title");
      if (titleEl) titleEl.textContent = "Root";
      updateBreadcrumb();
    }
    await loadFolders();
    const m = await import("./files.js");
    m.refreshFiles();
    toast(`Folder "${folderName}" deleted`);
  } else {
    const d = await r.json().catch(() => ({}));
    toast(d.error || "Could not delete folder", "err");
  }
}

// Aliases to match brief's expected export names
export const listFolders = loadFolders;

// Window exposure for inline handlers
if (typeof window !== "undefined") {
  window.loadFolders = loadFolders;
  window.listFolders = loadFolders;
  window.renderFolderTree = renderFolderTree;
  window.navigateFolder = navigateFolder;
  window.promptNewFolder = promptNewFolder;
  window.deleteFolder = deleteFolder;
  window.toggleFolderEssential = toggleFolderEssential;
  window.buildPath = buildPath;
  window.updateBreadcrumb = updateBreadcrumb;
  window.folderMap = state.folderMap;
  window.currentFolderId = state.currentFolderId;
  window.currentFolderName = state.currentFolderName;
}
