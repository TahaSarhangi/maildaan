/* MailDaan — bilingual frontend controller (vanilla JS) */

const PAGE_SIZE = 50;

const state = {
  lang: "en",
  locale: {},
  configured: false,
  view: "language",
  syncRunning: false,
  syncReport: null,
  filters: { search: "", folder_id: null, date_from: null, date_to: null, party: "" },
  sort: "newest",
  page: 1,
  total: 0,
  folderDisplay: {},
  busy: false,
};

/* ------------------------------------------------------------------ helpers */

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function toDigits(text) {
  if (state.lang !== "fa") return String(text);
  const map = "۰۱۲۳۴۵۶۷۸۹";
  return String(text).replace(/[0-9]/g, (d) => map[+d]);
}

function t(path, fallback) {
  const parts = path.split(".");
  let node = state.locale;
  for (const p of parts) {
    if (node && Object.prototype.hasOwnProperty.call(node, p)) node = node[p];
    else return fallback != null ? fallback : path;
  }
  return node;
}

function formatDate(value) {
  if (!value) return "—";
  return toDigits(value);
}

function formatDuration(seconds) {
  seconds = Math.round(seconds || 0);
  if (seconds < 60) return toDigits(seconds) + " " + t("common.seconds", "s");
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return toDigits(m) + ":" + String(s).padStart(2, "0");
}

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

function $(id) { return document.getElementById(id); }

function show(screen) {
  for (const el of document.querySelectorAll("main > section")) el.classList.add("hidden");
  const target = $("screen" + screen.charAt(0).toUpperCase() + screen.slice(1));
  if (target) target.classList.remove("hidden");
  const chrome = ["Archive", "Viewer", "Sync", "Settings"].includes(screen);
  $("appHeader").classList.toggle("hidden", !chrome);
  $("appFooter").classList.toggle("hidden", !chrome);
  state.view = screen.toLowerCase();
}

async function loadLocale(lang) {
  try {
    const resp = await fetch("locales/" + lang + ".json", { cache: "no-store" });
    state.locale = await resp.json();
  } catch (e) {
    state.locale = {};
  }
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    el.textContent = t(key, el.textContent);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    const key = el.getAttribute("data-i18n-ph");
    el.placeholder = t(key, el.placeholder);
  });
  const ph = $("searchBox");
  if (ph) ph.placeholder = t("list.searchPlaceholder", "Search…");
  document.documentElement.lang = state.lang;
  document.body.dir = state.lang === "fa" ? "rtl" : "ltr";
  document.title = t("appName", "MailDaan");
}

/* ------------------------------------------------------------------- modals */

function showModal(title, bodyHtml, buttons) {
  const overlay = $("modalOverlay");
  $("modalCard").innerHTML =
    '<h3 class="modal-title">' + title + '</h3>' +
    '<div class="modal-body">' + bodyHtml + "</div>" +
    '<div class="modal-actions"></div>';
  const actions = $("modalCard").querySelector(".modal-actions");
  buttons.forEach((btn) => {
    const node = document.createElement("button");
    node.textContent = btn.label;
    node.className = btn.primary ? "primary-btn" : "toolbar-btn";
    if (btn.danger) node.classList.add("danger");
    node.addEventListener("click", () => {
      if (!btn.keepOpen) hideModal();
      if (btn.onClick) btn.onClick();
    });
    actions.appendChild(node);
  });
  overlay.classList.remove("hidden");
}

function hideModal() { $("modalOverlay").classList.add("hidden"); }

function infoModal(title, message) {
  showModal(title, esc(message), [{ label: t("common.ok", "OK"), primary: true }]);
}

/* ------------------------------------------------------------------- header */

function updateNav(active) {
  $("navArchive").classList.toggle("active", active === "archive");
  $("navSync").classList.toggle("active", active === "sync");
  $("navSettings").classList.toggle("active", active === "settings");
}

function bindHeader() {
  $("navArchive").addEventListener("click", () => { updateNav("archive"); show("Archive"); loadFolders(); loadMessages(); });
  $("navSync").addEventListener("click", () => { updateNav("sync"); show("Sync"); renderSyncScreen(); });
  $("navSettings").addEventListener("click", () => { updateNav("settings"); show("Settings"); loadSettingsForm(); });
  $("btnExport").addEventListener("click", onExportExcel);
  $("langSelect").addEventListener("change", async (e) => {
    await api().setLanguage(e.target.value);
    await applyLanguage(e.target.value);
  });
  $("devLink").addEventListener("click", (e) => {
    e.preventDefault();
    api().openExternal("https://github.com/TahaSarhangi");
  });
}

async function applyLanguage(lang) {
  state.lang = lang;
  await loadLocale(lang);
  state.folderDisplay = (state.locale.folders && state.locale.folders.map) || {};
  applyTranslations();
  if (state.view === "archive") { loadFolders(); loadMessages(); }
  if (state.view === "sync") renderSyncScreen();
  if (state.view === "viewer") loadMessages();
}

/* ------------------------------------------------------------------ folders */

async function loadFolders() {
  const folders = await api().listFolders();
  const stats = await api().getStats();
  const allName = t("folders.allEmails", "All Emails");
  const activeId = state.filters.folder_id;
  let html =
    '<li class="folder-item' + (activeId == null ? " active" : "") + '" data-id="">' +
    '<span class="folder-name">' + esc(allName) + "</span>" +
    '<span class="folder-count">' + toDigits(stats.messages || 0) + "</span></li>";
  for (const f of folders) {
    const name = state.folderDisplay[f.display_name] || f.display_name;
    html +=
      '<li class="folder-item' + (activeId === f.id ? " active" : "") +
      (f.has_error ? " has-error" : "") + '" data-id="' + f.id + '">' +
      '<span class="folder-name">' + esc(name) + "</span>" +
      '<span class="folder-count" title="' + esc(f.imap_name) + '">' +
      toDigits(f.count || 0) + "</span></li>";
  }
  const list = $("folderList");
  list.innerHTML = html;
  list.querySelectorAll(".folder-item").forEach((item) => {
    item.addEventListener("click", () => {
      const id = item.getAttribute("data-id");
      state.filters.folder_id = id ? Number(id) : null;
      state.page = 1;
      loadFolders();
      loadMessages();
    });
  });
  const sel = $("filterFolder");
  if (sel && sel.options.length <= 1) {
    sel.innerHTML = '<option value="">' + esc(t("filters.anyFolder", "All folders")) + "</option>";
    for (const f of folders) {
      const opt = document.createElement("option");
      opt.value = f.id;
      opt.textContent = state.folderDisplay[f.display_name] || f.display_name;
      sel.appendChild(opt);
    }
  }
}

/* ------------------------------------------------------------------ messages */

async function loadMessages() {
  const filters = currentFilters();
  const result = await api().queryMessages(JSON.stringify(filters), state.sort, state.page, PAGE_SIZE);
  state.total = result.total;
  const list = $("messageList");
  if (!result.rows.length) {
    list.innerHTML = '<div class="empty-state">' +
      (state.total === 0 ? esc(t("list.emptyArchive", "Empty")) : esc(t("list.noResults", "No results"))) +
      "</div>";
    renderPagination();
    return;
  }
  let html = "";
  for (const m of result.rows) {
    const sender = m.sender_name || m.sender_email || "—";
    const subject = m.subject || t("viewer.noSubject", "(No subject)");
    const att = m.att_count
      ? '<span class="att-badge">📎 ' + toDigits(m.att_count) + "</span>"
      : "";
    html +=
      '<div class="message-row" data-id="' + m.id + '">' +
      '<div class="msg-sender">' + esc(sender) + "</div>" +
      '<div class="msg-date">' + formatDate(m.date_utc) + "</div>" +
      '<div class="msg-subject">' + esc(subject) + "</div>" +
      '<div class="msg-meta">' + att + "</div>" +
      '<div class="msg-preview">' + esc(m.preview || m.plain_body || "") + "</div>" +
      "</div>";
  }
  list.innerHTML = html;
  list.querySelectorAll(".message-row").forEach((row) => {
    row.addEventListener("click", () => openMessage(Number(row.getAttribute("data-id"))));
  });
  renderPagination();
}

function currentFilters() {
  const f = $("filterFrom"), tt = $("filterTo");
  return {
    search: state.filters.search,
    folder_id: state.filters.folder_id,
    date_from: f && f.value ? Math.floor(new Date(f.value + "T00:00:00").getTime() / 1000) : null,
    date_to: tt && tt.value ? Math.floor(new Date(tt.value + "T23:59:59").getTime() / 1000) : null,
    party: state.filters.party,
  };
}

function renderPagination() {
  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  const cur = Math.min(state.page, pages);
  $("pagination").innerHTML =
    '<button id="pgPrev" ' + (cur <= 1 ? "disabled" : "") + ">‹ " + t("list.prev", "Previous") + "</button>" +
    "<span>" + t("list.page", "Page") + " " + toDigits(cur) + " " + t("list.of", "of") + " " +
    toDigits(pages) + " — " + toDigits(state.total) + " " + t("folders.messages", "messages") + "</span>" +
    '<button id="pgNext" ' + (cur >= pages ? "disabled" : "") + ">" + t("list.next", "Next") + " ›</button>";
  const prev = $("pgPrev"), next = $("pgNext");
  if (prev) prev.addEventListener("click", () => { state.page = cur - 1; loadMessages(); });
  if (next) next.addEventListener("click", () => { state.page = cur + 1; loadMessages(); });
}

/* ------------------------------------------------------------------- viewer */

async function openMessage(id) {
  const m = await api().getMessage(id);
  if (!m) { infoModal("Error", "Message not found."); return; }
  const subject = m.subject || t("viewer.noSubject", "(No subject)");
  let meta = "";
  const addRow = (label, value) => {
    if (value) meta += "<dt>" + esc(label) + '</dt><dd dir="auto">' + esc(value) + "</dd>";
  };
  addRow(t("viewer.from", "From"), (m.sender_name ? m.sender_name + " " : "") + "<" + (m.sender_email || "") + ">");
  ["to", "cc", "bcc"].forEach((kind) => {
    const label = kind === "to" ? t("viewer.to", "To")
      : kind === "cc" ? t("viewer.cc", "CC") : t("viewer.bcc", "BCC");
    const group = (m.recipients || []).filter((r) => r.rtype === kind);
    if (group.length) addRow(label, group.map((r) => (r.name ? r.name + " " : "") + "<" + r.email + ">").join("; "));
  });
  addRow(t("viewer.date", "Date"), m.date_utc);
  addRow(t("viewer.folders", "Folders"), m.folder_names);

  let bodyHtml = "";
  if (m.plain_body && m.plain_body.trim()) {
    bodyHtml = '<div class="viewer-body" dir="auto">' + esc(m.plain_body) + "</div>";
  } else if (m.html_body && m.html_body.trim()) {
    bodyHtml =
      '<p class="html-notice">' + esc(t("viewer.htmlNotice", "HTML only")) + "</p>" +
      '<iframe class="html-frame" id="htmlFrame" sandbox="" srcdoc="' + esc(sanitizeHtml(m.html_body)) +
      '"></iframe>';
  } else {
    bodyHtml = '<div class="viewer-body" dir="auto">' + esc(m.plain_body || "") + "</div>";
  }

  let attHtml = "";
  if (m.attachments && m.attachments.length) {
    attHtml = '<div class="viewer-label">' + esc(t("viewer.attachments", "Attachments")) + "</div>" +
      '<div class="attachment-list">' +
      m.attachments.map((a) =>
        '<div class="attachment-chip"><span>' + esc(a.filename || a.filename_safe) +
        " (" + toDigits(a.size || 0) + ' B)</span>' +
        '<button data-att="' + a.id + '">' + esc(t("viewer.saveAttachment", "Save")) + "</button></div>"
      ).join("") +
      '<div class="attachment-chip"><button id="btnSaveAllAtt">' +
      esc(t("viewer.saveAllAttachments", "Save All")) + "</button></div></div>";
  }

  $("viewerContent").innerHTML =
    '<div class="viewer-card">' +
    '<h2 class="viewer-subject" dir="auto">' + esc(subject) + "</h2>" +
    '<dl class="viewer-meta">' + meta + "</dl>" + bodyHtml + attHtml +
    '<div class="viewer-actions">' +
    '<button class="primary-btn" id="btnSaveEml">' + esc(t("viewer.saveEml", "Save EML")) + "</button>" +
    "</div></div>";
  show("Viewer");
  document.querySelectorAll("#viewerContent [data-att]").forEach((btn) => {
    btn.addEventListener("click", () => saveAttachment(id, Number(btn.getAttribute("data-att"))));
  });
  const saveAll = $("btnSaveAllAtt");
  if (saveAll) saveAll.addEventListener("click", () => api().saveAllAttachments(id).then((r) => fileResult(r)));
  $("btnSaveEml").addEventListener("click", () => api().saveEml(id).then((r) => fileResult(r)));
  $("btnBack").textContent = "";
  $("btnBack").innerHTML = (state.lang === "fa" ? "›" : "‹") + " " + esc(t("nav.archive", "Archive"));
}

function sanitizeHtml(html) {
  let out = String(html || "");
  out = out.replace(/<\s*(script|style|iframe|object|embed|link|meta|base|form)\b[\s\S]*?<\/\s*\1\s*>/gi, "");
  out = out.replace(/<\s*(script|style|iframe|object|embed|link|meta|base|form)\b[^>]*>/gi, "");
  out = out.replace(/<!--[\s\S]*?-->/g, "");
  out = out.replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  out = out.replace(/\s(src|href|srcdoc|background|data|poster)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi,
    (match, attr, val) => {
      const v = val.replace(/^["']|["']$/g, "").trim();
      if (/^(cid:|data:image\/)/i.test(v)) return match;
      return " " + attr + '="#"';
    });
  return out;
}

async function saveAttachment(messageId, attachmentId) {
  const r = await api().saveAttachment(messageId, attachmentId);
  fileResult(r);
}

function fileResult(r) {
  if (!r) return;
  if (r.error) infoModal(t("common.saveFailed", "Save failed"), r.error);
  else infoModal(t("common.ok", "OK"), t("common.savedToFile", "Saved to:") + "\n" + r.path);
}

/* --------------------------------------------------------------------- sync */

function bindConfigForm() {
  $("configForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const host = $("cfgHost").value.trim();
    const port = Number($("cfgPort").value);
    const user = $("cfgUser").value.trim();
    const pass = $("cfgPass").value;
    const err = $("cfgError");
    const fail = (msg) => { err.textContent = msg; err.classList.remove("hidden"); };
    if (!host || !user || !pass) return fail(t("config.fillAllFields", "Fill all fields"));
    if (!(port >= 1 && port <= 65535)) return fail(t("config.invalidPort", "Invalid port"));
    err.classList.add("hidden");
    await api().saveImapSettings(host, port, user, pass);
    state.configured = true;
    updateNav("sync");
    show("Sync");
    startSync(pass);
  });
}

function askPassword(title, body, onSubmit) {
  showModal(title, body +
    '<div style="margin-top:10px"><input type="password" id="modalPassword" ' +
    'placeholder="' + esc(t("config.password", "Password")) +
    '" style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:7px"></div>',
    [
      { label: t("common.cancel", "Cancel") },
      {
        label: t("nav.syncEmails", "Sync"), primary: true, keepOpen: false, onClick: () => {
          const node = $("modalPassword");
          const pw = node ? node.value : "";
          onSubmit(pw);
        }
      }
    ]);
}

function renderSyncScreen() {
  const card = $("syncCard");
  if (state.syncReport) { renderReport(state.syncReport); return; }
  if (state.syncRunning) { pollProgress(); return; }
  card.innerHTML =
    '<h1 class="sync-title">' + esc(t("sync.title", "Sync")) + "</h1>" +
    '<p class="sync-phase">' + esc(t("sync.title", "Sync")) + "</p>" +
    '<div class="sync-actions"><button class="primary-btn" id="btnStartSync">' +
    esc(t("nav.syncEmails", "Sync Emails")) + "</button></div>";
  $("btnStartSync").addEventListener("click", () => {
    if (!state.configured) { updateNav("archive"); show("Config"); return; }
    askPassword(
      t("sync.title", "Sync"),
      esc(t("config.password", "Password")) + " — <b>" + esc(state.username || "") + "</b>",
      (pw) => startSync(pw)
    );
  });
}

async function startSync(password) {
  state.syncReport = null;
  if (!password) { renderSyncScreen(); return; }
  state.syncRunning = true;
  if ($("btnStartSync")) $("btnStartSync").disabled = true;
  const res = await api().startSync(password);
  if (res && res.error) {
    state.syncRunning = false;
    infoModal(t("sync.statusError", "Failed"), res.error);
    renderSyncScreen();
    return;
  }
  pollProgress();
}

function renderProgress(s) {
  const card = $("syncCard");
  const folderName = state.folderDisplay[s.current_folder] || s.current_folder || "—";
  card.innerHTML =
    '<h1 class="sync-title">' + esc(t("sync.title", "Sync")) + "</h1>" +
    '<p class="sync-phase">' + esc(s.running
      ? t("sync.preparing", "Preparing…")
      : t("sync.statusRunning", "Running")) + "</p>" +
    makeProgress(t("sync.title", "Sync"), s.total_processed, s.total_pending, s.overall_percent) +
    makeProgress(folderName, s.folder_processed, s.folder_pending, s.folder_percent, true) +
    '<div class="progress-grid">' +
    '<div>' + esc(t("sync.foldersProgress", "Folders")) + ': <b>' + toDigits(s.folders_done) + "/" +
      toDigits(s.folders_total) + "</b></div>" +
    '<div>' + esc(t("sync.speed", "Speed")) + ': <b>' + toDigits(s.speed) + " " +
      esc(t("common.messagesPerSecond", "msg/s")) + "</b></div>" +
    '<div>' + esc(t("sync.eta", "ETA")) + ': <b>' + formatDuration(s.eta_seconds) + "</b></div>" +
    '<div>' + esc(t("sync.processed", "Processed")) + ': <b>' + toDigits(s.total_processed) +
      " / " + toDigits(s.total_pending) + "</b></div>" +
    "</div>" +
    (s.last_error ? '<div class="sync-error">' + esc(s.last_error) + "</div>" : "") +
    '<div class="sync-actions"><button class="primary-btn danger" id="btnCancelSync">' +
    esc(t("sync.cancel", "Cancel")) + "</button></div>";
  $("btnCancelSync").addEventListener("click", () => {
    showModal(t("sync.cancel", "Cancel"), esc(t("sync.cancelConfirm", "Cancel?")), [
      { label: t("common.no", "No"), primary: true, keepOpen: false },
      { label: t("common.yes", "Yes"), danger: true, onClick: () => api().cancelSync() },
    ]);
  });
}

function makeProgress(label, done, total, percent, secondary) {
  return '<div class="progress-block">' +
    '<div class="progress-label"><span>' + esc(label) + "</span>" +
    '<span class="pct">' + toDigits(percent || 0) + "%</span></div>" +
    '<div class="progress-track"><div class="progress-fill' +
    (secondary ? " secondary" : "") + '" style="width:' + (percent || 0) + '%"></div></div>' +
    '<div class="progress-label" style="margin-top:3px"><span>' + toDigits(done) + " / " +
    toDigits(total) + "</span></div></div>";
}

let pollTimer = null;
async function pollProgress() {
  if (pollTimer) clearTimeout(pollTimer);
  if (state.view !== "sync") { pollTimer = setTimeout(pollProgress, 700); return; }
  const s = await api().getSyncProgress();
  if (state.view === "sync" && state.syncRunning) renderProgress(s);
  if (s.running) {
    pollTimer = setTimeout(pollProgress, 500);
  } else {
    state.syncRunning = false;
    const rep = state.pendingReport || s;
    state.pendingReport = null;
    if (rep && rep.status && rep.status !== "idle") renderReport(rep);
    else renderSyncScreen();
  }
}

function renderReport(r) {
  const statusKey = {
    completed: "statusCompleted", incomplete: "statusIncomplete",
    cancelled: "statusCancelled", error: "statusError", running: "statusRunning",
  }[r.status] || "statusCompleted";
  const card = $("syncCard");
  card.innerHTML =
    '<h1 class="sync-title">' + esc(t("report.title", "Report")) + "</h1>" +
    '<p><span class="status-pill ' + (r.status || "completed") + '">' +
    esc(t("sync." + statusKey, r.status)) + "</span></p>" +
    '<div class="report-list">' +
    reportRow(t("report.foldersProcessed", "Folders"), r.folders_processed || 0) +
    reportRow(t("report.newMessages", "New"), r.new_messages || 0) +
    reportRow(t("report.deduped", "Deduped"), r.deduped || 0) +
    reportRow(t("report.archived", "Archived"), r.total_messages || 0) +
    reportRow(t("report.contacts", "Contacts"), r.total_contacts || 0) +
    reportRow(t("report.attachments", "Attachments"), r.total_attachments || 0) +
    reportRow(t("report.errors", "Errors"), r.errors || 0) +
    reportRow(t("report.elapsed", "Elapsed"), formatDuration(r.elapsed || 0)) +
    "</div>" +
    (r.error_folders && r.error_folders.length
      ? '<div class="sync-error">' + esc(t("report.errorFolders", "Error folders")) + ": " +
        esc(r.error_folders.join(", ")) + "</div>" : "") +
    (r.last_error ? '<div class="sync-error">' + esc(r.last_error) + "</div>" : "") +
    '<div class="sync-actions">' +
    '<button class="primary-btn" id="btnOpenViewer">' + esc(t("report.openViewer", "Open")) + "</button>" +
    '<button class="toolbar-btn" id="btnSyncAgain">' + esc(t("report.syncAgain", "Sync again")) + "</button>" +
    "</div>";
  $("btnOpenViewer").addEventListener("click", () => {
    state.syncReport = null;
    updateNav("archive");
    show("Archive");
    loadFolders();
    loadMessages();
  });
  $("btnSyncAgain").addEventListener("click", () => {
    askPassword(t("sync.title", "Sync"), esc(t("config.password", "Password")), (pw) => startSync(pw));
  });
}

function reportRow(label, value) {
  return '<div class="report-row"><span>' + esc(label) + '</span><b>' +
    (typeof value === "number" ? toDigits(value) : esc(value)) + "</b></div>";
}

/* ------------------------------------------------------------------- export */

async function onExportExcel() {
  if (state.busy) return;
  state.busy = true;
  const btn = $("btnExport");
  btn.disabled = true;
  const filters = (state.view === "archive" || state.view === "viewer") ? currentFilters() : {};
  const res = await api().exportExcel(JSON.stringify(filters), state.sort);
  btn.disabled = false;
  state.busy = false;
  if (!res) return;
  if (res.error) infoModal(t("export.failed", "Export failed"), res.error);
  else infoModal(t("export.done", "Done"),
    toDigits(res.messages) + " " + esc(t("export.messages", "messages")) + " " +
    toDigits(res.contacts) + " " + esc(t("export.contacts", "contacts")) + " " +
    toDigits(res.organizations) + " " + esc(t("export.organizations", "organizations")) +
    "<br>" + esc(t("common.savedToFile", "Saved to:")) + "<br>" + esc(res.path));
}

/* ----------------------------------------------------------------- settings */

async function loadSettingsForm() {
  const s = await api().getSettings();
  state.username = s.username;
  $("settingsLang").value = state.lang;
  $("setHost").value = s.imap_host || "";
  $("setPort").value = s.imap_port || 993;
  $("setUser").value = s.username || "";
}

function bindSettingsForm() {
  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("setError");
    const port = Number($("setPort").value);
    if (!$("setHost").value.trim() || !$("setUser").value.trim())
      return err.textContent = t("config.fillAllFields", "Fill all fields"), err.classList.remove("hidden");
    if (!(port >= 1 && port <= 65535))
      return err.textContent = t("config.invalidPort", "Invalid port"), err.classList.remove("hidden");
    err.classList.add("hidden");
    await api().saveImapSettings($("setHost").value.trim(), port, $("setUser").value.trim(), "");
    const lang = $("settingsLang").value;
    if (lang !== state.lang) {
      await api().setLanguage(lang);
      $("langSelect").value = lang;
      await applyLanguage(lang);
    }
    infoModal(t("settings.saved", "Saved"), t("settings.saved", "Saved"));
    updateNav("archive");
    show("Archive");
    loadFolders();
    loadMessages();
  });
}

/* -------------------------------------------------------------------- search */

function bindSearchAndFilters() {
  let timer = null;
  $("searchBox").addEventListener("input", (e) => {
    clearTimeout(timer);
    const value = e.target.value;
    timer = setTimeout(() => {
      state.filters.search = value;
      state.page = 1;
      if (state.view === "archive") loadMessages();
    }, 350);
  });
  $("sortSelect").addEventListener("change", (e) => {
    state.sort = e.target.value;
    if (state.view === "archive") loadMessages();
  });
  $("btnFilters").addEventListener("click", () => {
    $("filterPanel").classList.toggle("hidden");
  });
  $("btnApplyFilters").addEventListener("click", () => {
    state.filters.party = $("filterParty").value.trim();
    state.filters.folder_id = $("filterFolder").value ? Number($("filterFolder").value) : null;
    state.page = 1;
    loadFolders();
    loadMessages();
  });
  $("btnResetFilters").addEventListener("click", () => {
    $("filterFrom").value = "";
    $("filterTo").value = "";
    $("filterParty").value = "";
    $("filterFolder").value = "";
    state.filters = { search: "", folder_id: null, date_from: null, date_to: null, party: "" };
    $("searchBox").value = "";
    state.page = 1;
    loadFolders();
    loadMessages();
  });
}

/* ------------------------------------------------------------------- startup */

async function bootstrap() {
  bindHeader();
  bindConfigForm();
  bindSettingsForm();
  bindSearchAndFilters();
  $("btnBack").addEventListener("click", () => { updateNav("archive"); show("Archive"); loadFolders(); loadMessages(); });

  const initial = await api().getInitialState();
  state.configured = initial.configured;
  state.username = initial.username;
  state.lang = initial.language || "en";
  $("langSelect").value = state.lang;
  await loadLocale(state.lang);
  state.folderDisplay = (state.locale.folders && state.locale.folders.map) || {};
  applyTranslations();

  if (!initial.language) {
    show("Language");
    return;
  }
  if (!initial.configured) {
    if (initial.settings) {
      $("cfgHost").value = initial.settings.imap_host || "";
      $("cfgPort").value = initial.settings.imap_port || 993;
      $("cfgUser").value = initial.settings.username || "";
    }
    show("Config");
    return;
  }
  updateNav("archive");
  show("Archive");
  loadFolders();
  loadMessages();
}

document.addEventListener("DOMContentLoaded", () => {
  const start = () => { if (api()) bootstrap(); else setTimeout(start, 120); };
  if (window.pywebview && window.pywebview.readyState === 1) start();
  else {
    window.addEventListener("pywebviewready", start);
    setTimeout(start, 400);
  }
});

/* ---------------------------------------------------------- exit interception */

window.maildaan = {
  handleExitRequest: function () {
    showModal(t("common.confirmExit", "Exit"), t("sync.syncActiveExit", "Sync running"), [
      { label: t("common.no", "No"), primary: true },
      { label: t("common.yes", "Yes"), onClick: () => api().exitApp(true) },
    ]);
  },
  pushReport: function (reportJson) {
    try { state.pendingReport = JSON.parse(reportJson); } catch (e) { /* ignore */ }
  },
};
