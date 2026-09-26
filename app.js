const $ = (selector) => document.querySelector(selector);
const pathInput = $('#project-path');
const scanButton = $('#scan-button');
const runButton = $('#run-button');
const emptyResults = $('#empty-results');
const resultsContent = $('#results-content');
let toastTimer;

$('#today-label').textContent = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date());

function notify(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3500);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function selectedChecks() {
  return [...document.querySelectorAll('input[name="checks"]:checked')].map((input) => input.value);
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  if (busy) {
    button.dataset.originalText = button.innerHTML;
    button.innerHTML = `<span class="run-icon">◌</span> ${label}`;
  } else if (button.dataset.originalText) {
    button.innerHTML = button.dataset.originalText;
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function showScan(scan) {
  $('#metric-files').textContent = scan.file_count;
  $('#metric-python').textContent = scan.python_file_count;
  $('#metric-tests').textContent = scan.test_file_count;
  $('#metric-issues').textContent = scan.syntax_issue_count;
  $('#metric-dirs').textContent = scan.directory_count ?? 0;
  $('#metric-size').textContent = formatSize(scan.total_size_bytes ?? 0);
  runButton.disabled = false;
  const issues = scan.syntax_issues || [];
  if (issues.length) {
    const list = issues.slice(0, 8).map((issue) => `<div class="result-item"><div class="result-item-head"><span>${escapeHtml(issue.file)}</span><span class="status-tag failed">SYNTAX</span></div><pre>${escapeHtml(issue.message)}</pre></div>`).join('');
    renderResults({ status: 'failed', checks: [{ name: 'Python syntax scan', status: 'failed', output: `${issues.length} syntax issue(s) found.`, duration_seconds: 0 }], suggestions: ['Fix the syntax errors listed above, then scan the project again.'] }, list);
  } else {
    emptyResults.classList.remove('hidden');
    resultsContent.classList.add('hidden');
    $('#result-badge').textContent = 'SCANNED';
    $('#result-badge').className = 'result-badge idle';
  }
  notify(`Scanned ${scan.python_file_count} Python file(s).`);
}

function renderResults(report, extraHtml = '') {
  emptyResults.classList.add('hidden');
  resultsContent.classList.remove('hidden');
  const badge = $('#result-badge');
  badge.textContent = report.status.toUpperCase();
  badge.className = `result-badge ${report.status}`;
  const checks = (report.checks || []).map((check) => `
    <div class="result-item">
      <div class="result-item-head"><span>${escapeHtml(check.name)}</span><span class="status-tag ${escapeHtml(check.status)}">${escapeHtml(check.status)} · ${escapeHtml(check.duration_seconds)}s</span></div>
      ${check.output ? `<pre>${escapeHtml(check.output)}${check.truncated ? '\n… output shortened' : ''}</pre>` : ''}
    </div>`).join('');
  const suggestions = (report.suggestions || []).length ? `<div class="suggestions"><strong>Next steps</strong>${report.suggestions.map(escapeHtml).join('<br>')}</div>` : '';
  resultsContent.innerHTML = `<div class="result-summary"><strong>${report.status === 'passed' ? 'Checks complete' : 'Review findings'}</strong><span>·</span><span>${escapeHtml(report.duration_seconds ?? 0)}s total</span></div>${extraHtml}${checks}${suggestions}`;
}

async function refreshHistory() {
  try {
    const { history } = await requestJson('/api/history');
    const container = $('#history-list');
    if (!history.length) {
      container.innerHTML = '<div class="history-empty">No runs yet. Your recent check reports will appear here.</div>';
      return;
    }
    container.innerHTML = history.slice(0, 6).map((run) => `
      <div class="history-row"><span class="history-dot ${run.status === 'failed' ? 'failed' : run.status === 'warning' ? 'warning' : ''}"></span>
      <span class="history-project" title="${escapeHtml(run.project_path)}">${escapeHtml(run.project_path)}</span>
      <span class="history-status ${escapeHtml(run.status)}">${escapeHtml(run.status.toUpperCase())}</span>
      <span class="history-meta">${escapeHtml(new Date(run.created_at).toLocaleString())} · ${run.checks.length} checks</span></div>`).join('');
  } catch (error) {
    notify(error.message);
  }
}

$('#project-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  setBusy(scanButton, true, 'Scanning…');
  try {
    const scan = await requestJson('/api/scan', { method: 'POST', body: JSON.stringify({ project_path: pathInput.value.trim() }) });
    showScan(scan);
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(scanButton, false);
  }
});

runButton.addEventListener('click', async () => {
  if (!pathInput.value.trim()) return notify('Enter a project folder path and scan it first.');
  const checks = selectedChecks();
  if (!checks.length) return notify('Select at least one quality check.');
  setBusy(runButton, true, 'Running checks…');
  $('#result-badge').textContent = 'RUNNING';
  $('#result-badge').className = 'result-badge running';
  try {
    const report = await requestJson('/api/run', { method: 'POST', body: JSON.stringify({ project_path: pathInput.value.trim(), checks }) });
    renderResults(report);
    await refreshHistory();
    notify(report.status === 'passed' ? 'All selected checks passed.' : 'Checks finished — review the findings.');
  } catch (error) {
    $('#result-badge').textContent = 'ERROR';
    $('#result-badge').className = 'result-badge failed';
    notify(error.message);
  } finally {
    setBusy(runButton, false);
  }
});

$('#refresh-history').addEventListener('click', refreshHistory);

requestJson('/api/status').then(() => { $('#service-status').textContent = 'Agent ready'; }).catch(() => { $('#service-status').textContent = 'Connection issue'; });
refreshHistory();
