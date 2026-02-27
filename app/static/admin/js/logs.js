// 请求日志管理

let allLogs = [];
let filteredLogs = [];

// 初始化
document.addEventListener('DOMContentLoaded', async () => {
  await refreshLogs();
});

// 刷新日志
async function refreshLogs() {
  const tableBody = document.getElementById('logs-table-body');
  const emptyEl = document.getElementById('logs-empty');
  const loadingEl = document.getElementById('logs-loading');

  tableBody.innerHTML = '';
  emptyEl.classList.add('hidden');
  loadingEl.classList.remove('hidden');

  try {
    const apiKey = await ensureAdminKey();
    if (!apiKey) return;

    const res = await fetch('/v1/admin/logs', {
      headers: buildAuthHeaders(apiKey)
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    allLogs = data.logs || [];

    updateModelFilter();
    applyFilters();
    updateStats();

  } catch (e) {
    console.error('Failed to load logs:', e);
    showToast('加载日志失败: ' + e.message, 'error');
  } finally {
    loadingEl.classList.add('hidden');
  }
}

function updateModelFilter() {
  const select = document.getElementById('filter-model');
  const currentValue = select.value;
  const models = [...new Set(allLogs.map(log => log.model).filter(Boolean))];
  select.innerHTML = '<option value="">全部</option>';
  models.forEach(model => {
    const option = document.createElement('option');
    option.value = model;
    option.textContent = model;
    select.appendChild(option);
  });
  if (currentValue && models.includes(currentValue)) {
    select.value = currentValue;
  }
}

function applyFilters() {
  const modelFilter = document.getElementById('filter-model').value;
  const statusFilter = document.getElementById('filter-status').value;
  const ipFilter = document.getElementById('filter-ip').value.toLowerCase();

  filteredLogs = allLogs.filter(log => {
    if (modelFilter && log.model !== modelFilter) return false;
    if (statusFilter === 'success' && log.status !== 200) return false;
    if (statusFilter === 'failed' && log.status === 200) return false;
    if (ipFilter && !log.ip.toLowerCase().includes(ipFilter)) return false;
    return true;
  });

  renderLogs();
}

function renderLogs() {
  const tableBody = document.getElementById('logs-table-body');
  const emptyEl = document.getElementById('logs-empty');
  tableBody.innerHTML = '';

  if (filteredLogs.length === 0) {
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');

  filteredLogs.forEach(log => {
    const tr = document.createElement('tr');
    const isSuccess = log.status === 200;
    const statusClass = isSuccess ? 'success' : 'failed';
    const statusText = isSuccess ? '成功' : `失败 (${log.status})`;
    tr.innerHTML = `
      <td class="text-left time-text">${escapeHtml(log.time || '-')}</td>
      <td class="text-left ip-text">${escapeHtml(log.ip || '-')}</td>
      <td class="text-left"><span class="model-badge">${escapeHtml(log.model || '-')}</span></td>
      <td class="text-center"><span class="status-badge ${statusClass}">${statusText}</span></td>
      <td class="text-right duration-text">${log.duration ? log.duration.toFixed(2) + 's' : '-'}</td>
      <td class="text-left error-text" title="${escapeHtml(log.error || '')}">${escapeHtml(log.error || '-')}</td>
    `;
    tableBody.appendChild(tr);
  });
}

function updateStats() {
  const total = allLogs.length;
  const success = allLogs.filter(log => log.status === 200).length;
  const failed = total - success;
  const durations = allLogs.map(log => log.duration).filter(d => typeof d === 'number');
  const avgDuration = durations.length > 0
    ? (durations.reduce((a, b) => a + b, 0) / durations.length).toFixed(2)
    : 0;

  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-success').textContent = success;
  document.getElementById('stat-failed').textContent = failed;
  document.getElementById('stat-avg-duration').textContent = avgDuration + 's';
}

function confirmClearLogs() {
  const dialog = document.getElementById('confirm-dialog');
  const cancelBtn = document.getElementById('confirm-cancel');
  const okBtn = document.getElementById('confirm-ok');
  dialog.showModal();
  cancelBtn.onclick = () => dialog.close();
  okBtn.onclick = async () => {
    dialog.close();
    await clearLogs();
  };
}

async function clearLogs() {
  try {
    const apiKey = await ensureAdminKey();
    if (!apiKey) return;
    const res = await fetch('/v1/admin/logs/clear', {
      method: 'POST',
      headers: { ...buildAuthHeaders(apiKey), 'Content-Type': 'application/json' }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast('日志已清空', 'success');
    allLogs = [];
    filteredLogs = [];
    renderLogs();
    updateStats();
  } catch (e) {
    console.error('Failed to clear logs:', e);
    showToast('清空日志失败: ' + e.message, 'error');
  }
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
