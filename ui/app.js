/* MailDaan — bilingual desktop frontend controller (vanilla JS) */

const PAGE_SIZE = 50;

const FOLDER_ICONS = {
  Inbox: "inbox", Sent: "send", Drafts: "edit", Trash: "trash",
  Junk: "spam", Spam: "spam", Archive: "archive", "All Mail": "mail",
  Important: "star", Starred: "star", Flagged: "star", Outbox: "send",
};

const state = {
  lang: "en",
  locale: {},
  theme: "light",
  accounts: [],
  activeAccountId: null,
  view: "language",
  accountMode: "add",          // 'first' (wizard) | 'add'
  syncRunning: false,
  syncAccountId: null,
  syncReport: null,
  pendingReport: null,
  pendingSwitchId: null,
  filters: { search: "", folder_id: null, date_from: null, date_to: null, party: "" },
  sort: "newest",
  page: 1,
  total: 0,
  folderDisplay: {},
  busy: false,
  lockEnabled: false,
  appPwMode: "enable",         // 'enable' | 'change'
  languageChosen: false,
  searchSeq: 0,
};

/* ------------------------------------------------------------------ helpers */

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function icon(name) {
  return '<svg class="ic"><use href="#i-' + name + '"/></svg>';
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
  return value ? toDigits(value) : "—";
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

function activeAccount() {
  return state.accounts.find((a) => a.id === state.activeAccountId) || null;
}

/* ------------------------------------------------------------------ routing */

const VIEW_TITLES = {
  archive: "nav.archive", sync: "nav.syncEmails", settings: "nav.settings",
  account: "accounts.add", language: "appName",
};

function show(view, opts) {
  opts = opts || {};
  state.view = view;
  for (const el of document.querySelectorAll("#content > section")) el.classList.add("hidden");
  const target = $("screen" + view.charAt(0).toUpperCase() + view.slice(1));
  if (target) target.classList.remove("hidden");
  const chrome = view !== "language" && opts.chrome !== false;
  $("sidebar").classList.toggle("hidden", !chrome);
  $("topbar").classList.toggle("hidden", !chrome);
  $("appFooter").classList.toggle("hidden", !chrome);
  updateNav();
  updateTopbar();
}

function updateNav() {
  $("navArchive").classList.toggle("active", state.view === "archive");
  $("navSync").classList.toggle("active", state.view === "sync");
  $("navSettings").classList.toggle("active", state.view === "settings");
}

function updateTopbar() {
  const key = VIEW_TITLES[state.view] || "appName";
  $("pageTitle").textContent = t(key, "MailDaan");
  const account = activeAccount();
  $("topbarAccount").classList.toggle("hidden", !account);
  $("topbarAccountName").textContent = account ? (account.display_name || account.email) : "";
  $("btnExport").classList.toggle("hidden", state.view !== "archive");
}

function goArchive() {
  show("archive");
  loadFolders();
  loadMessages();
  renderDetailEmpty();
}

/* ------------------------------------------------------------------ language */

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
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    el.title = t(key, el.title);
  });
  document.documentElement.lang = state.lang;
  document.body.dir = state.lang === "fa" ? "rtl" : "ltr";
  document.title = t("appName", "MailDaan");
}

function updateThemeButton() {
  const isDark = state.theme === "dark";
  $("themeIcon").setAttribute("href", isDark ? "#i-sun" : "#i-moon");
  $("themeLabel").textContent = t(isDark ? "theme.light" : "theme.dark", isDark ? "Light" : "Dark");
}

function updateLangButton() {
  $("langLabel").textContent = state.lang === "fa" ? "English" : "فارسی";
}

async function applyLanguage(lang) {
  state.lang = lang;
  await loadLocale(lang);
  state.folderDisplay = (state.locale.folders && state.locale.folders.map) || {};
  applyTranslations();
  updateThemeButton();
  updateLangButton();
  renderAccounts();
  if (state.view === "archive") { loadFolders(); loadMessages(); }
  if (state.view === "sync") renderSyncScreen();
  if (state.view === "settings") loadSettingsForms();
  updateTopbar();
}

function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  updateThemeButton();
}

async function toggleTheme() {
  const next = state.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  await api().setTheme(next);
}

async function toggleLanguage() {
  const next = state.lang === "fa" ? "en" : "fa";
  await api().setLanguage(next);
  await applyLanguage(next);
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
    node.className = btn.primary ? "btn btn-primary" : "btn";
    if (btn.danger) { node.classList.remove("btn-primary"); node.classList.add("btn-danger"); }
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

function errorModal(res) {
  const key = res && res.error_key ? res.error_key : "common.error";
  infoModal(t("common.error", "Error"), t(key, key));
}

function askPassword(title, body, onSubmit) {
  showModal(title, body +
    '<input type="password" id="modalPassword" class="modal-input" placeholder="' +
    esc(t("config.password", "Password")) + '">',
    [
      { label: t("common.cancel", "Cancel") },
      {
        label: t("nav.syncEmails", "Sync"), primary: true, onClick: () => {
          const node = $("modalPassword");
          onSubmit(node ? node.value : "");
        }
      }
    ]);
  const node = $("modalPassword");
  if (node) {
    node.focus();
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const pw = node.value;
        hideModal();
        onSubmit(pw);
      }
    });
  }
}

/* ----------------------------------------------------------------- accounts */

function renderAccounts() {
  const list = $("accountList");
  let html = "";
  for (const a of state.accounts) {
    const name = a.display_name || a.email || "—";
    const syncing = state.syncRunning && state.syncAccountId === a.id;
    html +=
      '<button class="account-item' + (a.id === state.activeAccountId ? " active" : "") +
      '" data-id="' + a.id + '" title="' + esc(a.email || a.imap_host) + '">' +
      icon("mail") +
      '<span class="acc-name">' + esc(name) + "</span>" +
      (syncing ? '<span class="att-badge">' + esc(t("accounts.running", "Syncing")) + "</span>" : "") +
      "</button>";
  }
  list.innerHTML = html;
  list.querySelectorAll(".account-item").forEach((item) => {
    item.addEventListener("click", () => switchAccount(item.getAttribute("data-id")));
  });
  updateTopbar();
}

async function switchAccount(id) {
  if (id === state.activeAccountId) return;
  if (state.syncRunning && id !== state.syncAccountId) {
    const syncing = state.accounts.find((a) => a.id === state.syncAccountId);
    showModal(
      t("sync.switchBlocked", "Sync running"),
      esc(t("sync.switchBlockedBody", "A synchronization is running for another account."))
        .replace("{account}", "<b>" + esc((syncing && (syncing.display_name || syncing.email)) || "") + "</b>"),
      [
        { label: t("sync.stayHere", "Stay"), primary: true },
        {
          label: t("sync.cancelAndSwitch", "Cancel Sync & Switch"), danger: true, onClick: async () => {
            state.pendingSwitchId = id;
            await api().cancelSync();
            infoModal(t("sync.cancel", "Cancel"), t("sync.cancelling", "Cancelling…"));
          }
        }
      ]);
    return;
  }
  await doSwitch(id);
}

async function doSwitch(id) {
  const res = await api().selectAccount(id);
  if (res && res.error_key) { errorModal(res); return; }
  state.activeAccountId = id;
  state.filters.folder_id = null;
  state.page = 1;
  state.selectedMessage = null;
  renderAccounts();
  if (state.view === "archive") goArchive();
  if (state.view === "sync") renderSyncScreen();
}

/* ------------------------------------------------------- account form (add) */

function openAccountForm(mode) {
  state.accountMode = mode;
  $("accountFormTitle").textContent =
    t(mode === "first" ? "accounts.firstTitle" : "accounts.add", "Add Account");
  $("accountForm").reset();
  $("accPort").value = 993;
  $("accError").classList.add("hidden");
  $("accPasswordField").classList.toggle("hidden", mode !== "first");
  $("accSubmit").textContent =
    t(mode === "first" ? "accounts.createAndSync" : "accounts.create", "Add Account");
  $("accCancel").classList.toggle("hidden", mode === "first");
  show("account", { chrome: mode !== "first" });
}

function bindAccountForm() {
  $("accountForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("accError");
    const fail = (msg) => { err.textContent = msg; err.classList.remove("hidden"); };
    const display = $("accDisplayName").value.trim();
    const email = $("accEmail").value.trim();
    const host = $("accHost").value.trim();
    const port = Number($("accPort").value);
    const pass = $("accPassword").value;
    if (!email || !host) return fail(t("config.fillAllFields", "Fill all fields"));
    if (!(port >= 1 && port <= 65535)) return fail(t("config.invalidPort", "Invalid port"));
    if (state.accountMode === "first" && !pass)
      return fail(t("config.fillAllFields", "Fill all fields"));
    err.classList.add("hidden");
    const res = await api().addAccount(display, email, host, port);
    if (res && res.error_key) return fail(t(res.error_key, res.error_key));
    if (!res || !res.account) return fail(t("common.error", "Error"));
    state.accounts = await api().getAccounts();
    state.activeAccountId = res.account.id;
    renderAccounts();
    if (state.accountMode === "first" && pass) {
      show("sync");
      renderSyncScreen();
      startSync(res.account.id, pass);
      return;
    }
    goArchive();
  });
  $("accCancel").addEventListener("click", () => goArchive());
  $("btnAddAccount").addEventListener("click", () => openAccountForm("add"));
}

/* ------------------------------------------------------------------ folders */

async function loadFolders() {
  const folders = await api().listFolders();
  const stats = await api().getStats();
  const allName = t("folders.allEmails", "All Emails");
  const activeId = state.filters.folder_id;
  let html =
    '<li class="folder-item' + (activeId == null ? " active" : "") + '" data-id="">' +
    icon("mail") +
    '<span class="folder-name">' + esc(allName) + "</span>" +
    '<span class="folder-count">' + toDigits(stats.messages || 0) + "</span></li>";
  for (const f of folders) {
    const name = state.folderDisplay[f.display_name] || f.display_name;
    const ic = FOLDER_ICONS[f.display_name] || "folder";
    html +=
      '<li class="folder-item' + (activeId === f.id ? " active" : "") +
      (f.has_error ? " has-error" : "") + '" data-id="' + f.id + '" title="' + esc(f.imap_name) + '">' +
      icon(ic) +
      '<span class="folder-name">' + esc(name) + "</span>" +
      '<span class="folder-count">' + toDigits(f.count || 0) + "</span></li>";
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
  if (sel) {
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

function emptyStateHtml(kind) {
  const box = (iconName, title, text, action) =>
    '<div class="state-box">' + icon(iconName) +
    '<p class="state-title">' + esc(title) + "</p>" +
    '<p class="state-text">' + esc(text) + "</p>" +
    (action || "") + "</div>";
  if (kind === "noAccount") {
    return box("user", t("empty.noAccountsTitle", "No accounts"),
      t("empty.noAccountsBody", "Add an IMAP account to start archiving."),
      '<button class="btn btn-primary" id="emptyAddAccount">' +
        esc(t("accounts.add", "Add Account")) + "</button>");
  }
  if (kind === "notSynced") {
    return box("sync", t("empty.notSyncedTitle", "Not synchronized"),
      t("empty.notSyncedBody", "Run a synchronization to download messages."),
      '<button class="btn btn-primary" id="emptySyncNow">' +
        esc(t("empty.syncCTA", "Sync Now")) + "</button>");
  }
  if (kind === "noResults") {
    return box("search", t("empty.noResultsTitle", "No results"),
      t("empty.noResultsBody", "No messages match your search or filters."), "");
  }
  return box("folder", t("empty.emptyFolderTitle", "Empty folder"),
    t("empty.emptyFolderBody", "This folder does not contain any messages."), "");
}

function highlight(text, query) {
  const escaped = esc(text);
  // Escape the query the same way so it matches entities literally and
  // never matches *inside* an entity (which would corrupt the markup).
  const q = esc((query || "").trim());
  if (!q) return escaped;
  const pattern = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  try {
    return escaped.replace(new RegExp("(" + pattern + ")", "gi"), "<mark>$1</mark>");
  } catch (e) { return escaped; }
}

function renderSearchSuggestion(suggestion) {
  const box = $("searchSuggestion");
  if (!suggestion || !suggestion.text) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  const quote = state.lang === "fa" ? "«" : "“";
  const closeQuote = state.lang === "fa" ? "»" : "”";
  box.classList.remove("hidden");
  box.innerHTML =
    '<span>' + esc(t("search.didYouMean", "Did you mean")) + "</span>" +
    '<button type="button" id="suggestionBtn">' +
      quote + esc(suggestion.text) + closeQuote + "</button>" +
    '<span>' + esc(t("search.was", "?")) + "</span>";
  $("suggestionBtn").addEventListener("click", () => {
    $("searchBox").value = suggestion.text;
    state.filters.search = suggestion.text;
    state.page = 1;
    if (state.view === "archive") loadMessages();
  });
}

async function loadMessages() {
  if (!state.accounts.length) {
    $("messageList").innerHTML = emptyStateHtml("noAccount");
    bindEmptyActions();
    $("pagination").innerHTML = "";
    renderSearchSuggestion(null);
    return;
  }
  const seq = ++state.searchSeq;
  const filters = currentFilters();
  const result = await api().queryMessages(JSON.stringify(filters), state.sort, state.page, PAGE_SIZE);
  // Ignore stale asynchronous results when the user changed the query.
  if (seq !== state.searchSeq) return;
  state.total = result.total;
  renderSearchSuggestion(result.suggestion || null);
  const list = $("messageList");
  if (!result.rows.length) {
    const stats = await api().getStats();
    const kind = stats.messages === 0 ? "notSynced"
      : (filters.search || filters.party || filters.folder_id || filters.date_from || filters.date_to)
        ? "noResults" : "emptyFolder";
    list.innerHTML = emptyStateHtml(kind);
    bindEmptyActions();
    renderPagination();
    return;
  }
  const query = filters.search || "";
  let html = "";
  for (const m of result.rows) {
    const sender = m.sender_name || m.sender_email || "—";
    const subject = m.subject || t("viewer.noSubject", "(No subject)");
    const att = m.att_count
      ? '<span class="att-badge">' + icon("paperclip") + toDigits(m.att_count) + "</span>"
      : "";
    html +=
      '<div class="message-row" data-id="' + m.id + '">' +
      '<div class="msg-sender">' + highlight(sender, query) + "</div>" +
      '<div class="msg-date">' + formatDate(m.date_utc) + "</div>" +
      '<div class="msg-subject">' + highlight(subject, query) + "</div>" +
      '<div class="msg-meta">' + att + "</div>" +
      '<div class="msg-preview">' + highlight(m.preview || m.plain_body || "", query) + "</div>" +
      "</div>";
  }
  list.innerHTML = html;
  list.querySelectorAll(".message-row").forEach((row) => {
    row.addEventListener("click", () => openMessage(Number(row.getAttribute("data-id"))));
  });
  renderPagination();
}

function bindEmptyActions() {
  const addBtn = $("emptyAddAccount");
  if (addBtn) addBtn.addEventListener("click", () => openAccountForm("add"));
  const syncBtn = $("emptySyncNow");
  if (syncBtn) syncBtn.addEventListener("click", () => { show("sync"); renderSyncScreen(); });
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
  // Outward-pointing arrows: Previous points back (left in LTR, right in
  // RTL) with the arrow on the outer side of its label; Next mirrors it.
  // SVG icons are used because the angle-quote characters get mirrored by
  // the bidirectional algorithm.
  const prevArrow = state.lang === "fa" ? "arrow-right" : "arrow-left";
  const nextArrow = state.lang === "fa" ? "arrow-left" : "arrow-right";
  const prevLabel = esc(t("list.prev", "Previous"));
  const nextLabel = esc(t("list.next", "Next"));
  $("pagination").innerHTML =
    '<button id="pgPrev" ' + (cur <= 1 ? "disabled" : "") + ">" +
      icon(prevArrow) + "<span>" + prevLabel + "</span></button>" +
    "<span>" + esc(t("list.page", "Page")) + " " + toDigits(cur) + " " +
      esc(t("list.of", "of")) + " " + toDigits(pages) + " — " +
      toDigits(state.total) + " " + esc(t("folders.messages", "messages")) + "</span>" +
    '<button id="pgNext" ' + (cur >= pages ? "disabled" : "") + ">" +
      "<span>" + nextLabel + "</span>" + icon(nextArrow) + "</button>";
  const prev = $("pgPrev"), next = $("pgNext");
  if (prev) prev.addEventListener("click", () => { state.page = cur - 1; loadMessages(); });
  if (next) next.addEventListener("click", () => { state.page = cur + 1; loadMessages(); });
}

/* ------------------------------------------------------------- message detail */

function renderDetailEmpty() {
  $("detailEmpty").classList.remove("hidden");
  $("detailContent").classList.add("hidden");
  $("detailContent").innerHTML = "";
}

async function openMessage(id) {
  const m = await api().getMessage(id);
  if (!m) { infoModal(t("common.error", "Error"), t("viewer.notFound", "Message not found.")); return; }
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
    bodyHtml = '<div class="detail-body" dir="auto">' + esc(m.plain_body) + "</div>";
  } else if (m.html_body && m.html_body.trim()) {
    bodyHtml =
      '<p class="html-notice">' + esc(t("viewer.htmlNotice", "HTML only")) + "</p>" +
      '<iframe class="html-frame" sandbox="" srcdoc="' + esc(sanitizeHtml(m.html_body)) + '"></iframe>';
  } else {
    bodyHtml = '<div class="detail-body" dir="auto"></div>';
  }

  let attHtml = "";
  if (m.attachments && m.attachments.length) {
    attHtml = '<div class="detail-label">' + esc(t("viewer.attachments", "Attachments")) + "</div>" +
      '<div class="attachment-list">' +
      m.attachments.map((a) =>
        '<div class="attachment-chip">' + icon("paperclip") +
        '<span>' + esc(a.filename || a.filename_safe) + " (" + toDigits(a.size || 0) + ' B)</span>' +
        '<button data-att="' + a.id + '">' + esc(t("viewer.saveAttachment", "Save")) + "</button></div>"
      ).join("") +
      '<div class="attachment-chip"><button id="btnSaveAllAtt">' +
      esc(t("viewer.saveAllAttachments", "Save All")) + "</button></div></div>";
  }

  $("detailEmpty").classList.add("hidden");
  const content = $("detailContent");
  content.classList.remove("hidden");
  content.innerHTML =
    '<div class="detail-card">' +
    '<div class="detail-actions">' +
    '<button class="btn btn-primary small" id="btnSaveEml">' + icon("file") +
      esc(t("viewer.saveEml", "Save original message (.eml)")) + "</button>" +
    "</div>" +
    '<h2 class="detail-subject" dir="auto">' + esc(subject) + "</h2>" +
    '<dl class="detail-meta">' + meta + "</dl>" + bodyHtml + attHtml +
    "</div>";
  content.querySelectorAll("[data-att]").forEach((btn) => {
    btn.addEventListener("click", () => saveAttachment(id, Number(btn.getAttribute("data-att"))));
  });
  const saveAll = $("btnSaveAllAtt");
  if (saveAll) saveAll.addEventListener("click", () =>
    api().saveAllAttachments(id).then((r) => fileResult(r)));
  $("btnSaveEml").addEventListener("click", () => api().saveEml(id).then((r) => fileResult(r)));
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
  fileResult(await api().saveAttachment(messageId, attachmentId));
}

function fileResult(r) {
  if (!r) return;
  if (r.error) infoModal(t("common.saveFailed", "Save failed"), r.error);
  else if (r.error_key) errorModal(r);
  else infoModal(t("common.ok", "OK"), t("common.savedToFile", "Saved to:") + "\n" + r.path);
}

/* --------------------------------------------------------------------- sync */

function renderSyncScreen() {
  const account = activeAccount();
  const box = $("syncAccountBox");
  box.innerHTML = account
    ? icon("user") + "<span>" + esc(account.display_name || account.email) + "</span>"
    : esc(t("accounts.none", "No account"));
  renderSyncControls();
  renderSyncStatus();
  if (state.syncReport) { renderReport(state.syncReport); return; }
  if (state.syncRunning) { renderProgress(null); return; }
  renderSyncIdle();
}

function renderSyncControls() {
  const box = $("syncControls");
  if (state.syncRunning) {
    box.innerHTML =
      '<button class="btn btn-danger" id="btnCancelSync">' + icon("x") +
      esc(t("sync.cancel", "Cancel")) + "</button>" +
      '<button class="btn" id="btnSyncBack">' + icon("archive") +
      esc(t("sync.backToArchive", "Back to Archive")) + "</button>";
    $("btnCancelSync").addEventListener("click", () => {
      showModal(t("sync.cancel", "Cancel"), esc(t("sync.cancelConfirm", "Cancel?")), [
        { label: t("common.no", "No"), primary: true },
        { label: t("common.yes", "Yes"), danger: true, onClick: () => api().cancelSync() },
      ]);
    });
  } else {
    box.innerHTML =
      '<button class="btn btn-primary" id="btnStartSync">' + icon("sync") +
      esc(t("sync.start", "Start Synchronization")) + "</button>" +
      '<button class="btn" id="btnSyncBack">' + icon("archive") +
      esc(t("sync.backToArchive", "Back to Archive")) + "</button>";
    $("btnStartSync").addEventListener("click", onStartSync);
  }
  $("btnSyncBack").addEventListener("click", () => goArchive());
}

async function onStartSync() {
  const account = activeAccount();
  if (!account) { openAccountForm("add"); return; }
  askPassword(
    t("sync.title", "Synchronization"),
    esc(t("config.password", "Password")) + " — <b>" + esc(account.email) + "</b>",
    (pw) => startSync(account.id, pw));
}

function renderSyncStatus(s) {
  s = s || {};
  const box = $("syncStatusBox");
  const conn = s.connected
    ? '<b style="color: var(--success)">' + esc(t("sync.connected", "Connected")) + "</b>"
    : '<b>' + esc(t("sync.disconnected", "Not connected")) + "</b>";
  box.innerHTML =
    "<div>" + esc(t("sync.connection", "Connection")) + ": " + conn + "</div>" +
    "<div>" + esc(t("sync.errors", "Errors")) + ": <b>" + toDigits(s.error_count || 0) + "</b></div>" +
    (s.last_error ? '<div class="sync-error" style="margin:8px 0 0">' + esc(s.last_error) + "</div>" : "");
}

function renderSyncIdle() {
  const card = $("syncCard");
  card.innerHTML =
    '<h2 class="card-title">' + esc(t("sync.ready", "Ready")) + "</h2>" +
    '<p class="form-subtitle">' +
      esc(t("sync.idleBody", "Synchronization downloads all messages from every IMAP folder into the local archive. It resumes from the last checkpoint when interrupted.")) +
    "</p>" +
    '<div class="progress-block">' +
      '<div class="progress-head"><span>' + esc(t("sync.title", "Synchronization")) + '</span><span class="pct">0%</span></div>' +
      '<div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>' +
    '</div>' +
    '<div class="progress-block">' +
      '<div class="progress-head"><span>' + esc(t("sync.currentFolder", "Current folder")) + '</span><span class="pct">0%</span></div>' +
      '<div class="progress-track"><div class="progress-fill secondary" style="width:0%"></div></div>' +
    '</div>';
}

async function startSync(accountId, password) {
  state.syncReport = null;
  state.pendingReport = null;
  if (!password) { renderSyncScreen(); return; }
  state.syncRunning = true;
  state.syncAccountId = accountId;
  renderAccounts();
  renderSyncScreen();
  const res = await api().startSync(accountId, password);
  if (res && res.error_key) {
    state.syncRunning = false;
    state.syncAccountId = null;
    errorModal(res);
    renderSyncScreen();
    return;
  }
  pollProgress();
}

let pollTimer = null;
async function pollProgress() {
  if (pollTimer) clearTimeout(pollTimer);
  const s = await api().getSyncProgress();
  if (s.sync_account_id && s.sync_account_id !== state.syncAccountId) {
    state.syncAccountId = s.sync_account_id;
    renderAccounts();
  }
  if (state.view === "sync") {
    if (state.syncReport) renderReport(state.syncReport);
    else if (s.running) renderProgress(s);
    renderSyncStatus(s);
  }
  if (s.running) {
    pollTimer = setTimeout(pollProgress, 500);
  } else {
    state.syncRunning = false;
    const report = state.pendingReport || (s.status && s.status !== "idle" ? s : null);
    state.pendingReport = null;
    if (report) {
      state.syncReport = report;
      state.accounts = await api().getAccounts();
      renderAccounts();
      if (state.pendingSwitchId) {
        const nextId = state.pendingSwitchId;
        state.pendingSwitchId = null;
        hideModal();
        await doSwitch(nextId);
        return;
      }
      if (state.view === "sync") renderSyncScreen();
    } else if (state.view === "sync") {
      renderSyncScreen();
    }
  }
}

function renderProgress(s) {
  const card = $("syncCard");
  if (!s) {
    card.innerHTML =
      '<h2 class="card-title">' + esc(t("sync.title", "Synchronization")) + "</h2>" +
      '<p class="form-subtitle">' + esc(t("sync.preparing", "Preparing…")) + "</p>" +
      '<div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>';
    return;
  }
  const folderName = state.folderDisplay[s.current_folder] || s.current_folder || "—";
  card.innerHTML =
    '<h2 class="card-title">' + esc(t("sync.statusRunning", "Running")) + "</h2>" +
    '<p class="form-subtitle">' + esc(s.running
      ? t("sync.preparing", "Preparing…")
      : t("sync.statusRunning", "Running")) + "</p>" +
    makeProgress(t("sync.overall", "Overall progress"), s.total_processed, s.total_pending,
                 s.overall_percent) +
    makeProgress(folderName, s.folder_processed, s.folder_pending, s.folder_percent, true) +
    '<div class="progress-grid">' +
    '<div>' + esc(t("sync.foldersProgress", "Folders")) + ': <b>' + toDigits(s.folders_done) + "/" +
      toDigits(s.folders_total) + "</b></div>" +
    '<div>' + esc(t("sync.processed", "Processed")) + ': <b>' + toDigits(s.total_processed) +
      " / " + toDigits(s.total_pending) + "</b></div>" +
    '<div>' + esc(t("sync.speed", "Speed")) + ': <b>' + toDigits(s.speed) + " " +
      esc(t("common.messagesPerSecond", "msg/s")) + "</b></div>" +
    '<div>' + esc(t("sync.eta", "ETA")) + ': <b>' + formatDuration(s.eta_seconds) + "</b></div>" +
    '<div>' + esc(t("sync.elapsed", "Elapsed")) + ': <b>' + formatDuration(s.elapsed_seconds) + "</b></div>" +
    '<div>' + esc(t("sync.errors", "Errors")) + ': <b>' + toDigits((s.error_folders || []).length) + "</b></div>" +
    "</div>" +
    (s.last_error ? '<div class="sync-error">' + esc(s.last_error) + "</div>" : "");
}

function makeProgress(label, done, total, percent, secondary) {
  return '<div class="progress-block">' +
    '<div class="progress-head"><span class="plabel">' + esc(label) + "</span>" +
    '<span class="pct">' + toDigits(percent || 0) + "%</span></div>" +
    '<div class="progress-track"><div class="progress-fill' +
    (secondary ? " secondary" : "") + '" style="width:' + (percent || 0) + '%"></div></div>' +
    '<div class="progress-count">' + toDigits(done) + " / " + toDigits(total) + "</div></div>";
}

function renderReport(r) {
  const statusKey = {
    completed: "statusCompleted", incomplete: "statusIncomplete",
    cancelled: "statusCancelled", error: "statusError", running: "statusRunning",
  }[r.status] || "statusCompleted";
  const account = state.accounts.find((a) => a.id === (r.account_id || state.syncAccountId));
  const accountName = account ? (account.display_name || account.email) : (r.account_email || "");
  const card = $("syncCard");
  card.innerHTML =
    '<h2 class="card-title">' + esc(t("report.title", "Report")) + "</h2>" +
    '<p style="margin:0 0 12px"><span class="status-pill ' + (r.status || "completed") + '">' +
      esc(t("sync." + statusKey, r.status)) + "</span></p>" +
    (accountName
      ? '<div class="sync-account" style="margin-bottom:14px">' + icon("user") +
        "<span>" + esc(accountName) + "</span></div>" : "") +
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
    '<button class="btn btn-primary" id="btnOpenViewer">' + icon("archive") +
      esc(t("report.openViewer", "Open Archived Emails")) + "</button>" +
    '<button class="btn" id="btnSyncAgain">' + icon("sync") +
      esc(t("report.syncAgain", "Sync Again")) + "</button>" +
    "</div>";
  $("btnOpenViewer").addEventListener("click", () => {
    state.syncReport = null;
    state.syncAccountId = null;
    goArchive();
  });
  $("btnSyncAgain").addEventListener("click", onStartSync);
}

function reportRow(label, value) {
  return '<div class="report-row"><span>' + esc(label) + '</span><b>' +
    (typeof value === "number" ? toDigits(value) : esc(value)) + "</b></div>";
}

/* ------------------------------------------------------------------- export */

async function onExportExcel() {
  if (state.busy) return;
  const account = activeAccount();
  if (!account) { errorModal({ error_key: "accounts.none" }); return; }
  state.busy = true;
  const btn = $("btnExport");
  btn.disabled = true;
  const filters = state.view === "archive" ? currentFilters() : {};
  const res = await api().exportExcel(JSON.stringify(filters), state.sort);
  btn.disabled = false;
  state.busy = false;
  if (!res) return;
  if (res.error) infoModal(t("export.failed", "Export failed"), res.error);
  else if (res.error_key) errorModal(res);
  else infoModal(t("export.done", "Done"),
    toDigits(res.messages) + " " + esc(t("export.messages", "messages")) + " • " +
    toDigits(res.contacts) + " " + esc(t("export.contacts", "contacts")) + " • " +
    toDigits(res.organizations) + " " + esc(t("export.organizations", "organizations")) +
    "<br>" + esc(t("common.savedToFile", "Saved to:")) + "<br>" + esc(res.path));
}

/* ----------------------------------------------------------------- settings */

async function loadSettingsForms() {
  const s = state;
  $("settingsLang").value = s.lang;
  $("settingsTheme").value = s.theme;
  const sel = $("settingsAccount");
  sel.innerHTML = "";
  for (const a of s.accounts) {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.display_name ? a.display_name + " (" + a.email + ")" : a.email;
    sel.appendChild(opt);
  }
  if (s.accounts.length) {
    sel.value = s.activeAccountId || s.accounts[0].id;
    fillAccountSettingsForm(sel.value);
  }
  renderAppPasswordCard();
}

function fillAccountSettingsForm(accountId) {
  const a = state.accounts.find((x) => x.id === accountId);
  if (!a) return;
  $("setDisplayName").value = a.display_name || "";
  $("setUser").value = a.email || "";
  $("setHost").value = a.imap_host || "";
  $("setPort").value = a.imap_port || 993;
  $("setError").classList.add("hidden");
}

function bindSettingsForms() {
  $("settingsAccount").addEventListener("change", (e) => fillAccountSettingsForm(e.target.value));

  $("btnSaveAppSettings").addEventListener("click", async () => {
    const lang = $("settingsLang").value;
    const theme = $("settingsTheme").value;
    if (theme !== state.theme) {
      applyTheme(theme);
      await api().setTheme(theme);
    }
    if (lang !== state.lang) {
      await api().setLanguage(lang);
      await applyLanguage(lang);
    }
    infoModal(t("settings.saved", "Saved"), t("settings.saved", "Saved"));
  });

  $("accountSettingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("setError");
    const port = Number($("setPort").value);
    if (!$("setHost").value.trim() || !$("setUser").value.trim())
      return err.textContent = t("config.fillAllFields", "Fill all fields"), err.classList.remove("hidden");
    if (!(port >= 1 && port <= 65535))
      return err.textContent = t("config.invalidPort", "Invalid port"), err.classList.remove("hidden");
    err.classList.add("hidden");
    const id = $("settingsAccount").value;
    const res = await api().updateAccount(id, $("setDisplayName").value.trim(),
      $("setUser").value.trim(), $("setHost").value.trim(), port);
    if (res && res.error_key) { errorModal(res); return; }
    state.accounts = await api().getAccounts();
    renderAccounts();
    infoModal(t("settings.saved", "Saved"), t("settings.saved", "Saved"));
  });

  $("btnCancelAccountSettings").addEventListener("click", () => goArchive());

  $("btnRemoveAccount").addEventListener("click", () => {
    const sel = $("settingsAccount");
    const account = state.accounts.find((a) => a.id === sel.value);
    if (!account) return;
    showModal(
      t("accounts.remove", "Remove Account"),
      esc(t("accounts.removeConfirm", "Remove this account? The local archive is kept on disk."))
        .replace("{account}", "<b>" + esc(account.display_name || account.email) + "</b>"),
      [
        { label: t("common.cancel", "Cancel"), primary: true },
        {
          label: t("accounts.remove", "Remove Account"), danger: true, onClick: async () => {
            const res = await api().removeAccount(account.id);
            if (res && res.error_key) { errorModal(res); return; }
            state.accounts = await api().getAccounts();
            state.activeAccountId = state.accounts.length ? state.accounts[0].id : null;
            renderAccounts();
            if (!state.accounts.length) { openAccountForm("first"); return; }
            loadSettingsForms();
            goArchive();
          }
        }
      ]);
  });
}

/* --------------------------------------------------- application lock screen */

function bindEyeToggles() {
  document.querySelectorAll(".eye-toggle").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.getAttribute("data-target"));
      if (!input) return;
      const show = input.getAttribute("type") === "password";
      input.setAttribute("type", show ? "text" : "password");
      btn.querySelector("use").setAttribute("href", show ? "#i-eye-off" : "#i-eye");
      btn.title = t(show ? "lock.hidePassword" : "lock.showPassword", btn.title);
    });
  });
}

async function submitUnlock(e) {
  e.preventDefault();
  const err = $("lockError");
  const input = $("lockPassword");
  const btn = $("lockUnlock");
  btn.disabled = true;
  const res = await api().unlock(input.value);
  btn.disabled = false;
  if (res && res.ok) {
    err.classList.add("hidden");
    input.value = "";
    finishBoot();
    return;
  }
  err.textContent = t("lock.invalidPassword", "The application password is incorrect.");
  err.classList.remove("hidden");
  input.value = "";
  input.focus();
}

function showLock() {
  applyTranslations();
  bindEyeToggles();
  $("lockError").classList.add("hidden");
  $("lockPassword").value = "";
  show("lock", { chrome: false });
  setTimeout(() => $("lockPassword").focus(), 50);
}

/* --------------------------------------------- application password settings */

const COMMON_PASSWORDS = [
  "password", "password1", "passw0rd", "12345678", "123456789", "qwerty",
  "qwerty123", "letmein", "welcome", "welcome1", "admin", "admin123",
  "iloveyou", "monkey", "dragon", "11111111", "12341234", "abc12345",
  "maildaan", "maildaan123", "maildaan1",
];

function passwordStrength(pw) {
  /* Lightweight locally-computed estimator (guidance, not validation).
     Rewards length and variety without demanding arbitrary character
     categories, and penalizes common passwords and predictable patterns. */
  if (!pw) return { level: "", score: 0 };
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  const variety = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^a-zA-Z0-9]/]
    .filter((re) => re.test(pw)).length;
  if (variety >= 2) score++;
  if (variety >= 3 && pw.length >= 10) score++;
  const low = pw.toLowerCase();
  if (COMMON_PASSWORDS.indexOf(low) !== -1) score = 0;
  if (/(.)\1\1/.test(pw)) score--;                                    // repeats
  if (/(?:0123|1234|2345|3456|4567|5678|6789|abcd|bcde|qwerty|asdf|zxcv)/i.test(low))
    score--;                                                          // sequences
  const account = activeAccount();
  const key = account && (account.email || "").split("@")[0].toLowerCase();
  if (key && key.length >= 4 && low.indexOf(key) !== -1) score = Math.min(score, 1);
  if (low.indexOf("maildaan") !== -1) score = Math.min(score, 1);
  const level = score <= 1 ? "weak" : score === 2 ? "medium" : "strong";
  return { level: level, score: Math.max(0, score) };
}

function updatePasswordMeter() {
  const pw = $("appPwNew").value || "";
  const meter = $("pwMeter");
  const label = $("pwMeterLabel");
  const res = passwordStrength(pw);
  meter.classList.remove("weak", "medium", "strong");
  label.classList.remove("weak", "medium", "strong");
  if (!res.level) {
    label.textContent = t("lock.strength", "Password strength");
    return;
  }
  meter.classList.add(res.level);
  label.classList.add(res.level);
  label.textContent = t("lock.strength", "Password strength") + ": " +
    t("lock." + res.level, res.level);
}

function renderAppPasswordCard() {
  $("lockEnabledView").classList.toggle("hidden", !state.lockEnabled);
  $("lockDisabledView").classList.toggle("hidden", state.lockEnabled);
  hideAppPasswordForms();
}

function hideAppPasswordForms() {
  $("appPasswordForm").classList.add("hidden");
  $("appPwDisableForm").classList.add("hidden");
  $("appPwError").classList.add("hidden");
  $("appPwDisableError").classList.add("hidden");
}

function openAppPasswordForm(mode) {
  state.appPwMode = mode;
  $("appPwDisableForm").classList.add("hidden");
  $("appPwCurrentField").classList.toggle("hidden", mode !== "change");
  $("appPasswordForm").classList.remove("hidden");
  $("appPasswordForm").reset();
  $("pwMeter").classList.remove("weak", "medium", "strong");
  $("pwMeterLabel").textContent = t("lock.strength", "Password strength");
  $("pwMeterLabel").classList.remove("weak", "medium", "strong");
  $("appPwError").classList.add("hidden");
  $("appPwSubmit").textContent =
    t(mode === "change" ? "lock.save" : "lock.enable", "Save");
  bindEyeToggles();
  $("appPw" + (mode === "change" ? "Current" : "New")).focus();
}

function bindAppPasswordForms() {
  $("btnEnableAppPassword").addEventListener("click", () => openAppPasswordForm("enable"));
  $("btnChangeAppPassword").addEventListener("click", () => openAppPasswordForm("change"));
  $("appPwCancel").addEventListener("click", hideAppPasswordForms);
  $("appPwNew").addEventListener("input", updatePasswordMeter);

  $("btnDisableAppPassword").addEventListener("click", () => {
    $("appPasswordForm").classList.add("hidden");
    $("appPwDisableForm").classList.remove("hidden");
    $("appPwDisableForm").reset();
    $("appPwDisableError").classList.add("hidden");
    bindEyeToggles();
    $("appPwDisableCurrent").focus();
  });
  $("appPwDisableCancel").addEventListener("click", hideAppPasswordForms);

  $("appPasswordForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("appPwError");
    const fail = (key) => { err.textContent = t(key, key); err.classList.remove("hidden"); };
    const cur = $("appPwCurrent").value;
    const pw = $("appPwNew").value;
    const confirm = $("appPwConfirm").value;
    const res = state.appPwMode === "change"
      ? await api().changeAppPassword(cur, pw, confirm)
      : await api().enableAppPassword(pw, confirm);
    if (res && res.ok) {
      state.lockEnabled = true;
      renderAppPasswordCard();
      infoModal(t("settings.saved", "Saved"), t("settings.saved", "Saved"));
      return;
    }
    if (res && res.error_key) fail(res.error_key);
    else fail("common.error");
  });

  $("appPwDisableForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("appPwDisableError");
    const res = await api().disableAppPassword($("appPwDisableCurrent").value);
    if (res && res.ok) {
      state.lockEnabled = false;
      renderAppPasswordCard();
      infoModal(t("settings.saved", "Saved"), t("settings.saved", "Saved"));
      return;
    }
    err.textContent = res && res.error_key ? t(res.error_key, res.error_key)
      : t("common.error", "Error");
    err.classList.remove("hidden");
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

/* -------------------------------------------------------------------- exit */

function requestExit() {
  if (state.syncRunning) {
    showModal(t("common.confirmExit", "Exit MailDaan?"),
      esc(t("sync.syncActiveExit", "Sync is still running.")), [
        { label: t("common.no", "No"), primary: true },
        { label: t("common.yes", "Yes"), danger: true, onClick: () => api().exitApp(true) },
      ]);
  } else {
    api().exitApp();
  }
}

/* ------------------------------------------------------------------- startup */

function bindOnce() {
  $("navArchive").addEventListener("click", goArchive);
  $("navSync").addEventListener("click", () => { show("sync"); renderSyncScreen(); });
  $("navSettings").addEventListener("click", () => { show("settings"); loadSettingsForms(); });
  $("btnExport").addEventListener("click", onExportExcel);
  $("btnTheme").addEventListener("click", toggleTheme);
  $("btnLang").addEventListener("click", toggleLanguage);
  $("btnExit").addEventListener("click", requestExit);
  $("btnLangFa").addEventListener("click", () => chooseLanguage("fa"));
  $("btnLangEn").addEventListener("click", () => chooseLanguage("en"));
  $("devLink").addEventListener("click", (e) => {
    e.preventDefault();
    api().openExternal("https://github.com/TahaSarhangi");
  });
  $("lockForm").addEventListener("submit", submitUnlock);
  $("lockExit").addEventListener("click", () => api().exitApp());
  bindEyeToggles();
  bindAccountForm();
  bindSettingsForms();
  bindAppPasswordForms();
  bindSearchAndFilters();
}

async function chooseLanguage(lang) {
  const bridge = api();
  if (!bridge) return;
  const res = await bridge.setLanguage(lang);
  if (res && res.error) return;
  await applyLanguage(lang);
  if (state.accounts.length) { goArchive(); return; }
  openAccountForm("first");
}

async function bootstrap() {
  bindOnce();
  const initial = await api().getInitialState();
  state.lang = initial.language || "en";
  state.theme = initial.theme || "light";
  state.accounts = initial.accounts || [];
  state.activeAccountId = initial.active_account_id;
  state.lockEnabled = !!initial.lock_enabled;
  state.languageChosen = !!initial.language;
  applyTheme(state.theme);
  await loadLocale(state.lang);
  state.folderDisplay = (state.locale.folders && state.locale.folders.map) || {};
  applyTranslations();
  updateLangButton();
  updateThemeButton();
  renderAccounts();

  if (initial.locked) {
    showLock();
    return;
  }
  finishBoot();
}

function finishBoot() {
  /* Route into the application. Called from bootstrap after authentication
     (or directly when no application password is enabled). */
  if (!state.languageChosen) {
    show("language");
    return;
  }
  if (!state.accounts.length) {
    openAccountForm("first");
    return;
  }
  goArchive();
}

document.addEventListener("DOMContentLoaded", () => {
  // 'pywebviewready' and the fallback timer can both fire, so make sure the
  // interface is only bootstrapped (and its handlers bound) once.
  let started = false;
  const start = () => {
    if (started || !api()) return;
    started = true;
    bootstrap();
  };
  if (window.pywebview && window.pywebview.readyState === 1) start();
  else {
    window.addEventListener("pywebviewready", start);
    setTimeout(start, 400);
  }
});

/* ---------------------------------------------------------- exit interception */

window.maildaan = {
  handleExitRequest: function () { requestExit(); },
  pushReport: function (reportJson) {
    try {
      const report = JSON.parse(reportJson);
      state.pendingReport = report;
      state.syncReport = report;
      state.syncRunning = false;
      api().getAccounts().then((accounts) => {
        state.accounts = accounts;
        renderAccounts();
        if (state.view === "sync") renderSyncScreen();
      });
    } catch (e) { /* ignore */ }
  },
};
