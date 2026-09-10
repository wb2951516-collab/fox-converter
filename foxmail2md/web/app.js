/* ═══════════════════════════════════════════════════════════
   Fox Converter 前端逻辑
   ═══════════════════════════════════════════════════════════ */
'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

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
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
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
    document.documentElement.style.setProperty('--primary-soft',
      `color-mix(in srgb, ${accent} 10%, transparent)`);
    document.documentElement.style.setProperty('--selected',
      `color-mix(in srgb, ${accent} 7%, transparent)`);
  }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── 主题归一（分组用）────────────────────────────────── */
function normSubject(s) {
  let out = String(s || '').trim();
  while (true) {
    const next = out
      .replace(/^\s*((re|fw|fwd|aw|sv)\s*[:：]\s*)/i, '')
      .replace(/^\s*(回复|转发|答复|转送)\s*[:：]\s*/, '')
      .replace(/^\s*【[^】]{1,12}】\s*/, '');
    if (next === out) break;
    out = next;
  }
  return out.trim() || '(无主题)';
}

function addrKey(s) {
  const m = String(s || '').match(/<([^>]+)>/);
  return (m ? m[1] : String(s || '')).trim() || '(空)';
}

function attIcon(name) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  const map = {
    pdf: 'M7 3h8l4 4v14H7z M15 3v5h5',
    doc: 'M7 3h8l4 4v14H7z M15 3v5h5',
    xls: 'M7 3h8l4 4v14H7z M15 3v5h5',
    zip: 'M7 3h8l4 4v14H7z M15 3v5h5',
    rar: 'M7 3h8l4 4v14H7z M15 3v5h5',
  };
  return map[ext] || map.pdf;
}

/* ── 邮件列表 ─────────────────────────────────────────── */
async function loadMails() {
  const grouped = !!state.group;
  const params = new URLSearchParams({
    page: grouped ? 1 : state.page,
    per_page: grouped ? 2000 : state.perPage,
    search: state.search, order: state.order,
  });
  if (state.archiveId) params.set('archive_id', state.archiveId);
  const data = await api(`/api/mails?${params}`);
  state.total = data.total;
  if (grouped) {
    renderGroupedMails(data.items);
    $('#listFooter').innerHTML = `<span class="num">共 ${data.items.length} 封 · ${groupsLabel()}</span>`;
  } else {
    renderMailList(data.items);
    renderPagination();
  }
  await renderStats();
}

function groupsLabel() {
  return { subject: '按主题分组', from: '按发件人分组', to: '按收件人分组' }[state.group] || '';
}

function renderGroupedMails(items) {
  const box = $('#mailList');
  if (!items.length) {
    box.innerHTML = `<div class="empty-state"><p>${state.search ? '没有匹配的邮件' : '暂无邮件'}</p></div>`;
    return;
  }
  const keyOf = (m) => {
    if (state.group === 'subject') return normSubject(m.subject);
    if (state.group === 'from') return addrKey(m.from);
    if (state.group === 'to') return addrKey(m.to);
    return '';
  };
  const groups = new Map();
  for (const m of items) {
    const k = keyOf(m);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);

  const itemHtml = (m) => `
    <button class="mail-item ${m.id === state.selectedId ? 'selected' : ''}" data-id="${m.id}" aria-label="${esc(m.subject)}">
      <div class="mail-item-top">
        <span class="mail-from">${state.group === 'from' ? esc(normSubject(m.subject)) : esc(m.from)}</span>
        <span class="mail-date">${fmtDate(m.date)}</span>
      </div>
      ${state.group === 'from' ? '' : `<div class="mail-subject">${esc(m.subject)}</div>`}
      ${m.has_attachments ? `
      <div class="mail-att-badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="12" height="12"><path d="M21 12.5l-8.5 8.5a5 5 0 01-7-7L14 5.5a3.5 3.5 0 015 5L10.5 19a2 2 0 01-3-3l6.5-6.5"/></svg>
        ${m.attachment_count} 个附件
      </div>` : ''}
    </button>`;

  box.innerHTML = sorted.map(([key, mails], gi) => {
    const open = state.expandedGroups.has(key) || gi === 0;
    return `
      <div class="group-block">
        <button class="group-header" data-key="${esc(key)}" aria-expanded="${open}">
          <svg class="icon chev ${open ? 'open' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M9 18l6-6-6-6"/></svg>
          <span class="group-name" title="${esc(key)}">${esc(key)}</span>
          <span class="group-count num">${mails.length}</span>
        </button>
        <div class="group-body" style="display:${open ? 'block' : 'none'}">
          ${mails.map(itemHtml).join('')}
        </div>
      </div>`;
  }).join('');

  box.querySelectorAll('.group-header').forEach((h) => {
    h.addEventListener('click', () => {
      const key = h.dataset.key;
      const body = h.nextElementSibling;
      const open = body.style.display === 'none';
      body.style.display = open ? 'block' : 'none';
      h.setAttribute('aria-expanded', String(open));
      h.querySelector('.chev')?.classList.toggle('open', open);
      if (open) state.expandedGroups.add(key); else state.expandedGroups.delete(key);
    });
  });
  box.querySelectorAll('.mail-item').forEach((el) => {
    el.addEventListener('click', () => selectMail(+el.dataset.id));
  });
}

function renderMailList(items) {
  const box = $('#mailList');
  if (!items.length) {
    box.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" width="40" height="40" style="opacity:.35">
          <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
        </svg>
        <p>${state.search ? '没有匹配的邮件' : '暂无邮件，请先导入 .fox 存档'}</p>
      </div>`;
    return;
  }
  box.innerHTML = items.map((m) => `
    <button class="mail-item ${m.id === state.selectedId ? 'selected' : ''}" data-id="${m.id}" aria-label="${esc(m.subject)}">
      <div class="mail-item-top">
        <span class="mail-from">${esc(m.from)}</span>
        <span class="mail-date">${fmtDate(m.date)}</span>
      </div>
      <div class="mail-subject">${esc(m.subject)}</div>
      ${m.has_attachments ? `
      <div class="mail-att-badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="12" height="12"><path d="M21 12.5l-8.5 8.5a5 5 0 01-7-7L14 5.5a3.5 3.5 0 015 5L10.5 19a2 2 0 01-3-3l6.5-6.5"/></svg>
        ${m.attachment_count} 个附件
      </div>` : ''}
    </button>`).join('');

  box.querySelectorAll('.mail-item').forEach((el) => {
    el.addEventListener('click', () => selectMail(+el.dataset.id));
  });
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.perPage));
  const footer = $('#listFooter');
  if (totalPages <= 1) { footer.innerHTML = `<span class="num">${state.total} 封</span>`; return; }
  const cur = state.page;
  const btn = (p, label = p, disabled = false, active = false) =>
    `<button class="page-btn ${active ? 'active' : ''}" ${disabled ? 'disabled' : ''} data-page="${p}">${label}</button>`;
  let html = btn(cur - 1, '‹', cur <= 1);
  const pages = [];
  for (let p = 1; p <= totalPages; p++) {
    if (p === 1 || p === totalPages || Math.abs(p - cur) <= 1) pages.push(p);
    else if (pages[pages.length - 1] !== '…') pages.push('…');
  }
  for (const p of pages) {
    html += p === '…' ? '<span>…</span>' : btn(p, p, false, p === cur);
  }
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
    <div class="stat-row"><span class="stat-label">邮件</span><span class="stat-value num">${s.total}</span></div>
    <div class="stat-row"><span class="stat-label">附件</span><span class="stat-value num">${s.attachment_count}</span></div>
    <div class="stat-row"><span class="stat-label">总量</span><span class="stat-value num">${fmtSize(s.total_size)}</span></div>
    ${s.min_date ? `<div class="stat-row"><span class="stat-label">时间</span><span class="stat-value num">${dateRange(s.min_date, s.max_date)}</span></div>` : ''}
  ` : '';
}

/* ── 存档列表（侧栏） ─────────────────────────────────── */
async function loadArchives() {
  const data = await api('/api/archives');
  const box = $('#archiveList');
  const item = (a) => `
    <button class="archive-item ${a.id === state.archiveId ? 'selected' : ''}" data-id="${a.id}" title="${esc(a.name)}（${fmtSize(a.total_size)}）">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/></svg>
      <span class="archive-name">${esc(a.name)}</span>
      <span class="archive-count num">${a.email_count}</span>
      <span class="archive-del" data-del="${a.id}" role="button" aria-label="删除存档 ${esc(a.name)}" title="删除此存档">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/></svg>
      </span>
    </button>`;
  box.innerHTML =
    `<button class="archive-item ${state.archiveId === 0 ? 'selected' : ''}" data-id="0">
       <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M3 12l9-9 9 9M5 10v10a1 1 0 001 1h3m10-11v10a1 1 0 01-1 1h-3m-6 0h6"/></svg>
       <span class="archive-name">全部存档</span>
     </button>` +
    data.archives.map(item).join('');

  box.querySelectorAll('.archive-item').forEach((el) => {
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('.archive-del')) return;
      state.archiveId = +el.dataset.id;
      state.page = 1;
      state.search = '';
      $('#searchInput').value = '';
      loadMails();
      loadArchives();
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
    toast('正在删除存档…');
    await api(`/api/archives/${id}`, { method: 'DELETE' });
    toast('存档已删除');
    if (state.archiveId === id) state.archiveId = 0;
    loadArchives();
    loadMails();
  } else {
    state.confirmDeleteId = id;
    const btn = document.querySelector(`.archive-del[data-del="${id}"]`);
    if (btn) {
      btn.classList.add('confirm');
      btn.innerHTML = '<span style="font-size:.6875rem;white-space:nowrap">确认删除</span>';
      setTimeout(() => {
        if (state.confirmDeleteId === id) {
          state.confirmDeleteId = null;
          loadArchives();
        }
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

  // 附件 tab：有附件才显示
  const attTab = document.querySelector('.tab[data-view="atts"]');
  attTab.style.display = mail.attachment_names.length ? '' : 'none';

  renderBody();
}

function renderBody() {
  const mail = state.currentMail;
  if (!mail) return;
  const body = $('#readerBody');
  const view = state.currentView;

  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));

  if (view === 'html') {
    body.innerHTML = `
      <div class="mail-header">
        <h1 class="mail-title">${esc(mail.subject)}</h1>
        <div class="mail-meta-grid">
          <span class="mail-meta-label">发件人</span><span class="mail-meta-value">${esc(mail.from)}</span>
          <span class="mail-meta-label">收件人</span><span class="mail-meta-value">${esc(mail.to)}</span>
          ${mail.cc ? `<span class="mail-meta-label">抄送</span><span class="mail-meta-value">${esc(mail.cc)}</span>` : ''}
          <span class="mail-meta-label">时间</span><span class="mail-meta-value">${fmtDateFull(mail.date)}</span>
        </div>
      </div>
      <div class="reader-content html-view">${sanitizeHtml(mail.body_html, mail.id) || '(空正文)'}</div>`;
  } else if (view === 'md') {
    body.innerHTML = `<pre class="md-view">${esc(mail.body_md || '(空)')}</pre>`;
  } else if (view === 'text') {
    body.innerHTML = `<div class="reader-content plain-view">${esc(mail.body_text || '(空)')}</div>`;
  } else if (view === 'atts') {
    // 防御：无附件邮件永远不渲染附件视图（tab 已隐藏时的兜底）
    if (!mail.attachment_names.length) {
      state.currentView = 'html';
      return renderBody();
    }
    body.innerHTML = renderAttachmentsView(mail);
    body.querySelectorAll('[data-att-index]').forEach((el) => {
      el.addEventListener('click', () => {
        const i = +el.dataset.attIndex;
        triggerDownload(mail, i);
      });
    });
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
          <div class="att-row-sub">${esc(ext)} 文件 · ${esc(rel)}</div>
        </div>
        <button class="btn btn-sm" data-att-index="${i}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="15" height="15"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
          下载
        </button>
      </div>`;
  }).join('');
  return `
    <div class="reader-content atts-view">
      <div class="atts-title">本邮件共 ${mail.attachment_names.length} 个附件</div>
      <div class="atts-list">${rows}</div>
    </div>`;
}

function triggerDownload(mail, i) {
  const rel = mail.attachment_files[i];
  if (!rel) return;
  const url = `/api/attachment/${mail.id}/${rel}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = mail.attachment_names[i] || '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast(`开始下载：${mail.attachment_names[i]}`);
}

/* 防 XSS + 内嵌图片改为 API 路径（否则导出目录不在前端同源下图片全部裂图） */
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
  pickedPaths = [];
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
  btns.forEach((b) => (b.disabled = true));
  const verified = [...pickedPaths];      // {path,size,total}
  const checking = newOnes.map((p) => ({ path: p, done: false, error: '' }));
  const box = $('#parseFileList');
  box.style.display = 'block';
  const render = () => {
    box.innerHTML =
      verified.map((v) => `
        <div class="picked-row">
          <span class="picked-name" title="${esc(v.path)}">${esc(v.path.split(/[\\/]/).pop())}</span>
          <span class="picked-meta num">${fmtSize(v.size)} · ${v.total != null ? v.total + ' 封' : ''}</span>
        </div>`).join('') +
      checking.filter((c) => !c.done).map((c) => `
        <div class="picked-row checking">
          <span class="picked-name">${esc(c.path.split(/[\\/]/).pop())}</span>
          <span class="picked-meta">检查中…</span>
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
  btns.forEach((b) => (b.disabled = false));
  pickedPaths = verified.map((v) => v.path);
  let disk = null;
  try {
    disk = await api('/api/diskcheck', {
      method: 'POST',
      body: JSON.stringify({ paths: pickedPaths }),
    });
  } catch {}
  const line = $('#diskLine');
  if (disk) {
    line.style.display = 'block';
    const ok = disk.free >= disk.need;
    line.innerHTML = `导出位置 <span title="${esc(disk.export_root)}">${esc(disk.export_root)}</span>` +
      ` · 约需 ${fmtSize(disk.need)} · 该位置可用 ${fmtSize(disk.free)}` +
      (ok ? ' <span style="color:var(--success)">✓</span>'
          : ' <span style="color:var(--danger)">空间不足，请到设置页更换导出目录</span>');
    $('#startParseBtn').disabled = !ok;
  } else {
    line.style.display = 'none';
  }
  if (pickedPaths.length) $('#startParseBtn').style.display = 'inline-flex';
}

async function browseFile() {
  try {
    const res = await api('/api/browse', { method: 'POST' });
    if (res.skipped) toast(`${res.skipped} 个非 .fox 文件已跳过`);
    await pickPaths(res.paths || []);
  } catch (e) {
    toast(`选择失败：${e.message}`);
  }
}

async function browseFolder() {
  try {
    const res = await api('/api/browse_folder', { method: 'POST' });
    if (!(res.paths || []).length) { toast('该文件夹内未找到 .fox 存档'); return; }
    await pickPaths(res.paths);
  } catch (e) {
    toast(`选择失败：${e.message}`);
  }
}

async function useManualPath() {
  const raw = $('#manualPath').value.trim();
  if (!raw) { toast('请输入文件路径'); return; }
  const paths = raw.split(/[\n;；]/).map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean);
  await pickPaths(paths);
}

async function startParse() {
  if (!pickedPaths.length) return;
  $('#parseStepSelect').style.display = 'none';
  $('#parseStepProgress').style.display = 'block';
  try {
    const res = await api('/api/parse', {
      method: 'POST',
      body: JSON.stringify({ paths: pickedPaths }),
    });
    listenProgress(res.task_id);
  } catch (e) {
    toast(`启动失败：${e.message}`);
    openBrowse();
  }
}

function listenProgress(taskId) {
  const es = new EventSource(`/api/parse/status/${taskId}`);
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    const filePct = d.total ? (d.current / d.total) * 100 : 0;
    const overall = d.file_total
      ? ((d.file_index - 1 + filePct / 100) / d.file_total) * 100
      : 0;
    $('#progressFill').style.width = `${Math.min(100, overall).toFixed(1)}%`;
    if (d.status === 'done') {
      $('#progressText').textContent = '导入完成';
      $('#progressFile').textContent = `共 ${d.file_total} 个文件`;
      $('#progressSub').textContent =
        `新增 ${d.imported} 封 · 跳过重复 ${d.skipped} 封 · 失败 ${d.errors} · 耗时 ${d.elapsed.toFixed(1)} 秒`;
    } else {
      $('#progressFile').textContent = `文件 ${d.file_index} / ${d.file_total}：${d.filename || ''}`;
      $('#progressText').textContent =
        `${d.current} / ${d.total || '…'}（${filePct.toFixed(0)}%）`;
      $('#progressSub').textContent = d.subject || '';
    }
    const df = $('#doneFiles');
    df.textContent = (d.done_files || []).map((f) => `✓ ${f}`).join('　');
    if (d.status === 'done' || d.status === 'cancelled') {
      es.close();
      if (d.status === 'cancelled') toast('导入已取消');
      $('#parseStepProgress').style.display = 'none';
      $('#parseStepDone').style.display = 'block';
      $('#doneText').innerHTML =
        `已导入 <strong class="num">${d.imported}</strong> 封邮件` +
        (d.skipped ? `，跳过重复 <strong class="num">${d.skipped}</strong> 封` : '') +
        (d.errors ? `，<span style="color:var(--danger)">失败 ${d.errors} 封</span>` : '') +
        `<br><span style="font-size:.8125rem;color:var(--text-3)">耗时 ${d.elapsed.toFixed(1)} 秒</span>`;
      state.page = 1;
      state.search = '';
      $('#searchInput').value = '';
      loadArchives();
      loadMails();
    } else if (d.status === 'error') {
      es.close();
      toast(`导入失败：${d.error}`);
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
      if (btn.dataset.route === 'settings') {
        $('#settingsPanel').style.display = 'flex';
        loadSettings();
      } else if (btn.dataset.route === 'agent') {
        $('#agentPanel').style.display = 'flex';
        loadAgentKey();
      }
    });
  });

  $('#openParseBtn').addEventListener('click', openBrowse);
  $('#browseBtn').addEventListener('click', browseFile);
  $('#browseFolderBtn').addEventListener('click', browseFolder);
  $('#useManualPathBtn').addEventListener('click', useManualPath);
  $('#startParseBtn').addEventListener('click', startParse);
  $('#parseCloseBtn').addEventListener('click', () => { $('#parseModal').style.display = 'none'; });
  $('#doneCloseBtn').addEventListener('click', () => { $('#parseModal').style.display = 'none'; });
  $('#settingsCloseBtn').addEventListener('click', () => {
    $('#settingsPanel').style.display = 'none';
    $$('.nav-item[data-route]').forEach((b) => b.classList.toggle('active', b.dataset.route === 'mails'));
  });
  $('#agentCloseBtn').addEventListener('click', () => {
    $('#agentPanel').style.display = 'none';
    $$('.nav-item[data-route]').forEach((b) => b.classList.toggle('active', b.dataset.route === 'mails'));
  });
  $('#copyPromptBtn').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('#agentPrompt').textContent);
      toast('提示词已复制，粘贴给智能体即可安装技能');
    } catch { toast('复制失败'); }
  });
  $('#copyKeyBtn').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('#agentKey').textContent);
      toast('API Key 已复制');
    } catch { toast('复制失败'); }
  });
  $('#regenKeyBtn').addEventListener('click', async () => {
    if (!confirm('重新生成后旧 Key 立即失效，已配置的智能体需要重新配置。继续？')) return;
    const r = await api('/api/agent-key/regenerate', { method: 'POST' });
    $('#agentKey').textContent = r.api_key;
    toast('已重新生成 API Key');
  });

  // 存储位置
  $('#browseDataDirBtn').addEventListener('click', async () => {
    const r = await api('/api/browse_dir', { method: 'POST' });
    if (r.path) $('#settingDataDir').value = r.path;
  });
  $('#browseExportDirBtn').addEventListener('click', async () => {
    const r = await api('/api/browse_dir', { method: 'POST' });
    if (r.path) $('#settingDir').value = r.path;
  });
  $('#migrateBtn').addEventListener('click', migrateStorage);

  const doSearch = debounce(() => {
    state.search = $('#searchInput').value.trim();
    state.page = 1;
    loadMails();
  }, 350);
  $('#searchInput').addEventListener('input', doSearch);
  $('#sortSelect').addEventListener('change', (e) => {
    state.order = e.target.value;
    state.page = 1;
    loadMails();
  });
  $('#groupSelect').addEventListener('change', (e) => {
    state.group = e.target.value;
    state.page = 1;
    loadMails();
  });

  $$('.tab').forEach((t) => t.addEventListener('click', () => {
    if (t.style.display === 'none') return;
    state.currentView = t.dataset.view;
    renderBody();
  }));

  $('#copyBtn').addEventListener('click', async () => {
    const mail = state.currentMail;
    if (!mail) return;
    let text = '';
    if (state.currentView === 'md') text = mail.body_md;
    else if (state.currentView === 'text') text = mail.body_text;
    else if (state.currentView === 'atts') {
      text = mail.attachment_names.join('\n');
    } else text = mail.body_html || mail.body_text;
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板');
    } catch {
      toast('复制失败');
    }
  });

  $('#settingTheme').addEventListener('change', saveSettings);
  $('#settingAccent').addEventListener('change', saveSettings);
  $('#settingBrowser').addEventListener('change', saveSettings);
}

async function loadSettings() {
  const cfg = await api('/api/settings');
  applyTheme(cfg.theme, cfg.accent_color);
  $('#settingTheme').value = cfg.theme;
  $('#settingAccent').value = cfg.accent_color;
  $('#settingBrowser').value = cfg.browser;
  try {
    const p = await api('/api/paths');
    $('#settingDataDir').value = p.data_dir;
    $('#settingDir').value = p.export_dir;
    $('#dataSizeLabel').textContent = `（${fmtSize(p.data_size)}）`;
    $('#exportSizeLabel').textContent = `（${fmtSize(p.export_size)}）`;
  } catch {}
}

async function saveSettings() {
  const cfg = {
    theme: $('#settingTheme').value,
    accent_color: $('#settingAccent').value,
    browser: $('#settingBrowser').value,
  };
  applyTheme(cfg.theme, cfg.accent_color);
  await api('/api/settings', { method: 'PUT', body: JSON.stringify(cfg) });
  toast('设置已保存');
}

async function migrateStorage() {  const dataDir = $('#settingDataDir').value.trim();
  const exportDir = $('#settingDir').value.trim();
  const body = {};
  if (exportDir && exportDir !== (await api('/api/paths')).export_dir) body.export_dir = exportDir;
  if (dataDir && dataDir !== (await api('/api/paths')).data_dir) body.data_dir = dataDir;
  if (!Object.keys(body).length) { toast('路径未变化，无需迁移'); return; }

  const bar = $('#migrateProgressBar');
  const fill = $('#migrateProgressFill');
  $('#migrateBtn').disabled = true;
  try {
    const r = await api('/api/migrate', { method: 'POST', body: JSON.stringify(body) });
    let needWait = false;
    if (r.data_dir) {
      if (r.data_dir.status === 'switched') {
        toast('存档数据库已切换到新位置，立即生效');
        $('#migrateNote').textContent = '数据库已迁移并即时生效。';
      } else {
        $('#migrateNote').textContent = '数据库已复制，重启软件后生效。';
        needWait = true;
      }
    }
    if (r.export_dir && r.export_dir.task_id) {
      needWait = true;
      bar.style.display = 'block';
      const tid = r.export_dir.task_id;
      const timer = setInterval(async () => {
        const s = await api(`/api/migrate/status/${tid}`);
        const pct = s.total ? Math.round((s.copied / s.total) * 100) : 0;
        fill.style.width = `${pct}%`;
        if (s.status === 'done') {
          clearInterval(timer);
          bar.style.display = 'none';
          $('#migrateBtn').disabled = false;
          toast(`导出目录已迁移（${s.total} 个文件），立即生效`);
          loadSettings();
        } else if (s.status === 'error') {
          clearInterval(timer);
          bar.style.display = 'none';
          $('#migrateBtn').disabled = false;
          toast(`迁移失败：${s.error}`);
        }
      }, 800);
    } else if (!r.export_dir && r.export_dir?.status === 'switched') {
      toast('导出目录已切换，立即生效');
    }
    if (!needWait) $('#migrateBtn').disabled = false;
    loadSettings();
  } catch (e) {
    toast(`迁移失败：${e.message}`);
    $('#migrateBtn').disabled = false;
  }
}

/* ── Agent 接入 ───────────────────────────────────────── */
async function loadAgentKey() {  const k = await api('/api/agent-key');
  const base = `http://127.0.0.1:${k.port}`;
  $('#agentKey').textContent = k.api_key;
  $('#agentStats').textContent = `已导入 ${k.emails} 封 · ${k.archives} 个存档 · 服务地址 ${base}`;
  $('#agentPrompt').textContent =
`请安装 fox-converter 技能
下载地址：${base}/skill/download
API Key：${k.api_key}
安装说明：下载 zip 并解压到你的技能目录（如 ~/.claude/skills/ 或 openclaw 的 skills 目录），读取其中的 SKILL.md 并按其说明，通过 HTTP 访问我本地的 Fox Converter 邮件存档：
- 服务地址：${base}
- 请求头：X-API-Key: ${k.api_key}
安装完成后，用"搜索邮件"接口列出我最近的 3 封邮件，确认技能可用。`;
}

/* ── 启动 ─────────────────────────────────────────────── */
window.addEventListener('unhandledrejection', (e) => {
  console.error('[fox-converter]', e.reason);
  toast(`操作失败：${e.reason?.message || e.reason}`);
});

async function init() {
  bindEvents();
  // 探活：服务未运行时显示明确横幅（页面可能是残留标签）
  try {
    await api('/api/health');
  } catch {
    $('#offlineBanner').style.display = 'flex';
  }
  $('#reconnectBtn').addEventListener('click', () => location.reload());
  try {
    const cfg = await api('/api/settings');
    applyTheme(cfg.theme, cfg.accent_color);
    $('#settingTheme').value = cfg.theme;
    $('#settingAccent').value = cfg.accent_color;
    $('#settingBrowser').value = cfg.browser;
  } catch (e) {
    console.error('设置加载失败', e);
  }
  try {
    await loadArchives();
    await loadMails();
  } catch (e) {
    console.error('初始化加载失败', e);
    toast(`加载失败：${e.message}`);
  }
}

init();
