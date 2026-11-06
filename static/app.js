// FieldData AI front end (Stages 2-3). All user text is inserted with textContent, never as HTML.
const $ = id => document.getElementById(id);
const state = {user: null, project: null, dsId: null, reportSummary: null, issueFilter: {severity: '', type: ''}};
let authMode = 'login';

// ---- helpers -------------------------------------------------------------------------------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

async function api(path, method = 'GET', body) {
  const opts = {method, headers: {}};
  if (body instanceof FormData) opts.body = body;
  else if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty response */ }
  if (!res.ok) {
    if (res.status === 401 && state.user) { state.user = null; showAuth(); }
    const err = new Error(data.error || 'Something went wrong.');
    err.status = res.status;
    throw err;
  }
  return data;
}

function showError(id, msg) { const e = $(id); e.textContent = msg || ''; e.classList.toggle('d-none', !msg); }
function flash(msg) { showError('page-error', msg); if (msg) window.scrollTo(0, 0); }
function fmtSize(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB'; }
function nice(s) { return String(s).replace(/_/g, ' '); }

function makeTable(headers, rows, opts = {}) {
  const wrap = el('div', 'table-responsive');
  const t = el('table', 'table table-sm table-striped tbl mb-2');
  const head = el('tr');
  headers.forEach(h => head.append(el('th', '', h)));
  t.append(el('thead')); t.firstChild.append(head);
  const body = el('tbody');
  rows.forEach(r => {
    const tr = el('tr');
    r.forEach((v, i) => {
      const td = el('td', opts.clip ? 'cell-clip' : '');
      if (opts.tagCol === i && v) td.append(el('span', 'tag ' + v, v)); else td.textContent = v === null || v === undefined ? '' : String(v);
      if (opts.clip) td.title = String(v);
      tr.append(td);
    });
    body.append(tr);
  });
  t.append(body); wrap.append(t);
  return wrap;
}

function pager(label, hasPrev, hasNext, onPrev, onNext) {
  const bar = el('div', 'd-flex justify-content-between align-items-center small text-muted mb-3');
  bar.append(el('span', '', label));
  const btns = el('div', 'btn-group btn-group-sm');
  const prev = el('button', 'btn btn-outline-secondary', 'Previous'); prev.disabled = !hasPrev; prev.onclick = onPrev;
  const next = el('button', 'btn btn-outline-secondary', 'Next'); next.disabled = !hasNext; next.onclick = onNext;
  btns.append(prev, next); bar.append(btns);
  return bar;
}

// ---- views -----------------------------------------------------------------------------------
function showView(name) {
  ['projects', 'project', 'dataset'].forEach(v => $('view-' + v).classList.toggle('d-none', v !== name));
  flash('');
}

function showAuth() {
  ['projects', 'project', 'dataset'].forEach(v => $('view-' + v).classList.add('d-none'));
  $('userbox').classList.add('d-none');
  $('auth').classList.remove('d-none');
  $('password').value = '';
}

function showApp(user) {
  state.user = user;
  $('auth').classList.add('d-none');
  $('userbox').classList.remove('d-none');
  $('username').textContent = user.name;
  route();
}

function route() {
  if (!state.user) return;
  const h = location.hash;
  let m;
  if ((m = h.match(/^#\/project\/(\d+)$/))) showProject(Number(m[1]));
  else if ((m = h.match(/^#\/dataset\/(\d+)$/))) showDataset(Number(m[1]));
  else showProjects();
}

// ---- sign in / projects ------------------------------------------------------------------------
function setAuthMode(m) {
  authMode = m;
  $('tab-login').classList.toggle('active', m === 'login');
  $('tab-register').classList.toggle('active', m === 'register');
  $('name-row').hidden = m === 'login';
  $('pw-hint').hidden = m === 'login';
  $('auth-submit').textContent = m === 'login' ? 'Sign in' : 'Create account';
  $('password').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  showError('auth-error', '');
}

async function showProjects() {
  showView('projects');
  try {
    const list = await api('/api/projects');
    const box = $('projects');
    box.replaceChildren();
    $('empty').classList.toggle('d-none', list.length > 0);
    list.forEach(p => {
      const card = el('div', 'card project-card shadow-sm');
      const body = el('div', 'card-body d-flex justify-content-between gap-3');
      const text = el('div');
      text.append(el('h3', 'mb-1', p.name),
        el('div', 'small text-muted', [p.location, 'created ' + p.created_at.slice(0, 10)].filter(Boolean).join(' · ')));
      if (p.description) text.append(el('p', 'small mb-0 mt-2', p.description));
      const btns = el('div', 'd-flex gap-2 align-self-start');
      const open = el('a', 'btn btn-sm btn-primary', 'Open'); open.href = '#/project/' + p.id;
      const del = el('button', 'btn btn-sm btn-outline-danger', 'Delete');
      del.onclick = async () => {
        if (!confirm('Delete project "' + p.name + '" and all its datasets?')) return;
        try { await api('/api/projects/' + p.id, 'DELETE'); showProjects(); } catch (e) { flash(e.message); }
      };
      btns.append(open, del); body.append(text, btns); card.append(body); box.append(card);
    });
  } catch (e) { flash(e.message); }
}

async function showProject(id) {
  showView('project');
  try {
    state.project = await api('/api/projects/' + id);
    $('project-title').textContent = state.project.name;
    $('project-meta').textContent = [state.project.location, 'created ' + state.project.created_at.slice(0, 10)].filter(Boolean).join(' · ');
    $('project-desc').textContent = state.project.description || '';
    showError('upload-error', '');
    await loadDatasets();
    await loadProjectSummary();
  } catch (e) { location.hash = '#/projects'; flash(e.message); }
}

async function loadProjectSummary() {
  const box = $('project-summary'); box.replaceChildren();
  try {
    const s = await api(`/api/projects/${state.project.id}/summary`);
    const t = s.totals;
    const row = el('div', 'row g-2 mb-3');
    row.append(scoreCard('Datasets', t.datasets), scoreCard('Records', t.records.toLocaleString()),
      scoreCard('Validated datasets', t.validated), scoreCard('Average quality', t.average_quality_score === null ? '—' : t.average_quality_score + '%'));
    box.append(row);
    if (s.datasets.length) box.append(makeTable(['Dataset','Records','Quality score','Cleaned score','Follow-up records'],
      s.datasets.map(d => [d.name,d.rows.toLocaleString(),d.quality_score === null ? 'Not checked' : d.quality_score + '%',
        d.cleaned_score === null ? 'Not cleaned' : d.cleaned_score + '%',d.followup_records ?? '—'])));
  } catch (e) { flash(e.message); }
}

async function loadDatasets() {
  const list = await api(`/api/projects/${state.project.id}/datasets`);
  const box = $('datasets');
  box.replaceChildren();
  $('no-datasets').classList.toggle('d-none', list.length > 0);
  list.forEach(d => {
    const card = el('div', 'card dataset-card shadow-sm');
    const body = el('div', 'card-body d-flex justify-content-between gap-3');
    const text = el('div');
    text.append(el('h3', 'mb-1', d.name),
      el('div', 'small text-muted', `${d.rows.toLocaleString()} rows × ${d.columns} columns · ${fmtSize(d.size_bytes)} · uploaded ${d.uploaded_at.slice(0, 10)}`));
    const btns = el('div', 'd-flex gap-2 align-self-start');
    const open = el('a', 'btn btn-sm btn-primary', 'Open'); open.href = '#/dataset/' + d.id;
    const del = el('button', 'btn btn-sm btn-outline-danger', 'Delete');
    del.onclick = async () => {
      if (!confirm('Delete dataset "' + d.name + '"?')) return;
      try { await api('/api/datasets/' + d.id, 'DELETE'); loadDatasets(); } catch (e) { flash(e.message); }
    };
    btns.append(open, del); body.append(text, btns); card.append(body); box.append(card);
  });
}

// ---- dataset page ------------------------------------------------------------------------------
function setTab(name) {
  document.querySelectorAll('#ds-tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  ['preview', 'columns', 'quality', 'cleaning', 'analytics'].forEach(t => $('tab-' + t).classList.toggle('d-none', t !== name));
  if (name === 'cleaning') loadCleaning();
  if (name === 'analytics') loadAnalytics();
}

async function showDataset(id) {
  showView('dataset');
  state.dsId = id;
  try {
    const info = await api('/api/datasets/' + id);
    $('ds-title').textContent = info.name;
    $('ds-meta').textContent = `${info.rows.toLocaleString()} rows × ${info.columns} columns · uploaded ${info.uploaded_at.slice(0, 10)}`;
    $('back-project').href = '#/project/' + info.project_id;
    renderColumns(info.column_details);
    setTab('preview');
    await loadPreview(0);
    await loadReport();
  } catch (e) { location.hash = '#/projects'; flash(e.message); }
}

function renderColumns(cols) {
  const box = $('tab-columns');
  box.replaceChildren();
  box.append(makeTable(['Column', 'Detected as', 'Type', 'Missing', 'Missing %', 'Unique values', 'Examples'],
    cols.map(c => [c.name, nice(c.role), c.type, c.missing, c.missing_pct + '%', c.unique, c.examples.join(', ')]), {clip: true}));
}

async function loadPreview(offset) {
  const box = $('tab-preview');
  try {
    const p = await api(`/api/datasets/${state.dsId}/preview?offset=${offset}&limit=20`);
    box.replaceChildren(makeTable(p.columns, p.rows, {clip: true}));
    const from = p.total ? p.offset + 1 : 0, to = p.offset + p.rows.length;
    box.append(pager(`Rows ${from}–${to} of ${p.total.toLocaleString()}`, p.offset > 0, to < p.total,
      () => loadPreview(Math.max(0, p.offset - p.limit)), () => loadPreview(p.offset + p.limit)));
  } catch (e) { flash(e.message); }
}

// ---- data quality tab --------------------------------------------------------------------------
async function loadReport() {
  const r = await api(`/api/datasets/${state.dsId}/validation`);
  if (r.ran) { renderReport(r); return; }
  const box = $('tab-quality');
  box.replaceChildren();
  const card = el('div', 'card shadow-sm'); const body = el('div', 'card-body');
  body.append(el('p', '', 'This dataset has not been checked yet. The check looks for missing values, duplicates, invalid entries, impossible values, bad GPS coordinates and inconsistent answers.'));
  body.append(runButton('Run data quality check'));
  card.append(body); box.append(card);
}

function runButton(label) {
  const b = el('button', 'btn btn-primary', label);
  b.onclick = async () => {
    b.disabled = true; b.textContent = 'Checking… this can take a few seconds';
    try { renderReport(await api(`/api/datasets/${state.dsId}/validate`, 'POST')); }
    catch (e) { flash(e.message); b.disabled = false; b.textContent = label; }
  };
  return b;
}

function scoreCard(label, value, cls = '') {
  const c = el('div', 'col-6 col-md-3'); const card = el('div', 'card score-card shadow-sm h-100'); const body = el('div', 'card-body');
  body.append(el('div', 'big ' + cls, value), el('div', 'label', label)); card.append(body); c.append(card);
  return c;
}

function renderReport(r) {
  const s = r.summary, box = $('tab-quality');
  state.reportSummary = s;
  state.issueFilter = {severity: '', type: ''};
  box.replaceChildren();
  const scoreCls = s.quality_score >= 90 ? 'text-success' : s.quality_score >= 70 ? 'text-warning' : 'text-danger';
  const row = el('div', 'row g-3 mb-3');
  row.append(scoreCard('Data quality score (records with no errors)', s.quality_score + '%', scoreCls),
    scoreCard('Records with errors', s.records_with_errors.toLocaleString(), 'text-danger'),
    scoreCard('Records with warnings only', s.records_with_warnings_only.toLocaleString(), 'text-warning'),
    scoreCard('Cells filled in (completeness)', s.completeness_pct + '%'));
  box.append(row);
  const rerun = el('div', 'd-flex align-items-center gap-3 mb-3');
  rerun.append(runButton('Run again'), el('span', 'small text-muted', r.run_at ? 'Last checked ' + r.run_at : ''));
  box.append(rerun);

  const recs = el('div', 'card shadow-sm mb-3'); const rb = el('div', 'card-body');
  rb.append(el('h3', 'h6', 'Recommendations'));
  const ul = el('ul', 'mb-0 small'); r.recommendations.forEach(t => ul.append(el('li', '', t))); rb.append(ul); recs.append(rb); box.append(recs);

  const types = Object.entries(s.issues_by_type).sort((a, b) => b[1] - a[1]);
  const tcard = el('div', 'card shadow-sm mb-3'); const tb = el('div', 'card-body');
  tb.append(el('h3', 'h6', 'Problems by type'));
  tb.append(types.length ? makeTable(['Problem type', 'Count'], types.map(([k, v]) => [nice(k), v])) : el('p', 'small text-muted mb-0', 'No problems found.'));
  tcard.append(tb); box.append(tcard);

  if (r.enumerators && r.enumerators.length) {
    const ecard = el('div', 'card shadow-sm mb-3'); const eb = el('div', 'card-body');
    eb.append(el('h3', 'h6', 'Enumerators (highest error rate first)'));
    eb.append(makeTable(['Enumerator', 'Records', 'Records with errors', 'Error rate %', 'Most common problem'],
      r.enumerators.slice(0, 10).map(e => [e.enumerator, e.records, e.records_with_errors, e.error_rate_pct + '%', nice(e.most_common_problem)])));
    ecard.append(eb); box.append(ecard);
  }

  const icard = el('div', 'card shadow-sm mb-3'); const ib = el('div', 'card-body');
  ib.append(el('h3', 'h6', 'Records needing attention'));
  const filters = el('div', 'd-flex flex-wrap gap-2 mb-2');
  const sev = el('select', 'form-select form-select-sm w-auto'); sev.setAttribute('aria-label', 'Filter by severity');
  [['', 'All severities'], ['error', 'Errors only'], ['warning', 'Warnings only']].forEach(([v, t]) => sev.add(new Option(t, v)));
  const typ = el('select', 'form-select form-select-sm w-auto'); typ.setAttribute('aria-label', 'Filter by problem type');
  typ.add(new Option('All problem types', ''));
  types.forEach(([k]) => typ.add(new Option(nice(k), k)));
  sev.onchange = () => { state.issueFilter.severity = sev.value; loadIssues(1); };
  typ.onchange = () => { state.issueFilter.type = typ.value; loadIssues(1); };
  filters.append(sev, typ);
  const table = el('div'); table.id = 'issues-table';
  ib.append(filters, table); icard.append(ib); box.append(icard);
  loadIssues(1);
}

async function loadIssues(page) {
  const f = state.issueFilter, box = $('issues-table');
  try {
    const q = new URLSearchParams({page, page_size: 25, severity: f.severity, issue_type: f.type});
    const d = await api(`/api/datasets/${state.dsId}/issues?${q}`);
    const idx = n => d.columns.indexOf(n);
    const show = ['row', 'record_id', 'enumerator', 'column', 'issue_type', 'severity', 'value', 'message', 'suggestion'];
    const rows = d.rows.map(r => show.map(c => c === 'issue_type' ? nice(r[idx(c)]) : r[idx(c)]));
    box.replaceChildren(makeTable(['Row', 'Record', 'Enumerator', 'Column', 'Problem', 'Severity', 'Value', 'What is wrong', 'Suggested fix'], rows, {tagCol: 5, clip: true}));
    box.append(pager(`${d.total.toLocaleString()} problems · page ${d.page} of ${d.pages}`, d.page > 1, d.page < d.pages,
      () => loadIssues(d.page - 1), () => loadIssues(d.page + 1)));
  } catch (e) { flash(e.message); }
}

// ---- assisted cleaning -----------------------------------------------------------------------------
async function loadCleaning() {
  const box = $('tab-cleaning');
  box.replaceChildren(el('p', 'text-muted small', 'Suggested changes are reviewed by you. The original upload is never overwritten.'));
  try {
    const report = await api(`/api/datasets/${state.dsId}/cleaning`);
    const actions = el('div', 'd-flex flex-wrap gap-2 mb-3');
    const generate = el('button', 'btn btn-primary', 'Generate suggestions');
    generate.onclick = async () => {
      generate.disabled = true; generate.textContent = 'Generating…';
      try { await api(`/api/datasets/${state.dsId}/corrections/generate`, 'POST'); await loadCleaning(); }
      catch (e) { flash(e.message); generate.disabled = false; generate.textContent = 'Generate suggestions'; }
    };
    actions.append(generate);
    box.append(actions);
    const data = await api(`/api/datasets/${state.dsId}/corrections?page=1&page_size=100`);
    if (data.total) {
      generate.textContent = 'Regenerate suggestions';
      const st = data.summary.by_status;
      actions.append(el('span', 'small text-muted align-self-center', `${data.total} suggestions · ${st.pending} pending · ${st.accepted} accepted · ${st.rejected} rejected`));
    }
    if (!data.total) { box.append(el('p', 'text-muted', 'No suggestions yet. Generate suggestions to review possible corrections.')); return; }
    const controls = el('div', 'd-flex flex-wrap gap-2 mb-2');
    const accept = el('button', 'btn btn-sm btn-success', 'Accept selected');
    const reject = el('button', 'btn btn-sm btn-outline-danger', 'Reject selected');
    const apply = el('button', 'btn btn-sm btn-primary', 'Apply accepted corrections');
    const selectedIds = () => [...box.querySelectorAll('input[data-correction]:checked')].map(x => Number(x.value));
    accept.onclick = () => reviewCorrections('accepted', selectedIds());
    reject.onclick = () => reviewCorrections('rejected', selectedIds());
    apply.onclick = async () => {
      if (!confirm('Apply accepted corrections to a separate cleaned copy? Your original file will remain unchanged.')) return;
      apply.disabled = true;
      try { const result = await api(`/api/datasets/${state.dsId}/clean`, 'POST'); flash(`Cleaning complete: ${result.changes_applied} changes applied; ${result.records_removed} records removed. Original file preserved.`); await loadCleaning(); }
      catch (e) { flash(e.message); apply.disabled = false; }
    };
    controls.append(accept, reject, apply);
    if (report.ran) ['cleaned','changes','followup'].forEach(kind => {
      const b = el('button', 'btn btn-sm btn-outline-secondary', `Download ${kind}`);
      b.onclick = () => { window.location.href = `/api/datasets/${state.dsId}/download?what=${kind}&format=csv`; };
      controls.append(b);
    });
    box.append(controls);
    const rows = data.rows.map(r => {
      const tr = el('tr');
      const td = el('td'); const cb = el('input'); cb.type='checkbox'; cb.value=r.id; cb.dataset.correction='1'; td.append(cb); tr.append(td);
      [r.row, r.record_id, r.column, r.action, r.old_value, r.new_value, r.confidence, r.status].forEach(v => tr.append(el('td','',v ?? '')));
      return tr;
    });
    const table = el('div','table-responsive'); const t=el('table','table table-sm table-striped');
    const head=el('tr'); ['Select','Row','Record','Column','Action','Old value','Suggested value','Confidence','Status'].forEach(h=>head.append(el('th','',h)));
    const thead=el('thead'); thead.append(head); const tbody=el('tbody'); rows.forEach(r=>tbody.append(r)); t.append(thead,tbody); table.append(t); box.append(table);
    if (data.total > data.rows.length) box.append(el('p','small text-muted',`Showing first ${data.rows.length} of ${data.total} suggestions. Use the API for larger review batches.`));
  } catch (e) { if (e.status !== 404) flash(e.message); }
}

async function reviewCorrections(status, ids) {
  if (!ids.length) { flash('Select at least one suggested correction first.'); return; }
  try { const r=await api(`/api/datasets/${state.dsId}/corrections/review`,'POST',{status,ids}); flash(`${r.updated} suggestions ${status}.`); await loadCleaning(); }
  catch(e) { flash(e.message); }
}

// ---- analytics -------------------------------------------------------------------------------------
async function loadAnalytics() {
  const box=$('tab-analytics'); box.replaceChildren(el('p','text-muted small','Statistics exclude invalid values and duplicate records where appropriate.'));
  const bar=el('div','d-flex flex-wrap gap-2 mb-3');
  const source=el('select','form-select form-select-sm w-auto'); source.setAttribute('aria-label','Analytics data source');
  [['original','Original data'],['cleaned','Cleaned data']].forEach(([v,t])=>source.add(new Option(t,v)));
  const refresh=el('button','btn btn-sm btn-outline-primary','Refresh analytics');
  const pdf=el('button','btn btn-sm btn-outline-success','Download PDF report');
  const xlsx=el('button','btn btn-sm btn-outline-success','Download Excel report');
  const downloadReport=(format)=>{ window.location.href=`/api/datasets/${state.dsId}/analytics/report?format=${format}&source=${source.value}`; };
  pdf.onclick=()=>downloadReport('pdf'); xlsx.onclick=()=>downloadReport('xlsx');
  bar.append(source,refresh,pdf,xlsx); box.append(bar);
  const render=async()=>{
    try {
      const a=await api(`/api/datasets/${state.dsId}/analytics?source=${source.value}`);
      box.querySelectorAll('.analytics-content').forEach(n=>n.remove());
      const content=el('div','analytics-content');
      const o=a.overview, cards=el('div','row g-3 mb-3');
      [[o.records_total,'Total records'],[o.records_used,'Records analysed'],[o.completeness_pct+'%','Completeness'],[o.duplicates_excluded,'Duplicates excluded']].forEach(([v,l])=>cards.append(scoreCard(l,v)));
      content.append(cards);
      if (a.findings?.length) { const c=el('div','card shadow-sm mb-3'), b=el('div','card-body'); b.append(el('h3','h6','Key findings')); const ul=el('ul','mb-0'); a.findings.forEach(f=>ul.append(el('li','mb-1',f))); b.append(ul); c.append(b); content.append(c); }
      if (a.trend?.daily?.length) content.append(lineChart('Submission trend','Daily submissions across the collection period',a.trend.daily));
      if (a.geography?.by_state?.values?.length) content.append(hbarChart('Submissions by state','Top states by valid submissions',a.geography.by_state.values.slice(0,10).map(x=>[x[0],x[1]])));
      const missing=a.quality?.missing_by_column||[];
      if (missing.length) content.append(hbarChart('Missing answers by column','Columns with the most missing responses',missing.map(x=>[x[0],x[1]]),{warn:true}));
      const numeric=Object.entries(a.numeric||{}).slice(0,6);
      if (numeric.length) { const c=el('div','card shadow-sm mb-3'), b=el('div','card-body'); b.append(el('h3','h6','Numeric summaries')); b.append(makeTable(['Variable','Valid values','Mean','Median','Minimum','Maximum'],numeric.map(([k,v])=>[k,v.n,v.mean,v.median,v.min,v.max]))); c.append(b); content.append(c); }
      box.append(content);
    } catch(e) { flash(e.message); }
  };
  refresh.onclick=render; source.onchange=render; await render();
}

// ---- events ---------------------------------------------------------------------------------------
$('tab-login').onclick = () => setAuthMode('login');
$('tab-register').onclick = () => setAuthMode('register');

$('auth-form').onsubmit = async e => {
  e.preventDefault();
  showError('auth-error', '');
  try {
    const email = $('email').value, password = $('password').value;
    const user = authMode === 'login'
      ? await api('/api/login', 'POST', {email, password})
      : await api('/api/register', 'POST', {name: $('name').value, email, password});
    showApp(user);
  } catch (err) { showError('auth-error', err.message); }
};

$('project-form').onsubmit = async e => {
  e.preventDefault();
  showError('project-error', '');
  try {
    await api('/api/projects', 'POST', {name: $('p-name').value, location: $('p-location').value, description: $('p-desc').value});
    $('project-form').reset();
    showProjects();
  } catch (err) { showError('project-error', err.message); }
};

$('upload-form').onsubmit = async e => {
  e.preventDefault();
  const f = $('file').files[0];
  showError('upload-error', '');
  if (!f) { showError('upload-error', 'Choose a CSV or Excel file first.'); return; }
  const btn = $('upload-btn'); btn.disabled = true; btn.textContent = 'Uploading…';
  try {
    const fd = new FormData(); fd.append('file', f);
    await api(`/api/projects/${state.project.id}/datasets`, 'POST', fd);
    $('file').value = '';
    await loadDatasets();
  } catch (err) { showError('upload-error', err.message); }
  finally { btn.disabled = false; btn.textContent = 'Upload'; }
};

document.querySelectorAll('#ds-tabs button').forEach(b => { b.onclick = () => setTab(b.dataset.tab); });
$('logout').onclick = async () => {
  try { await api('/api/logout', 'POST'); } catch (e) { /* ignore */ }
  state.user = null; location.hash = ''; showAuth();
};
window.addEventListener('hashchange', route);

(async () => { try { showApp(await api('/api/me')); } catch (e) { showAuth(); } })();
