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
  attachMode: "with",
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
  openMessageId: null,
  // Multi-selection in the message list. `ids` are the selected message ids
  // of the current filter scope; `mode` shows the checkboxes. Selection is
  // reset whenever the account, folder, search or filters change so it can
  // never silently follow the user into another scope.
  selection: { mode: false, ids: new Set(), lastAnchor: null, scope: "" },
  update: {
    status: "idle",        // idle | checking | uptodate | available | downloading | ready | error
    version: "",
    notes: "",
    percent: 0,
    error: "",
    checked: false,        // at least one check has completed
    popoverOpen: false,
    installPrompted: false, // one "install now / later" prompt per completed download
  },
  pendingUpdateVersion: null, // version recorded when the installer was started
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

function parseUtcDate(value) {
  // storage.py keeps email dates as 'YYYY-MM-DD HH:MM:SS' in UTC; make the
  // offset explicit so the string is never read as local time.
  const s = String(value).trim().replace(" ", "T");
  const date = new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z");
  return isNaN(date.getTime()) ? null : date;
}

let _dateFormatter = null;
let _dateFormatterLang = null;

function formatDate(value) {
  if (!value) return "—";
  const date = parseUtcDate(value);
  if (!date) return toDigits(value);   // unknown format: show the raw value
  try {
    if (_dateFormatterLang !== state.lang) {
      _dateFormatterLang = state.lang;
      _dateFormatter = state.lang === "fa"
        ? new Intl.DateTimeFormat("fa-IR", {
            calendar: "persian", numberingSystem: "arabext",
            year: "numeric", month: "long", day: "numeric",
            hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
          })
        : new Intl.DateTimeFormat("en-CA", {
            calendar: "gregory", numberingSystem: "latn",
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", second: "2-digit",
            hour12: false, timeZone: "UTC",
          });
    }
    return _dateFormatter.format(date);
  } catch (e) {
    return toDigits(value);
  }
}

/* Centralized locale-aware duration rendering. Durations are always shown
   as MM:SS (HH:MM:SS once an hour has elapsed); Latin or Persian digits
   follow the active interface language. The numeric value is never altered
   — this is display formatting only. */
function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  const text = h > 0
    ? pad(h) + ":" + pad(m) + ":" + pad(s)
    : pad(m) + ":" + pad(s);
  return toDigits(text);
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
  renderFooterVersion();
}

/* The footer shows the running application version next to the copyright:
   Latin digits in English, Persian digits in Persian. It stays empty until
   the backend reports the version during bootstrap. */
function renderFooterVersion() {
  const el = $("footerVersion");
  if (!el) return;
  const version = appVersionDisplay();
  el.textContent = version
    ? t("version", "Version") + " " + toDigits(version)
    : "";
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
  if (state.view === "archive") {
    loadFolders();
    loadMessages();
    // Re-render an already-open message so its date follows the new calendar.
    if (state.openMessageId != null) openMessage(state.openMessageId);
  }
  if (state.view === "sync") renderSyncScreen();
  if (state.view === "settings") loadSettingsForms();
  if (reportState && reportState.open) renderReportDialog();
  if (state.update.popoverOpen) renderUpdatePopover();
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

/* One reusable user-facing failure report. A failed bridge call carries
   either an error_key (translated here) or a raw `error` string shown as a
   secondary detail. Tracebacks, tokens and credentials never reach this
   layer. `opts.toast` uses the non-blocking toast instead of the modal. */
function errorResultHtml(res) {
  if (!res) return null;
  const key = res.error_key;
  const detail = res.error_detail || (!key ? (res.error || "") : "");
  if (!key && !res.error) return null;
  const html = "<div>" + esc(t(key || "common.error", key || "common.error")) + "</div>" +
    (detail ? '<div class="err-detail">' + esc(detail) + "</div>" : "");
  return html;
}

function reportError(res, opts) {
  const html = errorResultHtml(res);
  if (html === null) return false;
  if (opts && opts.toast) toast(html, "error");
  else showModal(t("common.error", "Error"), html,
    [{ label: t("common.close", "Close"), primary: true }]);
  return true;
}

/* Small, non-intrusive notification for harmless events; errors stay
   longer and are always dismissible. */
function toast(html, kind) {
  const root = $("toastRoot");
  if (!root || html === null || html === undefined) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "info");
  el.innerHTML = '<span class="toast-text">' + html + "</span>" +
    '<button class="toast-close" aria-label="' +
      esc(t("common.close", "Close")) + '">' + icon("x") + "</button>";
  root.appendChild(el);
  const close = () => {
    el.classList.add("hide");
    setTimeout(() => { if (el.parentNode) el.remove(); }, 200);
  };
  el.querySelector(".toast-close").addEventListener("click", close);
  setTimeout(close, kind === "error" ? 9000 : 4500);
  return close;
}

function askPassword(title, body, onSubmit) {
  showModal(title, body +
    '<input type="password" id="modalPassword" class="modal-input" placeholder="' +
    esc(t("config.password", "Password")) + '">',
    [
      { label: t("common.cancel", "Cancel") },
      {
        label: t("sync.start", "Start Synchronization"), primary: true, onClick: () => {
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
  resetSelection();
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
    if (res && res.error_key)
      return fail(t(res.error_key, res.error_key) +
        (res.error_detail ? " — " + res.error_detail : ""));
    if (res && res.error) return fail(res.error);
    if (!res || !res.account) return fail(t("common.error", "Error"));
    state.accounts = await api().getAccounts();
    state.activeAccountId = res.account.id;
    renderAccounts();
    if (state.accountMode === "first" && pass) {
      show("sync");
      renderSyncScreen();
      askAttachmentMode(res.account, (withAttachments) =>
        startSync(res.account.id, pass, withAttachments));
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
  if (folders && folders.error_key) toast(errorResultHtml(folders), "error");
  if (stats && stats.error_key) toast(errorResultHtml(stats), "error");
  const folderList = Array.isArray(folders) ? folders : [];
  const stat = stats && !stats.error_key ? stats : { messages: 0 };
  const allName = t("folders.allEmails", "All Emails");
  const activeId = state.filters.folder_id;
  let html =
    '<li class="folder-item' + (activeId == null ? " active" : "") + '" data-id="">' +
    icon("mail") +
    '<span class="folder-name">' + esc(allName) + "</span>" +
    '<span class="folder-count">' + toDigits(stat.messages || 0) + "</span></li>";
  for (const f of folderList) {
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
      resetSelection();
      loadFolders();
      loadMessages();
    });
  });
  const sel = $("filterFolder");
  if (sel) {
    sel.innerHTML = '<option value="">' + esc(t("filters.anyFolder", "All folders")) + "</option>";
    for (const f of folderList) {
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
  if (!result || result.error_key) {
    if (result && result.error_key) toast(errorResultHtml(result), "error");
    state.total = 0;
    $("messageList").innerHTML = emptyStateHtml("noResults");
    bindEmptyActions();
    renderSearchSuggestion(null);
    renderPagination();
    return;
  }
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
    renderSelectionBar();
    renderPagination();
    return;
  }
  const query = filters.search || "";
  const selecting = state.selection.mode || state.selection.ids.size > 0;
  let html = "";
  for (const m of result.rows) {
    const sender = m.sender_name || m.sender_email || "—";
    const subject = m.subject || t("viewer.noSubject", "(No subject)");
    const att = m.att_count
      ? '<span class="att-badge">' + icon("paperclip") + toDigits(m.att_count) + "</span>"
      : "";
    const checked = state.selection.ids.has(m.id);
    html +=
      '<div class="message-row' + (selecting ? " selecting" : "") +
      (checked ? " selected" : "") +
      '" data-id="' + m.id + '">' +
      (selecting
        ? '<button class="msg-check' + (checked ? " on" : "") + '" data-check="' + m.id +
          '" aria-label="' + esc(t("list.select", "Select")) + '">' +
          (checked ? icon("check") : "") + "</button>"
        : "") +
      '<div class="msg-sender">' + highlight(sender, query) + "</div>" +
      '<div class="msg-date">' + formatDate(m.date_utc) + "</div>" +
      '<div class="msg-subject">' + highlight(subject, query) + "</div>" +
      '<div class="msg-meta">' + att + "</div>" +
      '<div class="msg-preview">' + highlight(m.preview || m.plain_body || "", query) + "</div>" +
      "</div>";
  }
  list.innerHTML = html;
  list.querySelectorAll(".message-row").forEach((row) => {
    row.addEventListener("click", (e) => onMessageRowClick(e, row));
  });
  renderSelectionBar();
  renderPagination();
}

/* Row click: ordinary click opens the message as before. While selection
   mode is on — or when Ctrl/Shift are used — the click selects instead.
   Shift-click extends the selection from the last anchor row. */
function onMessageRowClick(e, row) {
  const id = Number(row.getAttribute("data-id"));
  if (e.shiftKey && (state.selection.mode || state.selection.ids.size)) {
    e.preventDefault();
    const list = $("messageList");
    const rows = Array.from(list.querySelectorAll(".message-row"));
    const anchorRow = rows.find((r) => Number(r.getAttribute("data-id")) === state.selection.lastAnchor)
      || row;
    const from = rows.indexOf(anchorRow);
    const to = rows.indexOf(row);
    if (from < 0 || to < 0) return;
    for (let i = Math.min(from, to); i <= Math.max(from, to); i++) {
      state.selection.ids.add(Number(rows[i].getAttribute("data-id")));
    }
    state.selection.mode = true;
    loadMessages();
    return;
  }
  if (e.ctrlKey || e.metaKey || state.selection.mode) {
    e.preventDefault();
    state.selection.mode = true;
    if (state.selection.ids.has(id)) state.selection.ids.delete(id);
    else state.selection.ids.add(id);
    state.selection.lastAnchor = id;
    loadMessages();
    return;
  }
  openMessage(id);
}

function selectionScopeKey() {
  const f = state.filters;
  return [state.activeAccountId, f.folder_id, f.search, f.party, f.date_from, f.date_to].join("|");
}

function resetSelection() {
  state.selection.mode = false;
  state.selection.ids = new Set();
  state.selection.lastAnchor = null;
  state.selection.scope = selectionScopeKey();
  renderSelectionBar();
}

function renderSelectionBar() {
  const bar = $("selectionBar");
  if (!bar) return;
  const n = state.selection.ids.size;
  if (!state.selection.mode && n === 0) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  bar.classList.remove("hidden");
  bar.innerHTML =
    '<span class="sel-count">' + icon("check-square") +
      toDigits(n) + " " + esc(t("list.selected", "selected")) + "</span>" +
    '<button class="btn small" id="btnSelectAll">' + esc(t("list.selectAll", "Select all")) + "</button>" +
    '<button class="btn small" id="btnClearSelection">' +
      esc(t("list.clearSelection", "Clear selection")) + "</button>" +
    '<button class="btn small btn-danger" id="btnDeleteSelected"' +
      (n ? "" : " disabled") + ">" + icon("trash") +
      esc(t("list.deleteSelected", "Delete selected")) +
      (n ? " (" + toDigits(n) + ")" : "") + "</button>";
  $("btnSelectAll").addEventListener("click", selectAllMessages);
  $("btnClearSelection").addEventListener("click", () => { resetSelection(); loadMessages(); });
  const del = $("btnDeleteSelected");
  if (del && n) del.addEventListener("click", deleteSelectedMessages);
}

async function selectAllMessages() {
  const filters = currentFilters();
  const ids = await api().queryMessageIds(JSON.stringify(filters), state.sort);
  if (!ids || ids.error_key) {
    if (ids && ids.error_key) reportError(ids, { toast: true });
    return;
  }
  // Select-all scope: the currently displayed (filtered) message set.
  state.selection.ids = new Set(ids.map(Number));
  state.selection.mode = true;
  loadMessages();
}

async function deleteSelectedMessages() {
  const ids = Array.from(state.selection.ids);
  await deleteMessagesFromArchive(ids, !!state.filters.folder_id);
}

/* One confirmation, then a local-only deletion. No IMAP deletion command is
   ever issued; tombstones keep the messages from coming back. */
async function deleteMessagesFromArchive(ids, scopedToFolder) {
  if (!ids || !ids.length) {
    infoModal(t("viewer.deleteTitle", "Delete"),
      t("viewer.deleteNoneSelected", "No message selected."));
    return;
  }
  const n = ids.length;
  showModal(
    t("viewer.deleteTitle", "Delete"),
    (n === 1
      ? esc(t("viewer.deleteConfirm",
            "Delete this message from the local archive? The copy on the mail server is not affected."))
      : esc(t("viewer.deleteSelectedConfirm",
            "Delete {n} selected messages from the local archive? The copies on the mail server are not affected."))
        .replace("{n}", "<b>" + toDigits(n) + "</b>")) +
    (scopedToFolder
      ? '<div class="err-detail" style="margin-top:8px">' +
        esc(t("viewer.deleteFolderScope",
          "Only the copy in the current folder is removed; copies in other folders stay.")) + "</div>"
      : ""),
    [
      { label: t("common.cancel", "Cancel"), primary: true },
      {
        label: t("viewer.delete", "Delete"), danger: true, onClick: async () => {
          const res = await api().deleteMessages(JSON.stringify(ids),
            state.filters.folder_id || null);
          if (res && res.error_key) { reportError(res); return; }
          const removed = res ? (res.messages || 0) : 0;
          resetSelection();
          state.openMessageId = null;
          if (state.view === "archive") {
            loadFolders();
            loadMessages();
            renderDetailEmpty();
          }
          toast(esc(t("viewer.deleted", "{n} message(s) deleted from the local archive."))
            .replace("{n}", "<b>" + toDigits(removed || n) + "</b>"), "success");
        }
      }
    ]);
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
  state.openMessageId = null;
  $("detailEmpty").classList.remove("hidden");
  $("detailContent").classList.add("hidden");
  $("detailContent").innerHTML = "";
}

async function openMessage(id) {
  state.openMessageId = id;
  const m = await api().getMessage(id);
  if (!m || m.error_key) {
    if (m && m.error_key) reportError(m, { toast: true });
    else infoModal(t("common.error", "Error"), t("viewer.notFound", "Message not found."));
    renderDetailEmpty();
    return;
  }
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
  addRow(t("viewer.date", "Date"), formatDate(m.date_utc));
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
  let actionsHtml =
    '<div class="detail-actions">' +
    '<button class="btn btn-primary small" id="btnSaveEml">' + icon("file") +
      esc(t("viewer.saveEml", "Save original message (.eml)")) + "</button>" +
    '<button class="btn btn-danger small" id="btnDeleteMessage" title="' +
      esc(t("viewer.deleteTitle", "Delete from the local archive")) + '">' + icon("trash") +
      esc(t("viewer.delete", "Delete")) + "</button>" +
    "</div>";
  if (m.attachments && m.attachments.length && m.complete === false) {
    // Only partial rows (complete=0) that actually carry identified,
    // undownloaded attachment metadata show the button; never complete
    // rows and never messages without attachments.
    actionsHtml +=
      '<div class="detail-actions">' +
      '<button class="btn small" id="btnDownloadMissingAtt">' + icon("download") +
        esc(t("viewer.downloadAttachments", "Download Attachments")) + "</button>" +
      "</div>";
  }
  content.innerHTML =
    '<div class="detail-card">' +
    actionsHtml +
    '<h2 class="detail-subject" dir="auto">' + esc(subject) + "</h2>" +
    '<dl class="detail-meta">' + meta + "</dl>" + bodyHtml + attHtml +
    "</div>";
  content.querySelectorAll("[data-att]").forEach((btn) => {
    btn.addEventListener("click", () => saveAttachment(id, Number(btn.getAttribute("data-att"))));
  });
  const saveAll = $("btnSaveAllAtt");
  if (saveAll) saveAll.addEventListener("click", () =>
    api().saveAllAttachments(id).then((r) => fileResult(r)));
  const dlMissing = $("btnDownloadMissingAtt");
  if (dlMissing) dlMissing.addEventListener("click", () => downloadMissingAttachments([id]));
  $("btnSaveEml").addEventListener("click", () => api().saveEml(id).then((r) => fileResult(r)));
  const delBtn = $("btnDeleteMessage");
  if (delBtn) delBtn.addEventListener("click", () =>
    deleteMessagesFromArchive([id], !!state.filters.folder_id));
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

async function downloadMissingAttachments(messageIds) {
  const s = state;
  if (s.syncRunning) {
    errorModal({error_key: "sync.statusRunning"});
    return;
  }
  const account = activeAccount();
  if (!account) return;
  await askPassword(
    t("sync.title", "Synchronization"),
    esc(t("config.password", "Password")) + " — <b>" + esc(account.email) + "</b>",
    async (pw) => {
      let res = null;
      try {
        res = await api().downloadMissingAttachments(messageIds, pw);
      } catch (e) {
        reportError({ error_key: "common.internalError" });
        return;
      }
      if (reportError(res)) return;
      toast(esc(t("viewer.missingDownloaded",
        "Downloaded attachments for {n} message(s).")).replace("{n}", toDigits(res.completed || 0)), "success");
      if (state.openMessageId != null) {
        // Refresh the open message from its stored state: the row is now
        // complete, so the button disappears immediately — no restart.
        // (A partial row merged into an existing complete copy is dropped,
        // in which case the detail pane simply returns to its empty state.)
        const refreshed = await api().getMessage(state.openMessageId);
        if (refreshed && !refreshed.error_key) openMessage(state.openMessageId);
        else renderDetailEmpty();
      }
    });
}

function fileResult(r) {
  if (!r) return;
  if (r.error_key || r.error) { reportError(r); return; }
  toast(esc(t("common.savedToFile", "Saved to:")) + "<br>" + esc(r.path), "success");
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
      showModal(t("sync.cancelTitle", "Cancel Synchronization"),
        esc(t("sync.cancelConfirm",
          "Are you sure you want to cancel email synchronization?")), [
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
  askAttachmentMode(account, (withAttachments) =>
    askPassword(
      t("sync.title", "Synchronization"),
      esc(t("config.password", "Password")) + " — <b>" + esc(account.email) + "</b>",
      (pw) => startSync(account.id, pw, withAttachments)));
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
    '<p class="form-subtitle">' +
      esc(t("sync.attachModeHint", "When you start the synchronization, you choose whether attachments are downloaded.")) +
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

/* The attachment-mode dialog shown before every synchronization. The mode
   always defaults to "with attachments"; cancelling terminates the flow
   before any IMAP connection is made. */
function askAttachmentMode(account, onStart) {
  let mode = "with";
  const option = (value, label, desc) =>
    '<div class="attach-opt" data-mode="' + value + '" role="radio" ' +
      'aria-checked="false" tabindex="0">' +
      '<span class="attach-opt-radio"></span>' +
      '<span class="attach-opt-text">' +
        '<span class="attach-opt-label">' + esc(label) + "</span>" +
        '<span class="attach-opt-desc">' + esc(desc) + "</span>" +
      "</span>" +
    "</div>";
  const name = esc(account.display_name || account.email);
  showModal(
    t("sync.attachModeTitle", "Synchronization Settings"),
    '<p style="margin:0 0 14px">' +
      esc(t("sync.attachModeBody", "Choose how to synchronize."))
        .replace("{account}", "<b>" + name + "</b>") +
    "</p>" +
    '<div class="attach-opts" role="radiogroup">' +
      option("with", t("sync.attachModeWith", "Download emails with attachments"),
             t("sync.attachModeWithDesc", "With attachments")) +
      option("without", t("sync.attachModeWithout", "Download emails without attachments"),
             t("sync.attachModeWithoutDesc", "Without attachments")) +
    "</div>",
    [
      { label: t("sync.attachModeCancel", "Cancel") },
      {
        label: t("sync.start", "Start Synchronization"), primary: true,
        onClick: () => onStart(mode === "with")
      }
    ]);
  const card = $("modalCard");
  const opts = card.querySelectorAll(".attach-opt");
  const select = (el) => {
    mode = el.getAttribute("data-mode");
    opts.forEach((x) => {
      const on = x === el;
      x.classList.toggle("selected", on);
      x.setAttribute("aria-checked", on ? "true" : "false");
    });
  };
  opts.forEach((el) => {
    el.addEventListener("click", () => select(el));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(el); }
    });
  });
  if (opts.length) select(opts[0]);
}

async function startSync(accountId, password, withAttachments) {
  state.syncReport = null;
  state.pendingReport = null;
  if (!password) { renderSyncScreen(); return; }
  state.attachMode = withAttachments === false ? "without" : "with";
  state.syncRunning = true;
  state.syncAccountId = accountId;
  renderAccounts();
  renderSyncScreen();
  const res = await api().startSync(accountId, password, withAttachments !== false);
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
let pollErrorShown = false;
async function pollProgress() {
  if (pollTimer) clearTimeout(pollTimer);
  const s = await api().getSyncProgress();
  if (!s || s.error_key) {
    // The progress endpoint itself failed. Report it once and keep
    // watching: the sync thread may still be running fine, and the
    // progress view must never freeze on a spinner.
    if (s && s.error_key && !pollErrorShown) {
      reportError(s, { toast: true });
      pollErrorShown = true;
    }
    pollTimer = setTimeout(pollProgress, 2000);
    return;
  }
  pollErrorShown = false;
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
  if (r && r.tls_error) {
    // Untrusted certificate chain: render the error report, then put the
    // bilingual certificate dialog on top so the user can decide.
    openTlsTrustDialog(r);
  }
  const statusKey = {
    completed: "statusCompleted", incomplete: "statusIncomplete",
    cancelled: "statusCancelled", error: "statusError", running: "statusRunning",
  }[r.status] || "statusCompleted";
  const account = state.accounts.find((a) => a.id === (r.account_id || state.syncAccountId));
  const accountName = account ? (account.display_name || account.email) : (r.account_email || "");
  // A classified failure carries a translated error key; the raw server
  // text is kept out of the user-facing report.
  const errorBlock = r.error_key
    ? '<div class="sync-error">' + esc(t(r.error_key, r.error_key)) +
        (r.error_detail ? '<div class="err-detail">' + esc(r.error_detail) + "</div>" : "") +
      "</div>"
    : (r.last_error ? '<div class="sync-error">' + esc(r.last_error) + "</div>" : "");
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
    reportRow(t("report.deduped", "Already archived (deduplicated)"), r.deduped || 0) +
    reportRow(t("report.archived", "Archived"), r.total_messages || 0) +
    reportRow(t("report.contacts", "Contacts"), r.total_contacts || 0) +
    reportRow(t("report.attachments", "Attachments"), r.total_attachments || 0) +
    reportRow(t("report.failedMessages", "Failed or incomplete messages"),
      r.failed_messages || (r.errors || 0)) +
    reportRow(t("report.attachmentsFailed", "Failed or pending attachments"),
      r.attachments_failed || 0) +
    reportRow(t("report.skippedDeleted", "Skipped (locally deleted)"),
      r.skipped_deleted || 0) +
    reportRow(t("report.elapsed", "Elapsed"), formatDuration(r.elapsed || 0)) +
    "</div>" +
    (r.error_folders && r.error_folders.length
      ? '<div class="sync-error">' + esc(t("report.errorFolders", "Error folders")) + ": " +
        esc(r.error_folders.join(", ")) + "</div>" : "") +
    failedItemsBlock(r) +
    errorBlock +
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

/* Optional per-item details of a partial synchronization: the affected
   folder, UID and the sanitized reason, never credentials or raw protocol
   text. Capped so a very bad mailbox cannot flood the report. */
function failedItemsBlock(r) {
  const items = r && r.failed_items ? r.failed_items : [];
  if (!items.length) return "";
  const rows = items.map((it) =>
    '<div class="failed-item"><span class="failed-folder">' + esc(it.folder || "—") +
    '</span><span class="failed-uid">UID ' + toDigits(it.uid || 0) + "</span>" +
    '<span class="failed-reason" dir="auto">' + esc(it.reason || it.stage || "") +
    "</span></div>").join("");
  return '<div class="failed-items-block">' +
    '<div class="failed-items-title">' +
      esc(t("report.failedItems", "Messages needing another synchronization")) +
      " (" + toDigits(items.length) +
      (r.failed_messages > items.length
        ? " " + esc(t("report.of", "of")) + " " + toDigits(r.failed_messages) : "") +
    ")</div>" + rows + "</div>";
}

/* ---------------------------------------------------- certificate trust */

function openTlsTrustDialog(r) {
  const cert = r.tls_error || {};
  const accountId = r.account_id || state.syncAccountId;
  const account = state.accounts.find((a) => a.id === accountId);
  const serverLine = esc(cert.host || "") + (cert.port ? ":" + toDigits(cert.port) : "");
  const pairs = (cert.sha256 || "").match(/.{2}/g);
  const fingerprint = pairs ? pairs.join(":") : esc(cert.sha256 || "");
  const accountName = account ? (account.email || account.imap_host || "") : "";
  const row = (label, value) =>
    '<div class="cert-row"><span>' + esc(label) + "</span><b>" + esc(value || "—") + "</b></div>";
  const body =
    (cert.pin_mismatch
      ? '<div class="sync-error" style="margin:0 0 12px">' +
        esc(t("tls.pinMismatch", "The certificate no longer matches the certificate you previously trusted for this account.")) + "</div>"
      : '<p style="margin:0 0 12px">' +
        esc(t("tls.body", "The IMAP server's TLS certificate could not be verified.")) + "</p>") +
    '<div class="cert-card">' +
    row(t("tls.server", "Server"), (accountName ? accountName + " · " : "") + serverLine) +
    row(t("tls.subject", "Subject"), cert.subject) +
    row(t("tls.issuer", "Issuer"), cert.issuer) +
    row(t("tls.validFrom", "Valid from"), cert.valid_from) +
    row(t("tls.validTo", "Valid until"), cert.valid_to) +
    "</div>" +
    '<div class="cert-fp-label">' + esc(t("tls.fingerprint", "SHA-256 fingerprint")) + "</div>" +
    '<div class="cert-fingerprint">' + fingerprint + "</div>" +
    '<p class="cert-hint">' +
      esc(t("tls.trustHint", "Trusting remembers this certificate for this account only; MailDaan refuses to connect when it changes.")) +
    "</p>";
  showModal(t("tls.title", "Untrusted certificate"), body, [
    { label: t("common.cancel", "Cancel") },
    {
      label: t("tls.importCa", "Import CA certificate"), primary: true, keepOpen: false,
      onClick: () => onImportCa(r)
    },
    {
      label: t("tls.trust", "Trust this certificate"), danger: true,
      onClick: () => onTrustCertificate(r, cert)
    }
  ]);
}

async function onTrustCertificate(r, cert) {
  const accountId = r.account_id || state.syncAccountId;
  const res = await api().trustServerCertificate(accountId, cert.sha256 || "");
  if (!res) return;
  if (res.error_key) { errorModal(res); return; }
  if (res.error) { infoModal(t("common.error", "Error"), res.error); return; }
  restartSyncAfterTrust(accountId);
}

async function onImportCa(r) {
  const accountId = r.account_id || state.syncAccountId;
  const res = await api().importCaCertificate(accountId);
  if (!res) return;  // file picker cancelled
  if (res.error_key || res.error) { reportError(res); return; }
  restartSyncAfterTrust(accountId);
}

function restartSyncAfterTrust(accountId) {
  const account = state.accounts.find((a) => a.id === accountId);
  if (!account) return;
  state.syncReport = null;
  askPassword(
    t("sync.title", "Synchronization"),
    esc(t("config.password", "Password")) + " — <b>" + esc(account.email) + "</b>",
    (pw) => startSync(account.id, pw, state.attachMode !== "without"));
}

/* ------------------------------------------------------------------- export */

/* ------------------------------------------------------ report builder */

/* The Excel report is only produced through this configuration dialog:
   purpose, scope and output content are chosen before any file is
   generated. Nothing here touches the archive — a report is read-only. */
const REPORT_DRAFT = {
  purpose: "comprehensive",
  range: "all",
  dateFrom: "",
  dateTo: "",
  allFolders: true,
  folderIds: [],
  direction: "both",
  includeDrafts: false,
  keyword: "",
  content: {
    summary: true, messages: true, contacts: true, domains: true,
    attachments: false, body: false, emlPath: false, allContacts: false,
    charts: true,
  },
  columns: {
    date: true, direction: true, senderName: true, senderEmail: true,
    recipients: true, cc: false, bcc: false, subject: true, folders: true,
    size: true, attachments: true,
  },
};

/* Recommended output content per report purpose. The user can still change
   every checkbox afterwards. Full bodies stay off in every preset. */
const REPORT_PRESETS = {
  comprehensive: { summary: true, messages: false, contacts: true,
                   domains: true, attachments: false },
  activity: { summary: true, messages: true, contacts: false,
              domains: false, attachments: false },
  contacts: { summary: true, messages: false, contacts: true,
              domains: false, attachments: false },
  domains: { summary: true, messages: false, contacts: false,
             domains: true, attachments: false },
  messages: { summary: false, messages: true, contacts: false,
              domains: false, attachments: true },
};

let reportState = null;
let reportCountTimer = null;
let reportCountSeq = 0;

function rdKey(key, fallback) { return t("reportDialog." + key, fallback); }

function reportOptions() {
  const d = REPORT_DRAFT;
  return {
    purpose: d.purpose,
    lang: state.lang,
    scope: {
      range: d.range,
      date_from: d.dateFrom,
      date_to: d.dateTo,
      folder_ids: d.allFolders ? [] : d.folderIds.slice(),
      direction: d.direction,
      include_drafts: d.includeDrafts,
      keyword: d.keyword,
    },
    content: Object.assign({}, d.content),
    columns: Object.assign({}, d.columns),
  };
}

function reportSummaryText() {
  const d = REPORT_DRAFT;
  const account = activeAccount();
  const ranges = { all: rdKey("rangeAll", "All time"),
                   "30d": rdKey("range30", "Last 30 days"),
                   year: rdKey("rangeYear", "Last year"),
                   custom: rdKey("rangeCustom", "Custom dates") };
  const dirs = { both: rdKey("dirBoth", "Incoming and outgoing"),
                 incoming: rdKey("dirIncoming", "Incoming only"),
                 outgoing: rdKey("dirOutgoing", "Outgoing only") };
  const parts = [];
  if (account) parts.push(account.display_name || account.email);
  parts.push(ranges[d.range] || ranges.all);
  if (d.range === "custom")
    parts.push((d.dateFrom || "…") + " → " + (d.dateTo || "…"));
  parts.push((d.allFolders || !d.folderIds.length)
    ? rdKey("allFolders", "All folders")
    : toDigits(d.folderIds.length) + " " + rdKey("folders", "Mail folders"));
  parts.push(dirs[d.direction] || dirs.both);
  parts.push(rdKey(d.includeDrafts ? "draftsIncluded" : "draftsExcluded", "Drafts"));
  if (d.keyword.trim())
    parts.push(rdKey("keyword", "Keyword") + ": " + d.keyword.trim());
  return parts.join(" • ");
}

function renderReportDialog() {
  const d = REPORT_DRAFT;
  const purposes = [
    ["comprehensive", rdKey("purposeComprehensive", "Comprehensive / Management Report"),
     rdKey("purposeComprehensiveDesc", "")],
    ["activity", rdKey("purposeActivity", "Correspondence Activity"),
     rdKey("purposeActivityDesc", "")],
    ["contacts", rdKey("purposeContacts", "Contacts Analysis"),
     rdKey("purposeContactsDesc", "")],
    ["domains", rdKey("purposeDomains", "Email Domain Analysis"),
     rdKey("purposeDomainsDesc", "")],
    ["messages", rdKey("purposeMessages", "Detailed Message Export"),
     rdKey("purposeMessagesDesc", "")],
  ];
  const purposeRow = (value, label, desc) =>
    '<label class="rd-opt' + (d.purpose === value ? " selected" : "") + '">' +
      '<input type="radio" name="rdPurpose" value="' + value + '"' +
      (d.purpose === value ? " checked" : "") + ">" +
      '<span class="rd-opt-text"><span class="rd-opt-label">' + esc(label) + "</span>" +
      (desc ? '<span class="rd-opt-desc">' + esc(desc) + "</span>" : "") +
      "</span></label>";
  const ranges = [["all", rdKey("rangeAll", "All time")],
                  ["30d", rdKey("range30", "Last 30 days")],
                  ["year", rdKey("rangeYear", "Last year")],
                  ["custom", rdKey("rangeCustom", "Custom dates")]];
  const dirs = [["both", rdKey("dirBoth", "Incoming and outgoing")],
                ["incoming", rdKey("dirIncoming", "Incoming only")],
                ["outgoing", rdKey("dirOutgoing", "Outgoing only")]];
  const sel = (id, options, current) =>
    '<select id="' + id + '" class="select">' +
      options.map((o) => '<option value="' + o[0] + '"' +
        (o[0] === current ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
    "</select>";
  const folderList = reportState.folders.map((f) => {
    const name = state.folderDisplay[f.display_name] || f.display_name || f.imap_name;
    const on = d.folderIds.indexOf(f.id) >= 0;
    return '<label class="rd-check"><input type="checkbox" class="rdFolder" value="' +
      f.id + '"' + (on ? " checked" : "") + ">" + esc(name) + "</label>";
  }).join("");
  const c = d.content;
  const contentCheck = (key, label, hint) =>
    '<label class="rd-check' + (c[key] ? " on" : "") + '">' +
      '<input type="checkbox" class="rdContent" data-key="' + key + '"' +
      (c[key] ? " checked" : "") + "><span>" + esc(label) +
      (hint ? '<span class="rd-hint">' + esc(hint) + "</span>" : "") +
      "</span></label>";
  const colCheck = (key, label) =>
    '<label class="rd-check' + (d.columns[key] ? " on" : "") + '">' +
      '<input type="checkbox" class="rdColumn" data-key="' + key + '"' +
      (d.columns[key] ? " checked" : "") + ">" + esc(label) + "</label>";
  const body =
    '<div class="rd-section"><div class="rd-label">' +
      esc(rdKey("purposeTitle", "Report purpose")) + "</div>" +
      '<div class="rd-opts">' + purposes.map((p) => purposeRow(p[0], p[1], p[2])).join("") +
      "</div></div>" +
    '<div class="rd-section"><div class="rd-label">' +
      esc(rdKey("scopeTitle", "Reporting scope")) + "</div>" +
      '<div class="rd-row">' +
        '<label class="rd-field"><span>' + esc(rdKey("account", "Account")) + "</span>" +
          '<input type="text" class="select" dir="ltr" value="' +
          esc((activeAccount() || {}).email || "") + '" disabled></label>' +
        '<label class="rd-field"><span>' + esc(rdKey("dateRange", "Date range")) + "</span>" +
          sel("rdRange", ranges, d.range) + "</label>" +
        '<label class="rd-field rd-date' + (d.range === "custom" ? "" : " hidden") +
          '"><span>' + esc(rdKey("dateFrom", "From")) + "</span>" +
          '<input type="date" id="rdFrom" class="select" value="' + esc(d.dateFrom) + '"></label>' +
        '<label class="rd-field rd-date' + (d.range === "custom" ? "" : " hidden") +
          '"><span>' + esc(rdKey("dateTo", "To")) + "</span>" +
          '<input type="date" id="rdTo" class="select" value="' + esc(d.dateTo) + '"></label>' +
      "</div>" +
      '<div class="rd-row">' +
        '<div class="rd-field rd-folders"><span>' + esc(rdKey("folders", "Mail folders")) + "</span>" +
          '<label class="rd-check"><input type="checkbox" id="rdAllFolders"' +
            (d.allFolders ? " checked" : "") + ">" +
            esc(rdKey("allFolders", "All folders")) + "</label>" +
          '<div class="rd-folder-list' + (d.allFolders ? " hidden" : "") + '" id="rdFolderList">' +
            (folderList || '<div class="rd-hint">' + esc(rdKey("noFolders", "")) + "</div>") +
          "</div>" +
        "</div>" +
        '<label class="rd-field"><span>' + esc(rdKey("direction", "Direction")) + "</span>" +
          sel("rdDirection", dirs, d.direction) + "</label>" +
        '<div class="rd-field"><span>&nbsp;</span>' +
          '<label class="rd-check"><input type="checkbox" id="rdDrafts"' +
            (d.includeDrafts ? " checked" : "") + ">" +
            esc(rdKey("includeDrafts", "Include drafts")) + "</label>" +
          '<div class="rd-hint">' + esc(rdKey("draftsHint", "")) + "</div>" +
        "</div>" +
        '<label class="rd-field rd-keyword"><span>' + esc(rdKey("keyword", "Keyword (optional)")) + "</span>" +
          '<input type="text" id="rdKeyword" class="select" value="' + esc(d.keyword) +
          '" data-i18n-ph="reportDialog.keywordPlaceholder" placeholder="' +
          esc(rdKey("keywordPlaceholder", "")) + '"></label>' +
      "</div>" +
    "</div>" +
    '<div class="rd-section"><div class="rd-label">' +
      esc(rdKey("contentTitle", "Output content")) + "</div>" +
      '<div class="rd-checks">' +
        contentCheck("summary", rdKey("contentSummary", "Executive summary and charts"), "") +
        contentCheck("messages", rdKey("contentMessages", "Message details"), "") +
        contentCheck("contacts", rdKey("contentContacts", "Contacts"), "") +
        contentCheck("domains", rdKey("contentDomains", "Email domains"), "") +
        contentCheck("attachments", rdKey("contentAttachments", "Attachment metadata"), "") +
        contentCheck("body", rdKey("contentBody", "Include full message bodies"),
                     rdKey("contentBodyHint", "")) +
        contentCheck("allContacts", rdKey("contentAllContacts", "Complete directory"), "") +
        contentCheck("emlPath", rdKey("contentEmlPath", "Include original EML paths"), "") +
      "</div>" +
      '<div class="rd-columns' + (c.messages ? "" : " hidden") + '">' +
        '<div class="rd-label">' + esc(rdKey("contentColumns", "Message columns")) + "</div>" +
        '<div class="rd-checks">' +
          colCheck("date", rdKey("colDate", "Date")) +
          colCheck("direction", rdKey("colDirection", "Direction")) +
          colCheck("senderName", rdKey("colSenderName", "Sender name")) +
          colCheck("senderEmail", rdKey("colSenderEmail", "Sender email")) +
          colCheck("recipients", rdKey("colRecipients", "Recipients")) +
          colCheck("cc", rdKey("colCc", "CC")) +
          colCheck("bcc", rdKey("colBcc", "BCC")) +
          colCheck("subject", rdKey("colSubject", "Subject")) +
          colCheck("folders", rdKey("colFolders", "Folders")) +
          colCheck("size", rdKey("colSize", "Size")) +
          colCheck("attachments", rdKey("colAttachments", "Attachments")) +
        "</div>" +
      "</div>" +
    "</div>" +
    '<div class="rd-section"><div class="rd-label">' +
      esc(rdKey("summaryTitle", "Summary")) + "</div>" +
      '<div class="rd-summary" id="rdSummary"></div>' +
      '<div class="rd-count" id="rdCount"></div>' +
    "</div>";
  showModal(rdKey("title", "Create Report"), body, [
    { label: rdKey("cancel", "Cancel"),
      onClick: () => { closeReportDialog(); } },
    { label: rdKey("generate", "Create Report"), primary: true, keepOpen: true,
      onClick: onGenerateReport },
  ]);
  $("modalCard").classList.add("report-card");
  updateReportSummary();
  bindReportDialog();
}

function updateReportSummary() {
  const summary = $("rdSummary");
  if (!summary) return;
  const content = REPORT_DRAFT.content;
  const chosen = [];
  if (content.summary) chosen.push(rdKey("contentSummary", "Summary"));
  if (content.messages) chosen.push(rdKey("contentMessages", "Messages"));
  if (content.contacts) chosen.push(rdKey("contentContacts", "Contacts"));
  if (content.domains) chosen.push(rdKey("contentDomains", "Domains"));
  if (content.attachments) chosen.push(rdKey("contentAttachments", "Attachments"));
  summary.innerHTML = esc(reportSummaryText()) +
    '<div class="rd-output">' + esc(chosen.join(" • ")) + "</div>";
}

function refreshReportCount() {
  if (!reportState || !reportState.open) return;
  if (reportCountTimer) clearTimeout(reportCountTimer);
  reportCountTimer = setTimeout(async () => {
    const seq = ++reportCountSeq;
    const box = $("rdCount");
    if (box) box.innerHTML = '<span class="rd-spinner"></span>' +
      esc(rdKey("matchingCounting", "Counting…"));
    const res = await api().previewReportCount(JSON.stringify(reportOptions()));
    if (seq !== reportCountSeq || !reportState || !reportState.open) return;
    if (!box) return;
    if (res && res.error_key) { box.textContent = ""; return; }
    const n = res ? res.count : 0;
    box.innerHTML = "<b>" + toDigits(n) + "</b> " +
      esc(rdKey("matching", "Matching messages")) +
      (n === 0 ? '<div class="rd-warn">' + esc(rdKey("matchingNone", "")) + "</div>" : "");
  }, 350);
}

function bindReportDialog() {
  const card = $("modalCard");
  card.querySelectorAll('input[name="rdPurpose"]').forEach((el) => {
    el.addEventListener("change", () => {
      REPORT_DRAFT.purpose = el.value;
      const preset = REPORT_PRESETS[el.value] || {};
      Object.keys(preset).forEach((k) => { REPORT_DRAFT.content[k] = preset[k]; });
      renderReportDialog();
      refreshReportCount();
    });
  });
  const range = $("rdRange");
  if (range) range.addEventListener("change", () => {
    REPORT_DRAFT.range = range.value;
    card.querySelectorAll(".rd-date").forEach((el) =>
      el.classList.toggle("hidden", range.value !== "custom"));
    refreshReportCount();
  });
  ["rdFrom", "rdTo"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("change", () => {
      REPORT_DRAFT[id === "rdFrom" ? "dateFrom" : "dateTo"] = el.value;
      refreshReportCount();
    });
  });
  const all = $("rdAllFolders");
  if (all) all.addEventListener("change", () => {
    REPORT_DRAFT.allFolders = all.checked;
    REPORT_DRAFT.folderIds = [];
    const list = $("rdFolderList");
    if (list) {
      list.classList.toggle("hidden", all.checked);
      list.querySelectorAll(".rdFolder").forEach((b) => { b.checked = false; });
    }
    refreshReportCount();
  });
  card.querySelectorAll(".rdFolder").forEach((box) => {
    box.addEventListener("change", () => {
      const id = Number(box.value);
      const ids = REPORT_DRAFT.folderIds;
      if (box.checked && ids.indexOf(id) < 0) ids.push(id);
      if (!box.checked) REPORT_DRAFT.folderIds = ids.filter((x) => x !== id);
      REPORT_DRAFT.allFolders = REPORT_DRAFT.folderIds.length === 0;
      if (all) all.checked = REPORT_DRAFT.allFolders;
      const list = $("rdFolderList");
      if (list) list.classList.toggle("hidden", REPORT_DRAFT.allFolders);
      refreshReportCount();
    });
  });
  const direction = $("rdDirection");
  if (direction) direction.addEventListener("change", () => {
    REPORT_DRAFT.direction = direction.value;
    refreshReportCount();
  });
  const drafts = $("rdDrafts");
  if (drafts) drafts.addEventListener("change", () => {
    REPORT_DRAFT.includeDrafts = drafts.checked;
    refreshReportCount();
  });
  const keyword = $("rdKeyword");
  if (keyword) keyword.addEventListener("input", () => {
    REPORT_DRAFT.keyword = keyword.value;
    refreshReportCount();
  });
  card.querySelectorAll(".rdContent").forEach((box) => {
    box.addEventListener("change", () => {
      REPORT_DRAFT.content[box.getAttribute("data-key")] = box.checked;
      const label = box.closest(".rd-check");
      if (label) label.classList.toggle("on", box.checked);
      const columns = card.querySelector(".rd-columns");
      if (columns) columns.classList.toggle("hidden", !REPORT_DRAFT.content.messages);
      updateReportSummary();
    });
  });
  card.querySelectorAll(".rdColumn").forEach((box) => {
    box.addEventListener("change", () => {
      REPORT_DRAFT.columns[box.getAttribute("data-key")] = box.checked;
      const label = box.closest(".rd-check");
      if (label) label.classList.toggle("on", box.checked);
    });
  });
}

function openReportDialog() {
  if (state.busy) return;
  const account = activeAccount();
  if (!account) { errorModal({ error_key: "accounts.none" }); return; }
  state.busy = true;
  api().reportFolders().then((folders) => {
    state.busy = false;
    reportState = {
      open: true,
      folders: Array.isArray(folders) && !folders.error_key ? folders : [],
    };
    renderReportDialog();
    refreshReportCount();
  });
}

// Closing the report dialog also drops the wider card layout so later
// modals keep their normal width.
function resetReportState() {
  reportState = { open: false, folders: [] };
  const card = $("modalCard");
  if (card) card.classList.remove("report-card");
}

function closeReportDialog() {
  resetReportState();
  hideModal();
}

async function onGenerateReport() {
  const content = REPORT_DRAFT.content;
  if (!(content.summary || content.messages || content.contacts ||
        content.domains || content.attachments)) {
    toast("<div>" + esc(rdKey("selectContent", "Select at least one output.")) + "</div>",
      "error");
    return;
  }
  if (REPORT_DRAFT.range === "custom" && !(REPORT_DRAFT.dateFrom && REPORT_DRAFT.dateTo)) {
    toast("<div>" + esc(rdKey("customRangeNeeded", "")) + "</div>", "error");
    return;
  }
  const card = $("modalCard");
  const buttons = card ? card.querySelectorAll(".modal-actions button") : [];
  buttons.forEach((b) => { b.disabled = true; });
  const last = buttons[buttons.length - 1];
  if (last) last.textContent = rdKey("generating", "Generating…");
  try {
    const res = await api().createReport(JSON.stringify(reportOptions()));
    if (!res) { closeReportDialog(); return; }
    if (res.error_key || res.error) {
      resetReportState();
      reportError(res);
      return;
    }
    closeReportDialog();
    toast(esc(t("export.created", "Report created.")) + "<br>" + esc(res.path), "success");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    if (last) last.textContent = rdKey("generate", "Create Report");
  }
}

/* ----------------------------------------------------------------- settings */

/* ------------------------------------------------------------------ updates
   Compact update control: a small icon above the Exit button
   in the sidebar bottom section, with a subtle badge when a newer release is
   available and a small contextual popover for checking, downloading,
   verifying and installing. The updater backend (GitHub API, SHA-256
   verification, safe installation) is unchanged. */

const UPDATE = {
  IDLE: "idle", CHECKING: "checking", UPTODATE: "uptodate",
  AVAILABLE: "available", DOWNLOADING: "downloading", READY: "ready", ERROR: "error",
};

function updateBadgeVisible() {
  return state.update.status === UPDATE.AVAILABLE || state.update.status === UPDATE.READY;
}

function refreshUpdateChrome() {
  const badge = $("updateBadge");
  if (badge) {
    badge.classList.toggle("hidden", !updateBadgeVisible());
    badge.classList.toggle("ready", state.update.status === UPDATE.READY);
  }
  const btn = $("btnUpdate");
  if (btn) {
    const u = state.update;
    btn.setAttribute("aria-expanded", u.popoverOpen ? "true" : "false");
    // Icon-driven states on the sidebar icon itself:
    //  available  -> download icon + badge dot
    //  downloading-> circular ring progress with live percentage
    //  ready      -> check icon (success) + green badge dot
    const pct = Math.max(0, Math.min(100, Math.round(u.percent || 0)));
    btn.classList.toggle("downloading", u.status === UPDATE.DOWNLOADING);
    btn.classList.toggle("ready", u.status === UPDATE.READY);
    const iconUse = $("updateIcon");
    if (iconUse) iconUse.setAttribute("href", u.status === UPDATE.READY ? "#i-check" : "#i-download");
    const ring = $("updateRingFill");
    if (ring) ring.style.strokeDashoffset = String(100 - pct);
    const pctNode = $("updatePct");
    if (pctNode) pctNode.textContent = toDigits(pct) + "%";
    let title = t("update.checkNow", "Check for Updates");
    if (u.status === UPDATE.AVAILABLE)
      title = t("update.newVersion", "Version available").replace("{version}", u.version || "");
    else if (u.status === UPDATE.READY) title = t("update.install", "Install Update");
    else if (u.status === UPDATE.DOWNLOADING)
      title = t("update.downloading", "Downloading…") + " " + toDigits(pct) + "%";
    btn.title = title;
    btn.setAttribute("aria-label", title);
  }
}

function toggleUpdatePopover() {
  if (state.update.popoverOpen) closeUpdatePopover();
  else openUpdatePopover();
}

function openUpdatePopover() {
  state.update.popoverOpen = true;
  $("updatePopover").classList.remove("hidden");
  refreshUpdateChrome();
  // Opening the popover always reports the current state: pick up an
  // in-flight or finished download, then perform a fresh check.
  refreshUpdateProgress().then(() => {
    if (state.update.status !== UPDATE.DOWNLOADING &&
        state.update.status !== UPDATE.READY) {
      updateCheck(true);
    }
  });
  if (state.update.status === UPDATE.IDLE) state.update.status = UPDATE.CHECKING;
  renderUpdatePopover();
}

function closeUpdatePopover() {
  state.update.popoverOpen = false;
  $("updatePopover").classList.add("hidden");
  refreshUpdateChrome();
}

/* Place the popover relative to the sidebar update icon so it is always
   fully inside the application window: it opens upward from the icon and
   extends inward toward the main content area (mirrored for RTL), with the
   size clamped to the viewport. Runs synchronously before the first paint,
   so there is no flicker. */
function placeUpdatePopover() {
  const box = $("updatePopover");
  const btn = $("btnUpdate");
  if (!box || !btn) return;
  if (box.classList.contains("hidden")) return;
  const margin = 10;
  box.style.maxHeight = Math.max(160, window.innerHeight - 2 * margin) + "px";
  const width = box.offsetWidth;
  const height = box.offsetHeight;
  const rect = btn.getBoundingClientRect();
  // Vertical: open upward with a small gap; never above the top margin and
  // never past the bottom margin (short windows fall back to covering the
  // icon, which is harmless — the popover sits on top).
  let top = rect.top - height - 8;
  top = Math.max(margin, Math.min(top, window.innerHeight - margin - height));
  // Horizontal: the popover's leading edge aligns with the icon's leading
  // edge and it extends inward toward the main content area.
  let left = state.lang === "fa" ? rect.right - width : rect.left;
  left = Math.max(margin, Math.min(left, window.innerWidth - margin - width));
  box.style.left = left + "px";
  box.style.top = top + "px";
}

/* Translate the updater's stable error strings. */
function mapUpdateError(err) {
  const text = String(err || "");
  if (text === "cancelled") return t("update.cancelled", "The download was cancelled.");
  if (text === "install") return t("update.installFailed", "The update could not be installed.");
  if (text.indexOf("checksum verification failed") !== -1)
    return t("update.checksumFail", "Checksum verification failed.");
  if (text.indexOf("checksum unavailable") !== -1)
    return t("update.checksumMissing", "Checksum not available.");
  if (text.indexOf("installer not verified") !== -1)
    return t("update.installFailed", "The update could not be installed.");
  return t("update.downloadFailed", "The update could not be downloaded.");
}

function appVersionDisplay() {
  return window.__maildaanVersion || "";
}

function renderUpdatePopover() {
  const box = $("updatePopover");
  if (!box) return;
  const u = state.update;
  const head = '<div class="upd-head"><span class="upd-title">' +
    esc(t("nav.updates", "Updates")) + "</span></div>";
  const actions = (buttons) => '<div class="upd-actions">' + buttons + "</div>";
  const btn = (id, label, primary, danger) =>
    '<button class="btn small' + (primary ? " btn-primary" : "") +
      (danger ? " btn-danger" : "") + '" id="' + id + '">' + esc(label) + "</button>";
  let body = "";
  let footer = "";
  switch (u.status) {
    case UPDATE.CHECKING:
      body = '<div class="upd-message"><span class="upd-spinner"></span>' +
        '<span>' + esc(t("update.checking", "Checking…")) + "</span></div>";
      break;
    case UPDATE.UPTODATE:
      body = '<div class="upd-message">' + icon("check") +
        '<span class="upd-ok">' +
        esc(t("update.upToDateBody", "You are running the latest version of MailDaan.")) +
        "</span></div>" +
        '<p class="upd-version" style="margin-top:8px">' +
        esc(t("update.currentVersion", "Current version")) + ": " +
        esc(appVersionDisplay()) + "</p>";
      footer = actions(btn("updRetry", t("update.retry", "Check Again")) +
        btn("updClose", t("common.close", "Close")));
      break;
    case UPDATE.AVAILABLE:
      body = '<p class="upd-version">' +
        esc(t("update.newVersion", "Version {version} is available.")
          .replace("{version}", u.version || "")) + "</p>" +
        (u.notes ? '<div class="upd-notes">' + esc(u.notes) + "</div>" : "");
      footer = actions(btn("updDownload", t("update.download", "Download Update"), true) +
        btn("updClose", t("common.close", "Close")));
      break;
    case UPDATE.DOWNLOADING:
      body = '<div class="upd-message"><span class="upd-spinner"></span>' +
        '<span>' + esc(t("update.downloading", "Downloading…")) + "</span></div>" +
        '<div class="progress-block" style="margin:10px 0 0">' +
        '<div class="progress-head"><span class="pct">' + toDigits(u.percent || 0) +
        '%</span></div>' +
        '<div class="progress-track"><div class="progress-fill" style="width:' +
        (u.percent || 0) + '%"></div></div>' +
        "</div>";
      footer = actions(btn("updCancel", t("update.cancel", "Cancel"), false, true));
      break;
    case UPDATE.READY:
      body = '<div class="upd-message">' + icon("check") +
        '<span class="upd-ok">' + esc(t("update.verified", "Installer verified.")) +
        "</span></div>" +
        '<p class="upd-body" style="margin:6px 0 0">' +
        esc(t("update.installHint", "MailDaan will close and the verified installer will open.")) +
        "</p>";
      footer = actions(btn("updInstall", t("update.install", "Install Update"), true) +
        btn("updClose", t("common.close", "Close")));
      break;
    case UPDATE.ERROR:
      body = '<div class="upd-error">' +
        esc(u.error || t("update.error", "Update check failed.")) + "</div>";
      footer = actions(btn("updRetry", t("update.retry", "Check Again"), true));
      break;
    default:
      body = '<div class="upd-body">' +
        esc(t("update.upToDateBody", "You are running the latest version of MailDaan.")) +
        "</div>";
      footer = actions(btn("updRetry", t("update.retry", "Check Again")) +
        btn("updClose", t("common.close", "Close")));
      break;
  }
  box.innerHTML = head + '<div class="upd-body">' + body + "</div>" + footer;

  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.addEventListener("click", fn);
  };
  bind("updRetry", () => updateCheck(true));
  bind("updDownload", updateDownload);
  bind("updCancel", updateCancel);
  bind("updInstall", attemptUpdateInstall);
  bind("updClose", closeUpdatePopover);
  // Re-anchor after every content change: the popover's height depends on
  // its state and must stay fully inside the window.
  placeUpdatePopover();
}

/* Automatic check shortly after boot: only lights the sidebar badge. */
async function checkForUpdatesAuto() {
  if (state.update.checked) return;
  try {
    const res = await api().checkForUpdates(false);
    state.update.checked = true;
    if (res && res.available) {
      state.update.status = UPDATE.AVAILABLE;
      state.update.version = res.version || "";
      state.update.notes = res.notes || "";
      refreshUpdateChrome();
    }
  } catch (e) {
    /* harmless: no badge, nothing to report */
  }
}

async function updateCheck(force) {
  state.update.status = UPDATE.CHECKING;
  if (state.update.popoverOpen) renderUpdatePopover();
  try {
    const res = await api().checkForUpdates(!!force);
    state.update.checked = true;
    if (res && res.available) {
      state.update.status = UPDATE.AVAILABLE;
      state.update.version = res.version || "";
      state.update.notes = res.notes || "";
    } else if (res && res.offline) {
      state.update.status = UPDATE.ERROR;
      state.update.error = t("update.offline", "Update check unavailable (offline).");
    } else {
      state.update.status = UPDATE.UPTODATE;
    }
  } catch (e) {
    state.update.status = UPDATE.ERROR;
    state.update.error = t("update.error", "Update check failed.");
  }
  refreshUpdateChrome();
  if (state.update.popoverOpen) renderUpdatePopover();
}

async function updateDownload() {
  state.update.status = UPDATE.DOWNLOADING;
  state.update.percent = 0;
  state.update.installPrompted = false;
  refreshUpdateChrome();
  if (state.update.popoverOpen) renderUpdatePopover();
  let res = null;
  try {
    res = await api().startUpdateDownload();
  } catch (e) {
    res = { error: "download" };
  }
  if (res && res.error) {
    state.update.status = UPDATE.ERROR;
    state.update.error = mapUpdateError(res.error);
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    else toast("<div>" + esc(state.update.error) + "</div>", "error");
    return;
  }
  pollUpdateProgress();
}

async function updateCancel() {
  try {
    await api().cancelUpdateDownload();
  } catch (e) {
    toast("<div>" + esc(t("update.downloadFailed",
      "The update could not be downloaded.")) + "</div>", "error");
  }
  // The polling loop observes the cancellation and renders the final state.
}

/* One-shot state check used when the popover opens, so an already running
   or completed download is picked up without waiting for the next poll. */
async function refreshUpdateProgress() {
  const res = await api().updateDownloadProgress();
  if (!res || res.error_key) return;
  if (res.path_ready) {
    if (state.update.status !== UPDATE.READY) {
      state.update.status = UPDATE.READY;
      state.update.percent = 100;
      refreshUpdateChrome();
      if (state.update.popoverOpen) renderUpdatePopover();
      promptInstallUpdate();
    }
    return;
  }
  if (res.running) {
    if (state.update.status !== UPDATE.DOWNLOADING) {
      state.update.status = UPDATE.DOWNLOADING;
      state.update.percent = res.percent || 0;
      refreshUpdateChrome();
      if (state.update.popoverOpen) renderUpdatePopover();
    }
    pollUpdateProgress();
    return;
  }
  if (res.error && state.update.status !== UPDATE.ERROR) {
    state.update.status = UPDATE.ERROR;
    state.update.error = mapUpdateError(res.error);
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
  }
}

let updatePollTimer = null;
async function pollUpdateProgress() {
  if (updatePollTimer) clearTimeout(updatePollTimer);
  const res = await api().updateDownloadProgress();
  if (!res || res.error_key) {
    state.update.status = UPDATE.ERROR;
    state.update.error = t("update.downloadFailed", "The update could not be downloaded.");
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    return;
  }
  if (res.running) {
    state.update.status = UPDATE.DOWNLOADING;
    state.update.percent = res.percent || 0;
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    updatePollTimer = setTimeout(pollUpdateProgress, 800);
    return;
  }
  if (res.path_ready) {
    const wasReady = state.update.status === UPDATE.READY;
    state.update.status = UPDATE.READY;
    state.update.percent = 100;
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    if (!wasReady) promptInstallUpdate();
    return;
  }
  if (res.error) {
    state.update.status = UPDATE.ERROR;
    state.update.error = mapUpdateError(res.error);
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    else toast("<div>" + esc(state.update.error) + "</div>", "error");
  }
}

/* After the download has finished and the SHA-256 checksum has been
   verified, the user is asked explicitly — the update is never installed
   automatically. "Install now" checks that no synchronization is running
   first; "Remind me later" keeps the verified installer cached and the icon
   in its ready state, so the user can install from the popover later. */
function promptInstallUpdate() {
  if (state.update.installPrompted) return;
  state.update.installPrompted = true;
  showModal(
    t("update.readyTitle", "Update Ready"),
    '<p style="margin:0">' + esc(t("update.readyBody",
      "The update has been downloaded and its checksum verified. Would you like to install it now?")) + "</p>",
    [
      { label: t("update.remindLater", "Remind Me Later") },
      {
        label: t("update.installNow", "Install Now"), primary: true,
        onClick: () => attemptUpdateInstall()
      }
    ]
  );
}

async function attemptUpdateInstall() {
  if (state.update.popoverOpen) closeUpdatePopover();
  // An active email backup must never be interrupted: verify with the
  // backend that no synchronization is running before anything is closed.
  let syncRunning = false;
  try {
    const s = await api().getSyncProgress();
    syncRunning = !!(s && s.running);
  } catch (e) {
    syncRunning = false;
  }
  if (syncRunning) {
    showUpdateSyncBlocked();
    return;
  }
  runInstall();
}

function showUpdateSyncBlocked() {
  showModal(
    t("update.syncBlockedTitle", "Synchronization in progress"),
    '<p style="margin:0">' + esc(t("update.syncBlockedBody",
      "The update cannot be installed while an email synchronization is running, because MailDaan must close to apply it. Cancel the synchronization first, or install the update later.")) + "</p>",
    [
      { label: t("update.remindLater", "Remind Me Later"), primary: true },
      {
        label: t("update.cancelSyncAndInstall", "Cancel Sync & Install"), danger: true,
        onClick: () => cancelSyncThenInstall()
      }
    ]
  );
}

async function cancelSyncThenInstall() {
  await api().cancelSync();
  infoModal(t("update.syncBlockedTitle", "Synchronization in progress"),
    t("update.cancellingSync",
      "Cancelling the synchronization… The verified update will be installed as soon as it is safe."));
  const deadline = Date.now() + 90000;
  const settle = () => {
    // The engine flags the run as finished slightly before its thread
    // exits; give it a moment so the backend's install gate stays open.
    hideModal();
    runInstall();
  };
  const wait = async () => {
    try {
      const s = await api().getSyncProgress();
      if (s && s.running && Date.now() < deadline) {
        setTimeout(wait, 500);
        return;
      }
    } catch (e) { /* fall through and try once */ }
    if (Date.now() >= deadline) {
      hideModal();
      toast("<div>" + esc(t("update.cancelFailed",
        "The synchronization is still running. The update stays ready — install it after the synchronization finishes.")) + "</div>", "error");
      state.update.status = UPDATE.READY;
      refreshUpdateChrome();
      return;
    }
    setTimeout(settle, 700);
  };
  setTimeout(wait, 500);
}

async function runInstall() {
  if (state.update.popoverOpen) {
    const actions = $("updatePopover").querySelector(".upd-actions");
    if (actions) actions.innerHTML = '<span class="upd-spinner"></span><span>' +
      esc(t("update.installing", "Installing…")) + "</span>";
    placeUpdatePopover();
  }
  let res = null;
  try {
    res = await api().installUpdate();
  } catch (e) {
    res = { error: "install" };
  }
  // On success the application exits and the installer takes over.
  if (res && (res.error || res.error_key)) {
    // The backend refuses to interrupt an active synchronization — never
    // an automatic cancellation. Offer the same choice as before.
    if (res.error_key === "update.syncRunning") {
      showUpdateSyncBlocked();
      return;
    }
    state.update.status = UPDATE.ERROR;
    state.update.error = res.error_key
      ? t(res.error_key, res.error_key)
      : mapUpdateError(res.error);
    refreshUpdateChrome();
    if (state.update.popoverOpen) renderUpdatePopover();
    else toast("<div>" + esc(state.update.error) + "</div>", "error");
  }
}

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
    if (res && (res.error_key || res.error)) { reportError(res); return; }
    state.accounts = await api().getAccounts();
    renderAccounts();
    toast(esc(t("settings.saved", "Settings saved.")), "success");
  });

  $("btnCancelAccountSettings").addEventListener("click", () => goArchive());

  $("btnRemoveAccount").addEventListener("click", () => {
    const sel = $("settingsAccount");
    const account = state.accounts.find((a) => a.id === sel.value);
    if (!account) return;
    const name = account.display_name || account.email;
    const afterRemove = async (res, keepMessageKey) => {
      if (res && res.error_key) { reportError(res); return; }
      state.accounts = await api().getAccounts();
      state.activeAccountId = state.accounts.length ? state.accounts[0].id : null;
      resetSelection();
      renderAccounts();
      if (keepMessageKey) {
        toast(esc(t(keepMessageKey, "")).replace("{account}", "<b>" + esc(name) + "</b>"),
          "success");
      }
      if (!state.accounts.length) { openAccountForm("first"); return; }
      loadSettingsForms();
      goArchive();
    };
    showModal(
      t("accounts.removeTitle", "Remove Account"),
      '<p style="margin:0 0 12px">' +
        esc(t("accounts.removeChoose",
          "Choose what happens to the local archive of {account}:"))
          .replace("{account}", "<b>" + esc(name) + "</b>") + "</p>" +
      '<div class="remove-opt">' +
        '<span class="remove-opt-label">' + esc(t("accounts.remove", "Remove Account")) + "</span>" +
        '<span class="remove-opt-desc">' +
          esc(t("accounts.removeKeepDesc",
            "The account disappears from MailDaan, but its local archive (database, .eml files and attachments) stays on this computer. It is reattached automatically when you add this mailbox again.")) +
        "</span>" +
      "</div>" +
      '<div class="remove-opt danger">' +
        '<span class="remove-opt-label">' +
          esc(t("accounts.removeFull", "Remove Account and All Local Data")) + "</span>" +
        '<span class="remove-opt-desc">' +
          esc(t("accounts.removeFullDesc",
            "Additionally deletes this account’s local archive from this computer: SQLite database, .eml files, attachments and synchronization checkpoints. Nothing on the mail server is ever deleted.")) +
        "</span>" +
      "</div>",
      [
        { label: t("common.cancel", "Cancel"), primary: true },
        {
          label: t("accounts.remove", "Remove Account"), onClick: async () => {
            await afterRemove(await api().removeAccount(account.id, false),
              "accounts.removeKept");
          }
        },
        {
          label: t("accounts.removeFull", "Remove Account and All Local Data"), danger: true,
          keepOpen: true, onClick: () => {
            // Destructive action: a second confirmation names the account
            // and states plainly that local data cannot be recovered.
            showModal(
              t("accounts.removeFullTitle", "Delete Local Data"),
              esc(t("accounts.removeFullConfirm",
                  "Permanently delete the local archive of {account}? Its database, .eml files and attachments are removed from this computer and cannot be recovered without a separate backup. The messages on the mail server are not affected."))
                .replace("{account}", "<b>" + esc(name) + "</b>"),
              [
                { label: t("common.cancel", "Cancel"), primary: true },
                {
                  label: t("accounts.removeFullConfirmAction", "Delete Permanently"),
                  danger: true, onClick: async () => {
                    await afterRemove(await api().removeAccount(account.id, true),
                      "accounts.removedData");
                  }
                }
              ]);
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
      resetSelection();
      if (state.view === "archive") loadMessages();
    }, 350);
  });
  $("sortSelect").addEventListener("change", (e) => {
    state.sort = e.target.value;
    if (state.view === "archive") loadMessages();
  });
  $("btnSelectMessages").addEventListener("click", () => {
    state.selection.mode = !state.selection.mode;
    if (state.selection.mode && state.selection.scope !== selectionScopeKey()) {
      resetSelection();
      state.selection.mode = true;
    }
    if (state.selection.mode) {
      toast(esc(t("list.selectHint",
        "Click or Ctrl-click to select, Shift-click for a range. Select All covers the messages currently shown.")), "info");
    }
    loadMessages();
  });
  $("btnFilters").addEventListener("click", () => {
    $("filterPanel").classList.toggle("hidden");
  });
  $("btnApplyFilters").addEventListener("click", () => {
    state.filters.party = $("filterParty").value.trim();
    state.filters.folder_id = $("filterFolder").value ? Number($("filterFolder").value) : null;
    state.page = 1;
    resetSelection();
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
    resetSelection();
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
  $("btnExport").addEventListener("click", openReportDialog);
  $("btnTheme").addEventListener("click", toggleTheme);
  $("btnLang").addEventListener("click", toggleLanguage);
  $("btnUpdate").addEventListener("click", (e) => { e.stopPropagation(); toggleUpdatePopover(); });
  $("btnExit").addEventListener("click", requestExit);
  $("btnLangFa").addEventListener("click", () => chooseLanguage("fa"));
  $("btnLangEn").addEventListener("click", () => chooseLanguage("en"));
  $("devLink").addEventListener("click", (e) => {
    e.preventDefault();
    api().openExternal("https://github.com/TahaSarhangi");
  });
  $("lockForm").addEventListener("submit", submitUnlock);
  $("lockExit").addEventListener("click", () => api().exitApp());
  // Close the update popover when clicking elsewhere or pressing Escape.
  // The popover is anchored to the icon but is not a DOM child of the
  // update button wrapper, so both containers count as "inside".
  document.addEventListener("click", (e) => {
    if (state.update.popoverOpen && !e.target.closest(".update-wrap") &&
        !e.target.closest("#updatePopover")) closeUpdatePopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.update.popoverOpen) closeUpdatePopover();
  });
  // Keep the fixed popover inside the window when the window is resized.
  window.addEventListener("resize", () => {
    if (state.update.popoverOpen) placeUpdatePopover();
  });
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
  window.__maildaanVersion = initial.version || "";
  state.pendingUpdateVersion = initial.pending_update_version || null;
  applyTheme(state.theme);
  await loadLocale(state.lang);
  state.folderDisplay = (state.locale.folders && state.locale.folders.map) || {};
  applyTranslations();
  updateLangButton();
  updateThemeButton();
  renderAccounts();
  refreshUpdateChrome();

  if (initial.locked) {
    showLock();
    return;
  }
  finishBoot();
}

/* Verify the outcome of an applied update before reporting anything. The
   previous session recorded the version it handed to the installer — a
   persistent record, so it survives the restart — and success is announced
   only when this build's version matches it exactly. A cancelled or failed
   install, or a relaunch into a different version, stays silent: this must
   never claim success falsely. The record is cleared either way, so the
   notice can never be shown more than once. */
function checkPendingUpdate(pendingVersion) {
  if (!pendingVersion) return;
  state.pendingUpdateVersion = null;
  const expected = String(pendingVersion).replace(/^v/i, "");
  const installed = appVersionDisplay();
  try { api().clearPendingUpdate(); } catch (e) { /* cleared server-side anyway */ }
  if (installed && installed === expected) {
    infoModal(
      t("update.successTitle", "Update Complete"),
      t("update.successBody", "MailDaan was successfully updated to version {version}.")
        .replace("{version}", toDigits(expected)));
  }
}

function finishBoot() {
  /* Route into the application. Called from bootstrap after authentication
     (or directly when no application password is enabled). */
  // Report the result of an update the previous session handed to the
  // installer — but only after verifying this build really is that version.
  checkPendingUpdate(state.pendingUpdateVersion);
  // Quietly look for a newer release in the background; a hit only lights
  // the small badge on the sidebar update icon.
  setTimeout(checkForUpdatesAuto, 2000);
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
      // A failed synchronization is reported on the sync screen; users who
      // navigated elsewhere still get a clear, dismissible toast.
      if (report && report.status === "error" && state.view !== "sync") {
        const msg = report.error_key
          ? t(report.error_key, report.error_key)
          : (report.last_error || t("sync.failed", "Synchronization failed."));
        toast("<div>" + esc(msg) + "</div>", "error");
      }
    } catch (e) { /* ignore */ }
  },
};
