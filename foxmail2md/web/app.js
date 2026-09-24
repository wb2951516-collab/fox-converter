/* ═══════════════════════════════════════════════════════════
   Fox Converter 前端逻辑（多语言版）
   ═══════════════════════════════════════════════════════════ */
'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let LANG = 'zh-CN';

function t(key, vars) {
  const dict = window.I18N[LANG] || window.I18N['zh-CN'];
  let s = dict[key] ?? window.I18N['zh-CN'][key] ?? key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
}

function applyLang(lang) {
  LANG = window.I18N[lang] ? lang : 'zh-CN';
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-ph]').forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  document.title = LANG === 'en' ? 'Fox Converter' : 'Fox Converter — 邮件存档转换阅读器';
}

const state = {
  source: '',
  archiveId: 0,
  page: 1,
  perPage: 50,
  search: '',
  order: 'date_desc',
  group: '',
  total: 0,
  selectedId: null,
  currentMail: null,
  currentView: 'html',
  expandedGroups: new Set(),
  confirmDeleteId: null,
  exportConfigured: null,
};

/* ── 工具 ─────────────────────────────────────────────── */
function fmtSize(n) {
  if (n == null) return '';
  for (const u of ['B', 'KB', 'MB', 'GB']) {
    if (n < 1024) return `${n.toFixed(n < 10 && u !== 'B' ? 1 : 0)}\u00A0${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)}\u00A0TB`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso.slice(0, 10);
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const dateStr = sameYear
    ? `${d.getMonth() + 1}/${d.getDate()}`
    : `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${dateStr} ${hh}:${mm}`;
}

function fmtDateFull(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function dateRange(min, max) {
  if (!min) return '';
  if (min.slice(0, 4) === max.slice(0, 4)) return `${min.slice(5, 10)} ~ ${max.slice(5, 10)}`;
  return `${min.slice(0, 10)} ~ ${max.slice(0, 10)}`;
}

function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  $('#toastContainer').appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .2s ease';
    setTimeout(() => el.remove(), 220);
  }, 2600);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function applyTheme(theme, accent) {
  document.documentElement.dataset.theme = theme;
  if (accent) {
    document.documentElement.style.setProperty('--primary', accent);
    document.documentElement.style.setProperty('--primary-hover', accent);
    document.documentElement.style.setProperty('--primary-soft', `color-mix(in srgb, ${accent} 10%, transparent)`);
    document.documentElement.style.setProperty('--selected', `color-mix(in srgb, ${accent} 7%, transparent)`);
  }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function normSubject(s) {
  let out = String(s || '').trim();
  for (;;) {
    const next = out
      .replace(/^\s*((re|fw|fwd|aw|sv)\s*[:：]\s*)/i, '')
      .replace(/^\s*(回复|转发|答复|转送)\s*[:：]\s*/, '')
      .replace(/^\s*【[^】]{1,12}】\s*/, '');
    if (next === out) break;
    out = next;
  }
  return out.trim() || '(no subject)';
}

function addrKey(s) {
  const m = String(s || '').match(/<([^>]+)>/);
  return (m ? m[1] : String(s || '')).trim() || '(empty)';
}

/* ── 邮件列表 ─────────────────────────────────────────── */
let loadSeq = 0;          // 请求序号：过期响应不渲染（防慢响应覆盖新结果）
let loadAbort = null;     // 连打搜索时中止在途请求
const groupCache = new Map();  // 分组 key → 成员列表（作用域变化时清空）

function clearGroupCache() { groupCache.clear(); }

function mailItemHtml(m) {
  const attSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="12" height="12"><path d="M21 12.5l-8.5 8.5a5 5 0 01-7-7L14 5.5a3.5 3.5 0 015 5L10.5 19a2 2 0 01-3-3l6.5-6.5"/></svg>';
  return `
    <button class="mail-item ${m.id === state.selectedId ? 'selected' : ''}" data-id="${m.id}" aria-label="${esc(m.subject)}">
      <div class="mail-item-top">
        <span class="mail-from">${esc(m.from)}</span>
        <span class="mail-date">${fmtDate(m.date)}</span>
      </div>
      <div class="mail-subject">${esc(m.subject)}</div>
      ${m.has_attachments ? `<div class="mail-att-badge">${attSvg}${m.attachment_count}</div>` : ''}
    </button>`;
}

async function loadMails() {
  const seq = ++loadSeq;
  if (loadAbort) loadAbort.abort();
  const ac = new AbortController();
  loadAbort = ac;
  const params = new URLSearchParams({ search: state.search, order: state.order });
  if (state.archiveId) params.set('archive_id', state.archiveId);
  let data;
  try {
    if (state.group) {
      params.set('group', state.group);
      data = await api(`/api/mails?${params}`, { signal: ac.signal });
    } else {
      params.set('page', state.page);
      params.set('per_page', state.perPage);
      data = await api(`/api/mails?${params}`, { signal: ac.signal });
    }
  } catch (e) {
    if (ac.signal.aborted) return;  // 已被更新的请求取代
    throw e;
  }
  if (seq !== loadSeq) return;
  state.total = data.total;
  if (state.group) {
    renderGroupHeaders(data.groups || []);
    $('#listFooter').innerHTML =
      `<span class="num">${t('list.groups', { n: (data.groups || []).length, m: data.total })}</span>`;
  } else {
    renderMailList(data.items);
    renderPagination();
  }
}

function groupsLabel() {
  return { subject: t('grouped.subject'), from: t('grouped.from'), to: t('grouped.to') }[state.group] || '';
}

function displayGroupKey(key) {
  if (key) return key;
  return state.group === 'subject' ? normSubject('') : addrKey('');
}

function renderGroupHeaders(groups) {
  const box = $('#mailList');
  if (!groups.length) {
    box.innerHTML = `<div class="empty-state"><p>${state.search ? t('empty.nomatch') : t('empty.list')}</p></div>`;
    return;
  }
  box.innerHTML = groups.map((g, gi) => {
    const open = state.expandedGroups.has(g.key) || gi === 0;
    return `
      <div class="group-block" data-key="${esc(g.key)}">
        <button class="group-header" aria-expanded="${open}">
          <svg class="icon chev ${open ? 'open' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M9 18l6-6-6-6"/></svg>
          <span class="group-name" title="${esc(displayGroupKey(g.key))}">${esc(displayGroupKey(g.key))}</span>
          <span class="group-count num">${g.count}</span>
        </button>
        <div class="group-body" style="display:${open ? 'block' : 'none'}"></div>
      </div>`;
  }).join('');
  box.querySelectorAll('.group-block').forEach((block, gi) => {
    const header = block.querySelector('.group-header');
    header.addEventListener('click', () => toggleGroupBlock(block));
    if (gi === 0) loadGroupMembers(block);  // 首组自动展开
  });
}

async function toggleGroupBlock(block) {
  const header = block.querySelector('.group-header');
  const grpBody = block.querySelector('.group-body');
  const key = block.dataset.key;
  const opening = grpBody.style.display === 'none';
  grpBody.style.display = opening ? 'block' : 'none';
  header.setAttribute('aria-expanded', String(opening));
  header.querySelector('.chev')?.classList.toggle('open', opening);
  if (opening) {
    state.expandedGroups.add(key);
    await loadGroupMembers(block);
  } else {
    state.expandedGroups.delete(key);
  }
}

async function loadGroupMembers(block) {
  const grpBody = block.querySelector('.group-body');
  if (block.dataset.loaded === '1') return;  // 本次视图内已加载
  const key = block.dataset.key;
  if (groupCache.has(key)) {
    fillGroupBody(grpBody, groupCache.get(key));
    block.dataset.loaded = '1';
    return;
  }
  const params = new URLSearchParams({
    group: state.group, key, search: state.search,
    order: state.order, per_page: 2000,
  });
  if (state.archiveId) params.set('archive_id', state.archiveId);
  try {
    const data = await api(`/api/mails/group_members?${params}`);
    groupCache.set(key, data.items);
    fillGroupBody(grpBody, data.items);
    block.dataset.loaded = '1';
  } catch (e) {
    grpBody.innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
  }
}

function fillGroupBody(grpBody, items) {
  if (!items.length) {
    grpBody.innerHTML = `<div class="empty-state"><p>${t('empty.nomatch')}</p></div>`;
    return;
  }
  grpBody.innerHTML = items.map(mailItemHtml).join('');
  grpBody.querySelectorAll('.mail-item').forEach((el) => {
    el.addEventListener('click', () => selectMail(+el.dataset.id));
  });
}

function renderMailList(items) {
  const box = $('#mailList');
  if (!items.length) {
    box.innerHTML = `<div class="empty-state"><p>${state.search ? t('empty.nomatch') : t('empty.list')}</p></div>`;
    return;
  }
  box.innerHTML = items.map(mailItemHtml).join('');
  box.querySelectorAll('.mail-item').forEach((el) => {
    el.addEventListener('click', () => selectMail(+el.dataset.id));
  });
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.perPage));
  const footer = $('#listFooter');
  if (totalPages <= 1) {
    footer.innerHTML = `<span class="num">${t('list.count', { n: state.total })}</span>`;
    return;
  }
  const cur = state.page;
  const btn = (p, label = p, disabled = false, active = false) =>
    `<button class="page-btn ${active ? 'active' : ''}" ${disabled ? 'disabled' : ''} data-page="${p}">${label}</button>`;
  let html = btn(cur - 1, '‹', cur <= 1);
  const pages = [];
  for (let p = 1; p <= totalPages; p++) {
    if (p === 1 || p === totalPages || Math.abs(p - cur) <= 1) pages.push(p);
    else if (pages[pages.length - 1] !== '…') pages.push('…');
  }
  for (const p of pages) html += p === '…' ? '<span>…</span>' : btn(p, p, false, p === cur);
  html += btn(cur + 1, '›', cur >= totalPages);
  footer.innerHTML = html;
  footer.querySelectorAll('.page-btn').forEach((el) => {
    el.addEventListener('click', () => {
      const p = +el.dataset.page;
      if (p >= 1 && p <= totalPages && p !== state.page) {
        state.page = p;
        loadMails();
      }
    });
  });
}

async function renderStats() {
  const params = state.archiveId ? `?archive_id=${state.archiveId}` : '';
  const s = await api(`/api/stats${params}`);
  $('#sidebarStats').innerHTML = s.total ? `
    <div class="stat-row"><span class="stat-label">${t('st.mail')}</span><span class="stat-value num">${s.total}</span></div>
    <div class="stat-row"><span class="stat-label">${t('st.att')}</span><span class="stat-value num">${s.attachment_count}</span></div>
    <div class="stat-row"><span class="stat-label">${t('st.size')}</span><span class="stat-value num">${fmtSize(s.total_size)}</span></div>
    ${s.min_date ? `<div class="stat-row"><span class="stat-label">${t('st.range')}</span><span class="stat-value num">${dateRange(s.min_date, s.max_date)}</span></div>` : ''}
  ` : '';
}

/* ── 存档列表（侧栏） ─────────────────────────────────── */
async function loadArchives() {
  const data = await api('/api/archives');
  const box = $('#archiveList');
  const attSvg = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/></svg>';
  const delSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/></svg>';
  const homeSvg = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M3 12l9-9 9 9M5 10v10a1 1 0 001 1h3m10-11v10a1 1 0 01-1 1h-3m-6 0h6"/></svg>';
  const item = (a) => `
    <button class="archive-item ${a.id === state.archiveId ? 'selected' : ''}" data-id="${a.id}" title="${esc(a.name)} (${fmtSize(a.total_size)})">
      ${attSvg}<span class="archive-name">${esc(a.name)}</span><span class="archive-count num">${a.email_count}</span>
      <span class="archive-del" data-del="${a.id}" role="button" aria-label="delete ${esc(a.name)}" title="delete">${delSvg}</span>
    </button>`;
  box.innerHTML =
    `<button class="archive-item ${state.archiveId === 0 ? 'selected' : ''}" data-id="0">${homeSvg}<span class="archive-name">${t('side.all')}</span></button>` +
    data.archives.map(item).join('');
  box.querySelectorAll('.archive-item').forEach((el) => {
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('.archive-del')) return;
      state.archiveId = +el.dataset.id;
      state.page = 1;
      state.search = '';
      $('#searchInput').value = '';
      clearGroupCache();
      loadMails();
      loadArchives();
      renderStats();
    });
  });
  box.querySelectorAll('.archive-del').forEach((el) => {
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      requestDeleteArchive(+el.dataset.del);
    });
  });
}

async function requestDeleteArchive(id) {
  if (state.confirmDeleteId === id) {
    state.confirmDeleteId = null;
    toast(t('cleanup.deleting'));
    await api(`/api/archives/${id}`, { method: 'DELETE' });
    toast(t('cleanup.deleted.arc'));
    if (state.archiveId === id) state.archiveId = 0;
    clearGroupCache();
    loadArchives();
    loadMails();
    renderStats();
  } else {
    state.confirmDeleteId = id;
    const btn = document.querySelector(`.archive-del[data-del="${id}"]`);
    if (btn) {
      btn.classList.add('confirm');
      btn.innerHTML = '<span style="font-size:.6875rem;white-space:nowrap">✓</span>';
      setTimeout(() => {
        if (state.confirmDeleteId === id) { state.confirmDeleteId = null; loadArchives(); }
      }, 3000);
    }
  }
}

/* ── 阅读区 ───────────────────────────────────────────── */
async function selectMail(id) {
  state.selectedId = id;
  $$('.mail-item').forEach((el) => el.classList.toggle('selected', +el.dataset.id === id));
  const mail = await api(`/api/mails/${id}`);
  state.currentMail = mail;
  state.currentView = 'html';
  $('#readerEmpty').style.display = 'none';
  $('#readerActive').style.display = 'flex';
  $('#readerPanel').classList.add('mobile-visible');
  $('#listPanel').classList.remove('mobile-visible');
  $('#readerMeta').textContent = `${mail.from} · ${fmtDateFull(mail.date)}`;
  const attTab = document.querySelector('.tab[data-view="atts"]');
  attTab.style.display = mail.attachment_names.length ? '' : 'none';
  renderBody();
}

function renderBody() {
  const mail = state.currentMail;
  if (!mail) return;
  const body = $('#readerBody');
  const view = state.currentView;
  $$('.tab').forEach((tb) => tb.classList.toggle('active', tb.dataset.view === view));

  if (view === 'html') {
    body.innerHTML = `
      <div class="mail-header">
        <h1 class="mail-title">${esc(mail.subject)}</h1>
        <div class="mail-meta-grid">
          <span class="mail-meta-label">${t('reader.from')}</span><span class="mail-meta-value">${esc(mail.from)}</span>
          <span class="mail-meta-label">${t('reader.to')}</span><span class="mail-meta-value">${esc(mail.to)}</span>
          ${mail.cc ? `<span class="mail-meta-label">${t('reader.cc')}</span><span class="mail-meta-value">${esc(mail.cc)}</span>` : ''}
          <span class="mail-meta-label">${t('reader.date')}</span><span class="mail-meta-value">${fmtDateFull(mail.date)}</span>
        </div>
      </div>
      <div class="reader-content html-view">${sanitizeHtml(mail.body_html, mail.id) || t('reader.emptyBody')}</div>`;
  } else if (view === 'md') {
    body.innerHTML = `<pre class="md-view">${esc(mail.body_md || '')}</pre>`;
  } else if (view === 'text') {
    body.innerHTML = `<div class="reader-content plain-view">${esc(mail.body_text || '')}</div>`;
  } else if (view === 'atts') {
    if (!mail.attachment_names.length) { state.currentView = 'html'; return renderBody(); }
    body.innerHTML = renderAttachmentsView(mail);
    body.querySelectorAll('[data-att-index]').forEach((el) => {
      el.addEventListener('click', () => triggerDownload(mail, +el.dataset.attIndex));
    });
    const dlAll = body.querySelector('#dlAllBtn');
    if (dlAll) dlAll.addEventListener('click', () => triggerDownloadAll(mail));
  }
  body.scrollTop = 0;
}

function renderAttachmentsView(mail) {
  const rows = mail.attachment_names.map((name, i) => {
    const rel = mail.attachment_files[i] || '';
    const ext = String(name).split('.').pop().toUpperCase().slice(0, 5);
    return `
      <div class="att-row">
        <svg class="att-row-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="20" height="20"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>
        <div class="att-row-main">
          <div class="att-row-name" title="${esc(name)}">${esc(name)}</div>
          <div class="att-row-sub">${esc(ext)} · ${esc(rel)}</div>
        </div>
        <button class="btn btn-sm" data-att-index="${i}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
          ${t('atts.download')}
        </button>
      </div>`;
  }).join('');
  return `
    <div class="reader-content atts-view">
      <div class="atts-title">${t('atts.count', { n: mail.attachment_names.length })}</div>
      <div class="atts-list">${rows}</div>
      <div style="margin-top:12px">
        <button class="btn btn-sm" id="dlAllBtn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
          ${t('atts.downloadAll')}
        </button>
      </div>
    </div>`;
}

function triggerDownload(mail, i) {
  const rel = mail.attachment_files[i];
  if (!rel) return;
  const a = document.createElement('a');
  a.href = `/api/attachment/${mail.id}/${rel}`;
  a.download = mail.attachment_names[i] || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast(t('atts.dlStart', { name: mail.attachment_names[i] }));
}

function triggerDownloadAll(mail) {
  const a = document.createElement('a');
  a.href = `/api/mails/${mail.id}/attachments/zip`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast(t('atts.dlAllStart'));
}

/* 防 XSS + 内嵌图片改为 API 路径 */
function sanitizeHtml(html, mailId) {
  if (!html) return '';
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const allowed = new Set(['P','BR','DIV','SPAN','B','STRONG','I','EM','U','S','H1','H2','H3','H4','H5','H6','UL','OL','LI','TABLE','THEAD','TBODY','TR','TD','TH','A','IMG','BLOCKQUOTE','PRE','CODE','FONT','HR','CENTER','SUB','SUP']);
  const dropWithContent = new Set(['STYLE','SCRIPT','HEAD','META','LINK','TITLE','OBJECT','EMBED','IFRAME','FORM','INPUT','BUTTON','SELECT','TEXTAREA','SVG','MATH']);
  const walk = (node) => {
    for (const child of [...node.children]) {
      const tag = child.tagName.toUpperCase();
      if (dropWithContent.has(tag)) { child.remove(); continue; }
      if (!allowed.has(tag)) { child.replaceWith(...child.childNodes); walk(node); continue; }
      for (const attr of [...child.attributes]) {
        const n = attr.name.toLowerCase();
        if (n === 'src' && tag === 'IMG') {
          if (attr.value.startsWith('attachments/')) {
            child.setAttribute('src', `/api/attachment/${mailId}/${attr.value}`);
          } else {
            child.removeAttribute(n);
          }
        } else if (n === 'href' && tag === 'A') {
          if (/^(https?:|mailto:|#)/i.test(attr.value) === false) child.removeAttribute(n);
        } else if (n !== 'style' && n !== 'colspan' && n !== 'rowspan') {
          child.removeAttribute(n);
        }
      }
      if (tag === 'A') { child.target = '_blank'; child.rel = 'noopener noreferrer'; }
      walk(child);
    }
  };
  walk(tpl.content);
  return tpl.innerHTML;
}

/* ── 解析流程（批量） ─────────────────────────────────── */
let pickedPaths = [];

function openBrowse() {
  if (state.exportConfigured === false) {
    $('#setupNotice').style.display = 'block';
    $('#parseStepSelect').style.display = 'block';
    $('#parseStepProgress').style.display = 'none';
    $('#parseStepDone').style.display = 'none';
    $('#parseFileList').style.display = 'none';
    $('#diskLine').style.display = 'none';
    $('#startParseBtn').style.display = 'none';
    $('#manualPath').value = '';
    $('#browseBtn').disabled = true;
    $('#browseFolderBtn').disabled = true;
    $('#useManualPathBtn').disabled = true;
    $('#parseModal').style.display = 'flex';
    return;
  }
  $('#setupNotice').style.display = 'none';
  $('#browseBtn').disabled = false;
  $('#browseFolderBtn').disabled = false;
  $('#useManualPathBtn').disabled = false;
  $('#parseStepSelect').style.display = 'block';
  $('#parseStepProgress').style.display = 'none';
  $('#parseStepDone').style.display = 'none';
  $('#parseFileList').style.display = 'none';
  $('#parseFileList').innerHTML = '';
  $('#diskLine').style.display = 'none';
  $('#startParseBtn').style.display = 'none';
  $('#manualPath').value = '';
  $('#parseModal').style.display = 'flex';
}

async function pickPaths(paths) {
  const newOnes = paths.filter((p) => p && !pickedPaths.some((v) => v.path === p));
  if (!newOnes.length) return;
  const btns = [$('#browseBtn'), $('#browseFolderBtn'), $('#useManualPathBtn')];
  btns.forEach((b) => { b.disabled = true; });
  const verified = [...pickedPaths];
  const checking = newOnes.map((p) => ({ path: p, done: false, error: '' }));
  const box = $('#parseFileList');
  box.style.display = 'block';
  const render = () => {
    box.innerHTML =
      verified.map((v) => `
        <div class="picked-row">
          <span class="picked-name" title="${esc(v.path)}">${esc(v.path.split(/[\\/]/).pop())}</span>
          <span class="picked-meta num">${fmtSize(v.size)} · ${v.total != null ? v.total : ''}</span>
        </div>`).join('') +
      checking.filter((c) => !c.done).map((c) => `
        <div class="picked-row checking">
          <span class="picked-name">${esc(c.path.split(/[\\/]/).pop())}</span>
          <span class="picked-meta">${t('import.checking')}</span>
        </div>`).join('') +
      checking.filter((c) => c.done && c.error).map((c) => `
        <div class="picked-row checking">
          <span class="picked-name" style="color:var(--danger)">${esc(c.path.split(/[\\/]/).pop())}</span>
          <span class="picked-meta">${esc(c.error)}</span>
        </div>`).join('');
  };
  render();
  for (const p of newOnes) {
    try {
      const res = await api(`/api/peek?path=${encodeURIComponent(p)}`);
      verified.push({ path: res.path, size: res.size, total: res.total });
    } catch (e) {
      const c = checking.find((x) => x.path === p);
      if (c) { c.done = true; c.error = e.message; }
    }
    const c = checking.find((x) => x.path === p);
    if (c) c.done = true;
    render();
  }
  btns.forEach((b) => { b.disabled = false; });
  pickedPaths = verified.map((v) => v.path);
  let disk = null;
  try {
    disk = await api('/api/diskcheck', { method: 'POST', body: JSON.stringify({ paths: pickedPaths }) });
  } catch {}
  const line = $('#diskLine');
  if (disk) {
    line.style.display = 'block';
    const ok = disk.free >= disk.need;
    line.innerHTML = `${t('import.disk.pos')} <span title="${esc(disk.export_root)}">${esc(disk.export_root)}</span>` +
      ` · ${t('import.disk.need')} ${fmtSize(disk.need)} · ${t('import.disk.free')} ${fmtSize(disk.free)}` +
      (ok ? ` <span style="color:var(--success)">${t('import.disk.ok')}</span>`
          : ` <span style="color:var(--danger)">${t('import.disk.lack')}</span>`);
    $('#startParseBtn').disabled = !ok;
  } else {
    line.style.display = 'none';
  }
  if (pickedPaths.length) $('#startParseBtn').style.display = 'inline-flex';
}

async function browseFile() {
  try {
    const res = await api('/api/browse', { method: 'POST' });
    if (res.skipped) toast(t('import.nonFox', { n: res.skipped }));
    await pickPaths(res.paths || []);
  } catch (e) {
    toast(t('import.pickFail', { msg: e.message }));
  }
}

async function browseFolder() {
  try {
    const res = await api('/api/browse_folder', { method: 'POST' });
    if (!(res.paths || []).length) { toast(t('import.folderEmpty')); return; }
    await pickPaths(res.paths);
  } catch (e) {
    toast(t('import.folderFail', { msg: e.message }));
  }
}

async function useManualPath() {
  const raw = $('#manualPath').value.trim();
  if (!raw) { toast(t('import.manualEmpty')); return; }
  const paths = raw.split(/[\n;；]/).map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean);
  await pickPaths(paths);
}

async function startParse() {
  if (!pickedPaths.length) return;
  $('#parseStepSelect').style.display = 'none';
  $('#parseStepProgress').style.display = 'block';
  try {
    const res = await api('/api/parse', { method: 'POST', body: JSON.stringify({ paths: pickedPaths }) });
    listenProgress(res.task_id);
  } catch (e) {
    toast(t('import.startFail', { msg: e.message }));
    openBrowse();
  }
}

function listenProgress(taskId) {
  const es = new EventSource(`/api/parse/status/${taskId}`);
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    const filePct = d.total ? (d.current / d.total) * 100 : 0;
    const overall = d.file_total ? ((d.file_index - 1 + filePct / 100) / d.file_total) * 100 : 0;
    $('#progressFill').style.width = `${Math.min(100, overall).toFixed(1)}%`;
    if (d.status === 'done') {
      $('#progressText').textContent = t('import.done');
      $('#progressFile').textContent = t('list.count', { n: d.file_total });
      $('#progressSub').textContent = t('import.elapsed', { s: d.elapsed.toFixed(1) });
    } else {
      $('#progressFile').textContent = t('import.file.n', { i: d.file_index, n: d.file_total, name: d.filename || '' });
      $('#progressText').textContent = t('import.progress', { cur: d.current, total: d.total || '…', pct: filePct.toFixed(0) });
      $('#progressSub').textContent = d.subject || '';
    }
    $('#doneFiles').textContent = (d.done_files || []).join(' · ');
    if (d.status === 'done' || d.status === 'cancelled') {
      es.close();
      $('#parseStepProgress').style.display = 'none';
      $('#parseStepDone').style.display = 'block';
      $('#doneText').innerHTML = t('import.doneLine', { n: d.imported }) +
        (d.skipped ? t('import.skippedLine', { n: d.skipped }) : '') +
        (d.errors ? t('import.errSuffix', { n: d.errors }) : '') +
        `<br><span style="font-size:.8125rem;color:var(--text-3)">${t('import.elapsed', { s: d.elapsed.toFixed(1) })}</span>`;
      state.page = 1;
      state.search = '';
      $('#searchInput').value = '';
      clearGroupCache();
      loadArchives();
      loadMails();
      renderStats();
    } else if (d.status === 'error') {
      es.close();
      toast(t('import.parseFail', { msg: d.error }));
      openBrowse();
    }
  };
  es.onerror = () => es.close();
}

/* ── 事件绑定 ─────────────────────────────────────────── */
function bindEvents() {
  $$('.nav-item[data-route]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('.nav-item[data-route]').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      $('#settingsPanel').style.display = 'none';
      $('#agentPanel').style.display = 'none';
      $('#cleanupPanel').style.display = 'none';
      $('#guidePanel').style.display = 'none';
      const route = btn.dataset.route;
      if (route === 'settings') { $('#settingsPanel').style.display = 'flex'; loadSettings(); }
      else if (route === 'agent') { $('#agentPanel').style.display = 'flex'; loadAgentKey(); }
      else if (route === 'cleanup') { $('#cleanupPanel').style.display = 'flex'; }
      else if (route === 'guide') { $('#guidePanel').style.display = 'flex'; }
    });
  });

  $('#openParseBtn').addEventListener('click', openBrowse);
  $('#browseBtn').addEventListener('click', browseFile);
  $('#browseFolderBtn').addEventListener('click', browseFolder);
  $('#useManualPathBtn').addEventListener('click', useManualPath);
  $('#startParseBtn').addEventListener('click', startParse);
  $('#gotoSetupBtn').addEventListener('click', () => {
    $('#parseModal').style.display = 'none';
    $$('.nav-item[data-route]').forEach((b) => b.classList.remove('active'));
    document.querySelector('.nav-item[data-route="settings"]')?.classList.add('active');
    $('#settingsPanel').style.display = 'flex';
    loadSettings();
  });
  $('#parseCloseBtn').addEventListener('click', () => { $('#parseModal').style.display = 'none'; });
  $('#doneCloseBtn').addEventListener('click', () => { $('#parseModal').style.display = 'none'; });
  $('#settingsCloseBtn').addEventListener('click', () => { $('#settingsPanel').style.display = 'none'; resetRoute(); });
  $('#agentCloseBtn').addEventListener('click', () => { $('#agentPanel').style.display = 'none'; resetRoute(); });
  $('#cleanupCloseBtn').addEventListener('click', () => { $('#cleanupPanel').style.display = 'none'; resetRoute(); });
  $('#guideCloseBtn').addEventListener('click', () => { $('#guidePanel').style.display = 'none'; resetRoute(); });

  const doSearch = debounce(() => {
    state.search = $('#searchInput').value.trim();
    state.page = 1;
    clearGroupCache();
    loadMails();
  }, 350);
  $('#searchInput').addEventListener('input', doSearch);
  $('#sortSelect').addEventListener('change', (e) => {
    state.order = e.target.value;
    state.page = 1;
    clearGroupCache();
    loadMails();
  });
  $('#groupSelect').addEventListener('change', (e) => {
    state.group = e.target.value;
    state.page = 1;
    clearGroupCache();
    loadMails();
  });

  $$('.tab').forEach((tb) => tb.addEventListener('click', () => {
    if (tb.style.display === 'none') return;
    state.currentView = tb.dataset.view;
    renderBody();
  }));

  $('#copyBtn').addEventListener('click', async () => {
    const mail = state.currentMail;
    if (!mail) return;
    let text = '';
    if (state.currentView === 'md') text = mail.body_md;
    else if (state.currentView === 'text') text = mail.body_text;
    else if (state.currentView === 'atts') text = mail.attachment_names.join('\n');
    else text = mail.body_html || mail.body_text;
    try { await navigator.clipboard.writeText(text); toast(t('copy.ok')); }
    catch { toast(t('copy.fail')); }
  });

  $('#settingTheme').addEventListener('change', saveSettings);
  $('#settingAccent').addEventListener('change', saveSettings);
  $('#settingBrowser').addEventListener('change', saveSettings);
  $('#settingLang').addEventListener('change', async (e) => {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({ lang: e.target.value }) });
    location.reload();
  });

  $('#browseDataDirBtn').addEventListener('click', async () => {
    const r = await api('/api/browse_dir', { method: 'POST' });
    if (r.path) $('#settingDataDir').value = r.path;
  });
  $('#browseExportDirBtn').addEventListener('click', async () => {
    const r = await api('/api/browse_dir', { method: 'POST' });
    if (r.path) $('#settingDir').value = r.path;
  });
  $('#migrateBtn').addEventListener('click', migrateStorage);

  $('#cleanupFindBtn').addEventListener('click', findCleanup);
  $('#cleanupResetBtn').addEventListener('click', () => {
    ['cfSender', 'cfSubject', 'cfDateFrom', 'cfDateTo', 'cfMinMb', 'cfMaxMb'].forEach((id) => { $('#' + id).value = ''; });
  });
  $('#cleanupDelBtn').addEventListener('click', deleteCleanup);

  $('#copyPromptBtn').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#agentPrompt').textContent); toast(t('agent.copy.ok')); }
    catch { toast(t('copy.fail')); }
  });
  $('#copyKeyBtn').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#agentKey').textContent); toast(t('copy.ok')); }
    catch { toast(t('copy.fail')); }
  });
  $('#regenKeyBtn').addEventListener('click', async () => {
    if (!confirm(t('agent.regen.confirm'))) return;
    const r = await api('/api/agent-key/regenerate', { method: 'POST' });
    $('#agentKey').textContent = r.api_key;
    toast(t('agent.regen.ok'));
  });
}

function resetRoute() {
  $$('.nav-item[data-route]').forEach((b) => b.classList.toggle('active', b.dataset.route === 'mails'));
}

async function loadSettings() {
  const cfg = await api('/api/settings');
  applyTheme(cfg.theme, cfg.accent_color);
  $('#settingTheme').value = cfg.theme;
  $('#settingAccent').value = cfg.accent_color;
  $('#settingBrowser').value = cfg.browser;
  $('#settingLang').value = cfg.lang || 'zh-CN';
  try {
    const p = await api('/api/paths');
    $('#settingDataDir').value = p.data_dir;
    $('#settingDir').value = p.export_dir;
    $('#dataSizeLabel').textContent = `(${fmtSize(p.data_size)})`;
    $('#exportSizeLabel').textContent = `(${fmtSize(p.export_size)})`;
    state.exportConfigured = !!p.export_configured;
    $('#migrateNote').textContent = p.export_configured ? t('set.migrate.note.ok') : t('set.migrate.note.cfg');
  } catch {}
}

async function saveSettings() {
  await api('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({
      theme: $('#settingTheme').value,
      accent_color: $('#settingAccent').value,
      browser: $('#settingBrowser').value,
    }),
  });
  toast(t('set.saved'));
}

async function migrateStorage() {
  const dataDir = $('#settingDataDir').value.trim();
  const exportDir = $('#settingDir').value.trim();
  const cur = await api('/api/paths');
  const body = {};
  if (exportDir && exportDir !== cur.export_dir) body.export_dir = exportDir;
  if (dataDir && dataDir !== cur.data_dir) body.data_dir = dataDir;
  if (!Object.keys(body).length) { toast(t('set.unchanged')); return; }

  const bar = $('#migrateProgressBar');
  const fill = $('#migrateProgressFill');
  $('#migrateBtn').disabled = true;
  try {
    const r = await api('/api/migrate', { method: 'POST', body: JSON.stringify(body) });
    let needWait = false;
    if (r.data_dir) {
      if (r.data_dir.status === 'switched') {
        toast(t('set.data.switched'));
        $('#migrateNote').textContent = t('set.data.switched');
      } else {
        $('#migrateNote').textContent = t('set.data.copied');
        needWait = true;
      }
    }
    if (r.export_dir && r.export_dir.task_id) {
      needWait = true;
      bar.style.display = 'block';
      const tid = r.export_dir.task_id;
      const timer = setInterval(async () => {
        const ms = await api(`/api/migrate/status/${tid}`);
        const pct = ms.total ? Math.round((ms.copied / ms.total) * 100) : 0;
        fill.style.width = `${pct}%`;
        if (ms.status === 'done') {
          clearInterval(timer);
          bar.style.display = 'none';
          $('#migrateBtn').disabled = false;
          toast(t('set.exp.moved', { n: ms.total }));
          loadSettings();
        } else if (ms.status === 'error') {
          clearInterval(timer);
          bar.style.display = 'none';
          $('#migrateBtn').disabled = false;
          toast(t('set.migrate.fail', { msg: ms.error }));
        }
      }, 800);
    } else if (!r.data_dir) {
      $('#migrateBtn').disabled = false;
    }
    if (r.export_dir && r.export_dir.status === 'switched') {
      toast(t('set.exp.switched'));
      $('#migrateBtn').disabled = false;
      await refreshExportConfigured();
    }
  } catch (e) {
    toast(t('set.migrate.fail', { msg: e.message }));
    $('#migrateBtn').disabled = false;
  }
}

/* ── Agent 接入 ───────────────────────────────────────── */
async function loadAgentKey() {
  const k = await api('/api/agent-key');
  const base = `http://127.0.0.1:${k.port}`;
  $('#agentKey').textContent = k.api_key;
  $('#agentStats').textContent = t('agent.stats', { n: k.emails, a: k.archives, base });
  $('#agentPrompt').textContent = t('agent.prompt', { base, key: k.api_key });
}

/* ── 查找清理 ─────────────────────────────────────────── */
let cleanupItems = [];
let cleanupSel = new Set();

async function findCleanup() {
  const body = {
    sender: $('#cfSender').value.trim(),
    subject: $('#cfSubject').value.trim(),
    date_from: $('#cfDateFrom').value,
    date_to: $('#cfDateTo').value,
    min_mb: $('#cfMinMb').value ? parseFloat($('#cfMinMb').value) : null,
    max_mb: $('#cfMaxMb').value ? parseFloat($('#cfMaxMb').value) : null,
  };
  if (!body.sender && !body.subject && !body.date_from && !body.date_to
      && body.min_mb == null && body.max_mb == null) {
    toast(t('cleanup.needCond'));
    return;
  }
  const btn = $('#cleanupFindBtn');
  btn.disabled = true;
  btn.textContent = t('cleanup.finding');
  $('#cleanupSummary').style.display = 'none';
  $('#cleanupResults').style.display = 'none';
  $('#cleanupActions').style.display = 'none';
  try {
    const r = await api('/api/mail-cleanup/find', { method: 'POST', body: JSON.stringify(body) });
    cleanupItems = r.items;
    cleanupSel = new Set();
    const sum = $('#cleanupSummary');
    sum.style.display = 'block';
    sum.innerHTML = r.total
      ? `${t('cleanup.found', { n: r.total }).split('·')[0].trim()} · ${t('cleanup.occ', { size: fmtSize(r.total_export_bytes) })}`
      : t('cleanup.none');
    renderCleanupResults(r.total);
  } catch (e) {
    toast(t('cleanup.findFail', { msg: e.message }));
  }
  btn.disabled = false;
  btn.textContent = t('cleanup.find');
}

function renderCleanupResults(totalFound) {
  const box = $('#cleanupResults');
  box.style.display = 'block';
  $('#cleanupActions').style.display = totalFound ? 'block' : 'none';
  $('#cleanupDelBtn').disabled = true;
  if (!totalFound) { box.innerHTML = ''; return; }
  box.innerHTML = `
    <label class="cleanup-selectall"><input type="checkbox" id="cleanupAll"><span data-i18n="cleanup.selectall">${t('cleanup.selectall')}</span></label>
    ${cleanupItems.map((m) => `
      <label class="cleanup-row">
        <input type="checkbox" data-id="${m.id}">
        <span class="cr-main">
          <span class="cr-subject" title="${esc(m.subject)}">${esc(m.subject)}</span>
          <span class="cr-meta">${esc(m.from)} · ${fmtDate(m.date)}</span>
        </span>
        <span class="cr-size num">${fmtSize(m.export_size)}</span>
      </label>`).join('')}`;
  const refresh = () => {
    cleanupSel = new Set([...box.querySelectorAll('input[data-id]:checked')].map((c) => +c.dataset.id));
    $('#cleanupDelBtn').disabled = !cleanupSel.size;
    $('#cleanupDelBtn').textContent = t('cleanup.del.n', { n: cleanupSel.size });
  };
  box.querySelectorAll('input[data-id]').forEach((c) => c.addEventListener('change', refresh));
  $('#cleanupAll').addEventListener('change', (e) => {
    box.querySelectorAll('input[data-id]').forEach((c) => { c.checked = e.target.checked; });
    refresh();
  });
  refresh();
}

async function deleteCleanup() {
  const ids = [...cleanupSel];
  if (!ids.length) return;
  const mode = document.querySelector('input[name="delMode"]:checked')?.value || 'full';
  if (!confirm(t('cleanup.confirm.' + mode, { n: ids.length }))) return;
  try {
    const r = await api('/api/mail-cleanup/delete', { method: 'POST', body: JSON.stringify({ ids, mode }) });
    toast(mode === 'full'
      ? t('cleanup.deleted.full', { n: r.deleted, size: fmtSize(r.freed_bytes) })
      : t('cleanup.deleted.index', { n: r.deleted }));
    cleanupSel = new Set();
    clearGroupCache();
    findCleanup();
    loadArchives();
    loadMails();
    renderStats();
  } catch (e) {
    toast(t('cleanup.delFail', { msg: e.message }));
  }
}

/* ── Agent 接入页加载 ─────────────────────────────────── */
async function loadAgentKeyOld() {}

window.addEventListener('unhandledrejection', (e) => {
  console.error('[fox-converter]', e.reason);
  toast(t('op.fail', { msg: e.reason?.message || e.reason }));
});

async function refreshExportConfigured() {
  try {
    const p = await api('/api/paths');
    state.exportConfigured = !!p.export_configured;
  } catch {}
  return state.exportConfigured;
}

async function pollMigration() {
  try {
    const s = await api('/api/migration/status');
    if (s.status !== 'running') return;
    toast(t('idx.building'));
    const timer = setInterval(async () => {
      try {
        const st = await api('/api/migration/status');
        if (st.status === 'done') { clearInterval(timer); toast(t('idx.done')); }
        else if (st.status !== 'running') clearInterval(timer);
      } catch { clearInterval(timer); }
    }, 2000);
  } catch {}
}

async function init() {
  bindEvents();
  let lang = 'zh-CN';
  try {
    const cfg = await api('/api/settings');
    lang = cfg.lang || 'zh-CN';
    applyTheme(cfg.theme, cfg.accent_color);
    $('#settingTheme').value = cfg.theme;
    $('#settingAccent').value = cfg.accent_color;
    $('#settingBrowser').value = cfg.browser;
    $('#settingLang').value = lang;
  } catch (e) {
    console.error('settings', e);
  }
  applyLang(lang);

  try { await api('/api/health'); }
  catch { $('#offlineBanner').style.display = 'flex'; }

  try {
    await loadArchives();
    await loadMails();
    renderStats();
  } catch (e) {
    console.error('init', e);
    toast(t('load.fail', { msg: e.message }));
  }
  pollMigration();

  if ((await refreshExportConfigured()) === false) {
    $$('.nav-item[data-route]').forEach((b) => b.classList.remove('active'));
    document.querySelector('.nav-item[data-route="settings"]')?.classList.add('active');
    $('#settingsPanel').style.display = 'flex';
    await loadSettings();
    toast(t('firstrun'));
  }
}

init();
