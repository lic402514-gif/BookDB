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
    if (m.no_library) return m;
    const root = m.library_root || '未配置';
    $('#lib-root').textContent = m.library_name
      ? `📚 ${m.library_name}　库根：${root}`
      : `库根：${root}`;
    return m;
  } catch (_) { return null; }
}

/* ═══════════════════════════════ Library 欢迎页（P0：尚未打开书库） */
function showWelcome() {
  $('#welcome').classList.remove('hidden');
  loadRecentLibraries();
}
function hideWelcome() { $('#welcome').classList.add('hidden'); }

async function loadRecentLibraries() {
  let data;
  try { data = await api('/api/library/recent'); } catch (_) { return; }
  const items = data.items || [];
  const wrap = $('#welcome-recent');
  if (!items.length) {
    wrap.classList.add('hidden');
    $('#welcome-sub').textContent = '还没有打开书库。';
    return;
  }
  $('#welcome-sub').textContent = '选择一个书库开始，或者新建一个。';
  wrap.classList.remove('hidden');
  $('#welcome-list').innerHTML = items.map((it) =>
    `<li data-path="${esc(it.path)}" class="${it.valid ? '' : 'invalid'}"
         title="${it.valid ? '点击打开' : '该目录已不是书库'}">
       <span class="nm">📚 ${esc(it.name)}</span>
       <span class="muted small">${esc(it.path)}</span>
       ${it.valid ? '' : '<span class="muted small">（已失效）</span>'}
     </li>`).join('');
  $all('#welcome-list li').forEach((li) => {
    li.onclick = () => {
      if (li.classList.contains('invalid')) { toast('该目录已不是书库，已失效', true); return; }
      openLibrary(li.dataset.path);
    };
  });
}

async function openLibrary(path) {
  try {
    const r = await api('/api/library/open', { method: 'POST', body: { path } });
    toast(`已打开书库：${r.name}`);
    setTimeout(() => location.reload(), 300);
  } catch (err) { toast(err.message, true); }
}

async function chooseAndOpenLibrary() {
  const dir = await pickFolder();
  if (!dir) return;
  let probe;
  try {
    probe = await api('/api/library/probe', { method: 'POST', body: { path: dir } });
  } catch (err) { toast(err.message, true); return; }
  if (!probe.valid) { toast(probe.reason || '该目录不是 BookDB 书库', true); return; }
  openLibrary(probe.path);
}

async function chooseAndCreateLibrary() {
  const dir = await pickFolder();
  if (!dir) return;
  let created;
  try {
    created = await api('/api/library/create', { method: 'POST', body: { path: dir } });
  } catch (err) { toast(err.message, true); return; }
  toast(`已新建书库：${created.name}`);
  openLibrary(created.path);
}

/** 启动提示条（来自 BookDB.exe 的参数解析结果），显示一次后从 URL 抹掉。 */
function showStartupNotice() {
  const notice = new URLSearchParams(location.search).get('notice');
  if (!notice) return;
  const el = $('#notice');
  el.textContent = notice;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 9000);
  history.replaceState(null, '', location.pathname);
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
  syncFileButtons();
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
  syncFileButtons();
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

/* ══════════════════════════════════════════════════════ 打开文件（系统默认程序） */
function syncFileButtons() {
  const hasFile = !!(state.current && state.current.file_path);
  $('#btn-open').disabled = !hasFile;
  $('#btn-reveal').disabled = !hasFile;
  $('#btn-open').title = hasFile ? '用系统默认程序打开该文件' : '该记录未关联文件';
  $('#btn-reveal').title = hasFile ? '在文件管理器中定位该文件' : '该记录未关联文件';
}

async function openAction(kind, okMsg) {
  if (!state.current) return;
  try {
    await api(`/api/${kind}/${state.current.id}`, { method: 'POST', body: {} });
    toast(okMsg);
  } catch (err) { toast(err.message, true); }
}

const openFile   = () => openAction('open', '已用系统默认程序打开');
const revealFile = () => openAction('reveal', '已在文件夹中定位');

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

/* ══════════════════════════════════════════════ 批量元数据补全（VLM） */
let batchResults = [];        // 逐条提取结果（含用户可能的编辑）
let batchAbort = false;       // 用户点「中止」后置位，循环下一轮退出

function doBatchEnrich(prefillDir = '') {
  openModal('批量补全元数据',
    `<label class="small">来源
       <select id="be-scope" style="width:100%">
         <option value="">全库（所有元数据不全的记录）</option>
         <option value="dir">只处理某个目录下的记录</option>
       </select>
     </label>
     <div id="be-dirwrap" class="hidden" style="margin-top:10px">
       <label class="small">目录
         <input id="be-dir" placeholder="E:\\Books" style="width:100%" value="${esc(prefillDir)}">
       </label>
       <label class="small" style="display:flex;gap:6px;align-items:center;margin-top:8px">
         <input id="be-rec" type="checkbox" checked> 递归子目录
       </label>
     </div>
     <p class="muted small" style="margin-top:10px">
       逐条定位版权页并跑本地 VLM 补齐<b>作者 / 出版社 / 年份 / ISBN</b>。<br>
       先列出待补清单，你确认后才开始；每条约 <b>6~15 秒</b>（3B 模型 CPU 推理），请耐心。<br>
       提取结果会先在表格里复核，<b>勾选后才写入</b>库。
     </p>`,
    [{ label: '取消', onClick: closeModal },
     { label: '选择目录…', onClick: async () => {
         const dir = await pickFolder();
         if (dir) { $('#be-dir').value = dir; $('#be-scope').value = 'dir'; toggleBeDir(); }
       } },
     { label: '下一步：列出待补', primary: true, onClick: () => batchEnrichPreview() }]);

  const sel = $('#be-scope');
  if (sel) sel.onchange = toggleBeDir;
  toggleBeDir();
}

function toggleBeDir() {
  const useDir = $('#be-scope') && $('#be-scope').value === 'dir';
  const wrap = $('#be-dirwrap');
  if (wrap) wrap.classList.toggle('hidden', !useDir);
}

async function batchEnrichPreview() {
  const useDir = $('#be-scope') && $('#be-scope').value === 'dir';
  const directory = useDir ? ($('#be-dir') ? $('#be-dir').value.trim() : '') : '';
  if (useDir && !directory) { toast('请填写要处理的目录', true); return; }
  const recursive = $('#be-rec') ? $('#be-rec').checked : true;

  let plan;
  try {
    plan = await api('/api/enrich/batch/preview', {
      method: 'POST', body: { directory, recursive } });
  } catch (err) { toast(err.message, true); return; }

  if (!plan.todo) {
    openModal('无需补全',
      `<p class="muted">${esc(plan.scope)}：共 ${plan.total} 条，<b>没有需要补全的记录</b>。</p>
       ${plan.unresolved.length ? `<p class="muted small">另有 ${plan.unresolved.length} 个 PDF 未匹配到记录。</p>` : ''}`,
      [{ label: '返回', onClick: () => doBatchEnrich(directory) }, { label: '关闭', onClick: closeModal }]);
    return;
  }

  const rows = plan.items.filter(it => !it.skip).map(it =>
    `<tr><td><input type="checkbox" class="be-cb" data-id="${it.id}" checked></td>
      <td>${it.id}</td><td>${esc(it.title)}</td>
      <td class="muted">${esc(it.missing.join('、'))}</td></tr>`).join('');

  openModal('待补全清单',
    `<p class="small">范围 <b>${esc(plan.scope)}</b>　共 ${plan.total} 条，
       待补 <b>${plan.todo}</b> 条
       ${plan.unresolved.length ? `　<span class="muted">${plan.unresolved.length} 个 PDF 未匹配记录</span>` : ''}</p>
     <div style="max-height:46vh;overflow:auto;margin-top:6px">
       <table class="mini">
         <thead><tr><th><input id="be-all" type="checkbox" checked></th>
           <th>#</th><th>书名</th><th>缺失字段</th></tr></thead>
         <tbody>${rows}</tbody>
       </table>
     </div>
     <p class="muted small" style="margin-top:8px">点「开始提取」后逐条跑模型，约 6~15 秒/条。</p>`,
    [{ label: '返回', onClick: () => doBatchEnrich(directory) },
     { label: '开始提取', primary: true, onClick: () => batchEnrichRun(directory, recursive) }]);

  const all = $('#be-all');
  if (all) all.onchange = () => $all('.be-cb').forEach(cb => { cb.checked = all.checked; });
}

async function batchEnrichRun(directory, recursive) {
  const ids = $all('.be-cb:checked').map(cb => Number(cb.dataset.id));
  if (!ids.length) { toast('未勾选任何记录', true); return; }

  batchResults = [];
  const render = (done, total, current, failed) => {
    const pct = Math.round((done / total) * 100);
    const box = $('#be-progress');
    if (!box) return;
    box.innerHTML =
      `<div class="small">进度 <b>${done}/${total}</b>（${pct}%）
         ${failed ? `<span style="color:#b3261e">失败 ${failed}</span>` : ''}</div>
       <div style="height:8px;background:var(--line);border-radius:4px;margin:6px 0 10px">
         <div style="height:8px;width:${pct}%;background:var(--accent);border-radius:4px"></div>
       </div>
       <p class="muted small">${esc(current || '')}</p>`;
  };

  openModal('正在提取…',
    `<div id="be-progress"></div>
     <p class="muted small">请勿关闭窗口。3B 模型逐页推理较慢，属正常现象。</p>`,
    [{ label: '中止', onClick: () => { batchAbort = true; } }]);
  batchAbort = false;
  render(0, ids.length, '准备中…', 0);

  let failed = 0;
  for (let n = 0; n < ids.length; n++) {
    if (batchAbort) break;
    const id = ids[n];
    let label = '#' + id;
    try {
      const r = await api('/api/enrich/batch/item', { method: 'POST', body: { id } });
      batchResults.push(r);
      label = `#${id} ✓ ${r.metadata.title || ''}（第 ${r.page || '?'} 页）`;
    } catch (err) {
      failed++;
      batchResults.push({ id, error: err.message, metadata: null });
      label = `#${id} ✗ ${err.message}`;
    }
    render(n + 1, ids.length, label, failed);
  }

  showBatchResults();
}

function showBatchResults() {
  const ok = batchResults.filter(r => r.metadata);
  const bad = batchResults.filter(r => !r.metadata);
  if (!ok.length) {
    openModal('提取结果',
      `<p class="muted">没有成功提取到元数据的记录。</p>` +
      bad.map(r => `<p class="small" style="color:#b3261e">#${r.id} ${esc(r.error)}</p>`).join(''),
      [{ label: '关闭', onClick: closeModal }]);
    return;
  }

  const F = ['title', 'author', 'translator', 'publisher', 'publication_place',
             'publication_year', 'edition', 'isbn'];
  const NAME = { title: '书名', author: '作者', translator: '译者', publisher: '出版社',
                 publication_place: '出版地', publication_year: '年份', edition: '版次', isbn: 'ISBN' };

  const rows = ok.map(r => {
    const cells = F.map(f => {
      const isMissing = (r.missing || []).includes(f === 'publisher' ? 'publisher_name' : f);
      return `<td><input class="be-f" data-id="${r.id}" data-field="${f}"
                 value="${esc(r.metadata[f] == null ? '' : r.metadata[f])}"
                 style="${isMissing ? 'border-color:var(--accent)' : ''}"></td>`;
    }).join('');
    return `<tr><td><input type="checkbox" class="be-write" data-id="${r.id}" checked></td>${cells}</tr>`;
  }).join('');

  openModal('复核并写入',
    `<p class="small">成功 <b>${ok.length}</b> 条${bad.length ? `，失败 <span style="color:#b3261e">${bad.length}</span> 条` : ''}。
       可直接在表格里改值，<b>取消勾选即不写入该条</b>。</p>
     <div style="max-height:48vh;overflow:auto;margin-top:6px">
       <table class="mini">
         <thead><tr><th><input id="be-wall" type="checkbox" checked></th>
           ${F.map(f => `<th>${NAME[f]}</th>`).join('')}</tr></thead>
         <tbody>${rows}</tbody>
       </table>
     </div>
     ${bad.length ? `<div class="small" style="color:#b3261e;margin-top:8px">`
        + bad.map(r => `#${r.id} ${esc(r.error)}`).join('<br>') + '</div>' : ''}`,
    [{ label: '放弃', onClick: () => { closeModal(); loadList(); } },
     { label: '写入选中', primary: true, onClick: () => batchEnrichApply() }]);

  const wall = $('#be-wall');
  if (wall) wall.onchange = () => $all('.be-write').forEach(cb => { cb.checked = wall.checked; });
}

async function batchEnrichApply() {
  const chosen = new Set($all('.be-write:checked').map(cb => Number(cb.dataset.id)));
  if (!chosen.size) { toast('未勾选任何记录', true); return; }

  const updates = [];
  for (const id of chosen) {
    const metadata = {};
    $all(`.be-f[data-id="${id}"]`).forEach(inp => {
      const v = inp.value.trim();
      if (v !== '') metadata[inp.dataset.field] = v;
    });
    if (Object.keys(metadata).length) updates.push({ id, metadata });
  }
  if (!updates.length) { toast('没有可写入的字段', true); return; }

  try {
    const r = await api('/api/enrich/batch/apply', { method: 'POST', body: { updates } });
    closeModal();
    toast(`已更新 ${r.updated.length} 条${r.failed.length ? `，失败 ${r.failed.length} 条` : ''}`);
    await loadList();
  } catch (err) { toast(err.message, true); }
}

/* ══════════════════════════════════════════════════════ 扫描建库 */
async function pickFolder() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
    return await window.pywebview.api.pick_folder();
  }
  return prompt('输入要扫描的目录（完整路径）：');
}

function doScan(prefillDir = '') {
  openModal('扫描目录建库',
    `<label class="small">目录
       <input id="scan-dir" placeholder="E:\\Books" style="width:100%" value="${esc(prefillDir)}">
     </label>
     <label class="small" style="display:flex;gap:6px;align-items:center;margin-top:10px">
       <input id="scan-rec" type="checkbox" checked> 递归子目录
     </label>
     <p class="muted small" style="margin-top:10px">
       扫描目录下的 PDF，从<b>文件名</b>解析书名 / 作者 / 版次，先预览再写入。<br>
       已入库的文件（按路径比对）自动跳过；书名 + 作者相同者标为「疑似重复」。
     </p>`,
    [{ label: '取消', onClick: closeModal },
     { label: '选择目录…', onClick: async () => {
         const dir = await pickFolder();
         if (dir) $('#scan-dir').value = dir;
       } },
     { label: '预览', primary: true, onClick: () => previewScan() }]);
  setTimeout(() => { const el = $('#scan-dir'); if (el) el.focus(); }, 50);
}

async function previewScan() {
  const dirEl = $('#scan-dir');
  const directory = dirEl ? dirEl.value.trim() : '';
  if (!directory) { toast('请填写要扫描的目录', true); return; }
  const recursive = $('#scan-rec') ? $('#scan-rec').checked : true;

  let plan;
  try {
    plan = await api('/api/scan/preview', { method: 'POST', body: { directory, recursive } });
  } catch (err) { toast(err.message, true); return; }

  if (!plan.items.length) {
    openModal('扫描结果',
      `<p class="muted">目录 <b>${esc(plan.directory)}</b> 下没有找到 PDF。</p>`,
      [{ label: '返回', onClick: () => doScan(directory) }, { label: '关闭', onClick: closeModal }]);
    return;
  }

  const rows = plan.items.map(it => {
    const st = it.error ? '<span style="color:#b3261e">错误</span>'
      : it.already ? '<span class="muted">已入库</span>'
      : it.duplicates ? '<span style="color:#b8860b">疑似重复</span>'
      : '<span style="color:#1b7a5a">新建</span>';
    const cb = it.creatable
      ? `<input type="checkbox" class="scan-cb" data-idx="${it.index}" checked>`
      : '<input type="checkbox" disabled>';
    return `<tr><td>${cb}</td><td>${it.index}</td><td>${st}</td>
      <td>${esc(it.title)}</td><td>${esc(it.author)}</td><td>${esc(it.year)}</td>
      <td class="muted">${esc(it.error || it.name)}</td></tr>`;
  }).join('');

  openModal('扫描预览',
    `<p class="small">目录 <b>${esc(plan.directory)}</b>
       　发现 ${plan.total} 个文件（${plan.skipped} 个已入库）
       　可新建 <b>${plan.creatable}</b>
       ${plan.duplicates ? `　<span style="color:#b8860b">疑似重复 ${plan.duplicates}</span>` : ''}</p>
     <div style="max-height:46vh;overflow:auto;margin-top:6px">
       <table class="mini">
         <thead><tr><th><input id="scan-all" type="checkbox" checked title="全选 / 全不选"></th>
           <th>#</th><th>状态</th><th>书名</th><th>作者</th><th>年</th><th>文件</th></tr></thead>
         <tbody>${rows}</tbody>
       </table>
     </div>`,
    [{ label: '返回', onClick: () => doScan(directory) },
     { label: '建立记录', primary: true, onClick: () => applyScan(directory, recursive) }]);

  const all = $('#scan-all');
  if (all) all.onchange = () => $all('.scan-cb').forEach(cb => { cb.checked = all.checked; });
}

async function applyScan(directory, recursive) {
  const selected = $all('.scan-cb:checked').map(cb => Number(cb.dataset.idx));
  if (!selected.length) { toast('未勾选任何条目', true); return; }
  try {
    const r = await api('/api/scan/apply', { method: 'POST', body: { directory, recursive, selected } });
    closeModal();
    toast(`已新建 ${r.created} 条记录${r.skipped ? `，跳过 ${r.skipped} 条` : ''}`);
    await loadList();
  } catch (err) { toast(err.message, true); }
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
  $('#btn-scan').onclick = () => doScan();
  $('#btn-import').onclick = pickImportFile;
  $('#btn-export').onclick = exportData;
  $('#btn-enrich').onclick = doEnrich;
  $('#btn-enrich-all').onclick = () => doBatchEnrich();
  $('#btn-cite').onclick = showCite;
  $('#btn-rename').onclick = showRename;
  $('#btn-open').onclick = openFile;
  $('#btn-reveal').onclick = revealFile;
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

  $('#welcome-new').onclick = chooseAndCreateLibrary;
  $('#welcome-open').onclick = chooseAndOpenLibrary;
}

/** 启动流程：无书库 → 欢迎页；有书库 → 正常界面。 */
async function boot() {
  showStartupNotice();
  const meta = await loadMeta();
  if (!meta || meta.no_library) { showWelcome(); return; }
  hideWelcome();
  loadList().catch(err => toast(err.message, true));
}

bind();
boot();
