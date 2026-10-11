'use strict';

/* ══════════════════════════════════════════════════════ 基础工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $all = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (v) => (v == null ? '' : String(v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));

const FIELDS = ['title', 'author', 'translator', 'publisher', 'publication_place',
  'publication_year', 'edition', 'isbn', 'clc_code', 'literature_type', 'file_path', 'abstract'];

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* 非 JSON 响应 */ }
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.toggle('err', isErr);
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), isErr ? 4200 : 2200);
}

/* ══════════════════════════════════════════════════════ 弹层 */
function openModal(title, bodyHTML, buttons = []) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHTML;
  const foot = $('#modal-foot');
  foot.innerHTML = '';
  for (const b of buttons) {
    const btn = document.createElement('button');
    btn.textContent = b.label;
    if (b.primary) btn.className = 'primary';
    if (b.danger) btn.className = 'danger';
    btn.onclick = b.onClick;
    foot.appendChild(btn);
  }
  $('#modal').classList.remove('hidden');
}
function closeModal() { $('#modal').classList.add('hidden'); }

/* ══════════════════════════════════════════════════════ 列表 */
const state = { books: [], current: null, mode: 'view' };

async function loadMeta() {
  try {
    const m = await api('/api/meta');
    $('#lib-root').textContent = m.library_root ? `库根：${m.library_root}` : '未配置库根';
  } catch (_) { /* 忽略 */ }
}

function renderRows(items) {
  const tb = $('#book-rows');
  tb.innerHTML = '';
  $('#empty').classList.toggle('hidden', items.length > 0);
  $('#list-count').textContent = `共 ${items.length} 条`;
  for (const b of items) {
    const tr = document.createElement('tr');
    tr.dataset.id = b.id;
    tr.innerHTML =
      `<td class="id">${b.id}</td>` +
      `<td>${esc(b.title)}</td>` +
      `<td>${esc(b.author)}</td>` +
      `<td>${esc(b.publisher_name)}</td>` +
      `<td>${b.publication_year ?? ''}</td>` +
      `<td>${esc(b.clc_code)}</td>`;
    tr.onclick = () => selectBook(b.id);
    tb.appendChild(tr);
  }
  if (state.current) highlightRow(state.current.id);
}

function highlightRow(id) {
  $all('#book-rows tr').forEach(tr => tr.classList.toggle('active', Number(tr.dataset.id) === id));
}

async function loadList() {
  const data = await api('/api/books');
  state.books = data.items;
  renderRows(data.items);
}

/* ══════════════════════════════════════════════════════ 详情 / 编辑 */
function blankDetail() {
  state.current = null;
  state.mode = 'new';
  $('#detail-empty').classList.add('hidden');
  const form = $('#detail-form');
  form.classList.remove('hidden');
  $('#detail-title').textContent = '新建图书';
  $('#detail-id').textContent = '';
  FIELDS.forEach(n => { form.elements[n].value = ''; });
  $('#detail-meta').textContent = '';
  highlightRow(-1);
  form.elements.title.focus();
}

async function selectBook(id) {
  const book = await api(`/api/books/${id}`);
  state.current = book;
  state.mode = 'view';
  $('#detail-empty').classList.add('hidden');
  const form = $('#detail-form');
  form.classList.remove('hidden');
  $('#detail-title').textContent = book.title || '（无书名）';
  $('#detail-id').textContent = `#${book.id}`;
  FIELDS.forEach(n => {
    const v = n === 'publisher' ? book.publisher_name : book[n];
    form.elements[n].value = v ?? '';
  });
  $('#detail-meta').textContent = `创建 ${book.created_at || '-'} · 更新 ${book.updated_at || '-'}`;
  highlightRow(id);
}

function collectForm() {
  const f = $('#detail-form').elements;
  const out = {};
  for (const n of FIELDS) {
    const v = f[n].value.trim();
    out[n] = v === '' ? null : v;     // 空 = 清空（表单即权威）
  }
  return out;
}

async function saveBook(ev) {
  ev.preventDefault();
  const body = collectForm();
  if (!body.title) { toast('书名不能为空', true); return; }
  try {
    if (state.mode === 'new') {
      await submitAdd(body);
    } else {
      await api(`/api/books/${state.current.id}`, { method: 'PUT', body });
      toast('已保存');
      await loadList();
      await selectBook(state.current.id);
    }
  } catch (err) {
    toast(err.message, true);
  }
}

async function submitAdd(body, allowDuplicate = false) {
  try {
    const r = await api('/api/books', { method: 'POST', body: { ...body, allow_duplicate: allowDuplicate } });
    toast('已添加');
    await loadList();
    await selectBook(r.id);
  } catch (err) {
    if (err.status === 409 && err.data.duplicates) {
      openModal('疑似重复',
        `<p>发现 <b>${err.data.duplicates.length}</b> 条疑似重复记录：</p>
         <pre class="box">${esc(err.data.duplicates.map(d => `#${d.id}  ${d.title}  ${d.author || ''}`).join('\n'))}</pre>
         <p class="muted small">仍要添加吗？</p>`,
        [{ label: '取消', onClick: closeModal },
         { label: '仍然添加', danger: true, onClick: () => { closeModal(); submitAdd(body, true); } }]);
      return;
    }
    toast(err.message, true);
  }
}

async function deleteBook() {
  if (!state.current) return;
  const id = state.current.id;
  openModal('删除确认',
    `<p>确定删除 <b>#${id} ${esc(state.current.title)}</b> ？</p>
     <p class="muted small">只删除数据库记录，实际 PDF 文件保持不动。</p>`,
    [{ label: '取消', onClick: closeModal },
     { label: '删除', danger: true, onClick: async () => {
         closeModal();
         try {
           await api(`/api/books/${id}`, { method: 'DELETE' });
           toast('已删除');
           state.current = null;
           $('#detail-form').classList.add('hidden');
           $('#detail-empty').classList.remove('hidden');
           await loadList();
         } catch (err) { toast(err.message, true); }
       } }]);
}

/* ══════════════════════════════════════════════════════ 引用 / 重命名 */
async function showCite() {
  if (!state.current) return;
  try {
    const { citation } = await api(`/api/cite/${state.current.id}`);
    openModal('GB/T 7714 引用',
      `<pre class="box" id="cite-text">${esc(citation)}</pre>`,
      [{ label: '关闭', onClick: closeModal },
       { label: '复制', primary: true, onClick: () => copyText(citation) }]);
  } catch (err) { toast(err.message, true); }
}

async function showRename() {
  if (!state.current) return;
  const id = state.current.id;
  try {
    const r = await api(`/api/rename/${id}`);
    const changed = r.changed;
    openModal('重命名预览',
      `<div class="diff-row">旧：<span class="muted">${esc(r.old_name)}</span></div>
       <div class="diff-row">新：<b>${esc(r.new_name)}</b></div>
       ${changed ? '' : '<p class="muted small">已是规范文件名，无需改动。</p>'}`,
      [{ label: '关闭', onClick: closeModal },
       ...(changed ? [{ label: '执行重命名', primary: true, onClick: () => applyRename(id) }] : [])]);
  } catch (err) { toast(err.message, true); }
}

async function applyRename(id) {
  try {
    const r = await api(`/api/rename/${id}/apply`, { method: 'POST', body: {} });
    closeModal();
    toast('已重命名');
    await loadList();
    if (state.current && state.current.id === id) await selectBook(id);
  } catch (err) {
    if (err.status === 409) { toast('目标文件已存在，未覆盖', true); return; }
    toast(err.message, true);
  }
}

function copyText(text) {
  const done = () => toast('已复制到剪贴板');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (_) { toast('复制失败，请手动选择', true); }
  ta.remove();
}

/* ══════════════════════════════════════════════════════ 导入 */
function pickImportFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,.csv';
  input.onchange = () => {
    const file = input.files[0];
    if (!file) return;
    const fmt = file.name.toLowerCase().endsWith('.csv') ? 'csv' : 'json';
    const reader = new FileReader();
    reader.onload = () => previewImport(fmt, reader.result);
    reader.readAsText(file, 'utf-8');
  };
  input.click();
}

async function previewImport(format, content) {
  let plan;
  try {
    plan = await api('/api/import/preview', { method: 'POST', body: { format, content } });
  } catch (err) { toast(err.message, true); return; }

  if (plan.errors && plan.errors.length) {
    openModal('导入校验失败',
      `<p>发现 ${plan.errors.length} 处错误，<b>未写入任何数据</b>：</p>
       <pre class="box">${esc(plan.errors.join('\n'))}</pre>`,
      [{ label: '关闭', onClick: closeModal }]);
    return;
  }
  if (!plan.total) {
    openModal('无变更', '<p class="muted">没有可导入的变更。</p>', [{ label: '关闭', onClick: closeModal }]);
    return;
  }

  const ins = plan.inserts.map(i => `<div class="diff-row">＋ ${esc(i.title)}  <span class="muted">${esc(i.author || '')}</span></div>`).join('');
  const upd = plan.updates.map(u => {
    const lines = Object.entries(u.fields).map(([k, v]) =>
      `<div class="small">　${k}: <span class="muted">${esc(dim(u.before[k === 'publisher' ? 'publisher_name' : k]))}</span> → <b>${esc(dim(v))}</b></div>`).join('');
    return `<div class="diff-row">✎ #${u.id} ${esc(u.title)}${lines}</div>`;
  }).join('');

  openModal('导入预览',
    `${plan.inserts.length ? `<h4>新增 ${plan.inserts.length} 条</h4>${ins}` : ''}
     ${plan.updates.length ? `<h4>更新 ${plan.updates.length} 条</h4>${upd}` : ''}`,
    [{ label: '取消', onClick: closeModal },
     { label: `确认执行（${plan.total}）`, primary: true,
       onClick: async () => {
         try {
           const r = await api('/api/import/apply', { method: 'POST', body: { format, content } });
           closeModal();
           toast(`已导入：新增 ${r.inserts}，更新 ${r.updates}`);
           await loadList();
         } catch (err) { toast(err.message, true); }
       } }]);
}

const dim = (v) => (v === null || v === undefined || v === '') ? '(空)' : String(v);

/* ══════════════════════════════════════════════════════ 导出 */
function exportData() {
  openModal('导出',
    '<p>选择导出格式（JSON 含 mode:full，可直接回灌）：</p>',
    [{ label: '取消', onClick: closeModal },
     { label: 'JSON', onClick: () => doExport('json') },
     { label: 'CSV', primary: true, onClick: () => doExport('csv') }]);
}

async function doExport(format) {
  try {
    const r = await api(`/api/export?format=${format}`);
    closeModal();
    download(`books.${format}`, r.content);
    toast(`已导出 ${format.toUpperCase()}`);
  } catch (err) { toast(err.message, true); }
}

function download(filename, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
}

/* ══════════════════════════════════════════════════════ 版权页提取 */
async function pickPath(kind) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_file) {
    return await window.pywebview.api.pick_file(kind);
  }
  return prompt(`输入${kind === 'image' ? '图片' : 'PDF'}文件的完整路径：`);
}

async function doEnrich() {
  const path = await pickPath('pdf');
  if (!path) return;
  toast('正在调用本地模型提取，请稍候…');
  let result;
  try {
    result = await api('/api/enrich', { method: 'POST', body: { path } });
  } catch (err) { toast(err.message, true); return; }

  const m = result.metadata;
  const row = (label, name, value) =>
    `<label>${label}<input name="${name}" value="${esc(value ?? '')}"></label>`;
  openModal(`版权页提取结果 · ${result.page ? `第 ${result.page} 页` : result.source}`,
    `<p class="muted small">模型输出，请核对后再写入。空字段表示未识别。</p>
     <div class="grid">
       ${row('书名', 'title', m.title)}
       ${row('作者', 'author', m.author)}
       ${row('译者', 'translator', m.translator)}
       ${row('出版社', 'publisher', m.publisher)}
       ${row('出版地', 'publication_place', m.publication_place)}
       ${row('出版年份', 'publication_year', m.publication_year)}
       ${row('版次', 'edition', m.edition)}
       ${row('ISBN', 'isbn', m.isbn)}
       ${row('文件路径', 'file_path', m.file_path || path)}
     </div>`,
    [{ label: '取消', onClick: closeModal },
     { label: '写入数据库', primary: true, onClick: () => saveEnrich() }]);
}

async function saveEnrich() {
  const box = $('#modal-body');
  const metadata = {};
  $all('input', box).forEach(i => { const v = i.value.trim(); metadata[i.name] = v === '' ? null : v; });
  try {
    const r = await api('/api/enrich/save', { method: 'POST', body: { metadata } });
    closeModal();
    toast('已写入');
    await loadList();
    await selectBook(r.id);
  } catch (err) {
    if (err.status === 409) { toast('发现疑似重复，请先在列表核对', true); return; }
    toast(err.message, true);
  }
}

/* ══════════════════════════════════════════════════════ 搜索 */
let searchTimer = null;
function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 250);
}

async function runSearch() {
  const params = new URLSearchParams();
  const q = $('#q').value.trim();
  if (q) params.set('q', q);
  const adv = {
    author: $('#f-author').value.trim(),
    year: $('#f-year').value.trim(),
    clc: $('#f-clc').value.trim(),
    publisher: $('#f-publisher').value.trim(),
    type: $('#f-type').value.trim(),
  };
  for (const [k, v] of Object.entries(adv)) if (v) params.set(k, v);
  if ($('#f-hasfile').checked) params.set('has_file', '1');

  try {
    const r = await api(`/api/search?${params.toString()}`);
    renderRows(r.items);
    $('#list-count').textContent = `匹配 ${r.items.length} 条`;
  } catch (err) { toast(err.message, true); }
}

function clearFilters() {
  $('#q').value = '';
  ['f-author', 'f-year', 'f-clc', 'f-publisher', 'f-type'].forEach(id => { $('#' + id).value = ''; });
  $('#f-hasfile').checked = false;
  loadList();
}

/* ══════════════════════════════════════════════════════ 绑定 */
function bind() {
  $('#btn-new').onclick = blankDetail;
  $('#btn-import').onclick = pickImportFile;
  $('#btn-export').onclick = exportData;
  $('#btn-enrich').onclick = doEnrich;
  $('#btn-cite').onclick = showCite;
  $('#btn-rename').onclick = showRename;
  $('#btn-delete').onclick = deleteBook;
  $('#btn-clear').onclick = clearFilters;
  $('#detail-form').onsubmit = saveBook;
  $('#modal-close').onclick = closeModal;
  $('#modal').onclick = (e) => { if (e.target.id === 'modal') closeModal(); };

  $('#btn-adv').onclick = () => $('#filters').classList.toggle('hidden');
  $('#q').oninput = scheduleSearch;
  $('#q').onkeydown = (e) => { if (e.key === 'Enter') { clearTimeout(searchTimer); runSearch(); } };
  ['f-author', 'f-year', 'f-clc', 'f-publisher', 'f-type'].forEach(id => { $('#' + id).oninput = scheduleSearch; });
  $('#f-hasfile').onchange = runSearch;

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

bind();
loadMeta().then(loadList).catch(err => toast(err.message, true));
