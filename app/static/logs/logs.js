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
    const apiKey = await ensureApiKey();
    if (!apiKey) return;

    const res = await fetch('/api/v1/admin/logs', {
      headers: buildAuthHeaders(apiKey)
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    allLogs = data.logs || [];

    // 更新模型筛选器
    updateModelFilter();

    // 应用筛选
    applyFilters();

    // 更新统计
    updateStats();

  } catch (e) {
    console.error('Failed to load logs:', e);
    showToast('加载日志失败: ' + e.message, 'error');
  } finally {
    loadingEl.classList.add('hidden');
  }
}

// 更新模型筛选器选项
function updateModelFilter() {
  const select = document.getElementById('filter-model');
  const currentValue = select.value;

  // 获取所有唯一模型
  const models = [...new Set(allLogs.map(log => log.model).filter(Boolean))];

  // 清空并重建选项
  select.innerHTML = '<option value="">全部</option>';
  models.forEach(model => {
    const option = document.createElement('option');
    option.value = model;
    option.textContent = model;
    select.appendChild(option);
  });

  // 恢复之前的选择
  if (currentValue && models.includes(currentValue)) {
    select.value = currentValue;
  }
}

// 应用筛选
function applyFilters() {
  const modelFilter = document.getElementById('filter-model').value;
  const statusFilter = document.getElementById('filter-status').value;
  const ipFilter = document.getElementById('filter-ip').value.toLowerCase();

  filteredLogs = allLogs.filter(log => {
    // 模型筛选
    if (modelFilter && log.model !== modelFilter) {
      return false;
    }

    // 状态筛选
    if (statusFilter === 'success' && log.status !== 200) {
      return false;
    }
    if (statusFilter === 'failed' && log.status === 200) {
      return false;
    }

    // IP 筛选
    if (ipFilter && !log.ip.toLowerCase().includes(ipFilter)) {
      return false;
    }

    return true;
  });

  renderLogs();
}

// 渲染日志表格
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
      <td class="text-left">${escapeHtml(log.time || '-')}</td>
      <td class="text-left ip-text">${escapeHtml(log.ip || '-')}</td>
      <td class="text-left"><span class="model-badge">${escapeHtml(log.model || '-')}</span></td>
      <td class="text-center"><span class="status-badge ${statusClass}">${statusText}</span></td>
      <td class="text-right duration-text">${log.duration ? log.duration.toFixed(2) + 's' : '-'}</td>
      <td class="text-left error-text" title="${escapeHtml(log.error || '')}">${escapeHtml(log.error || '-')}</td>
    `;

    tableBody.appendChild(tr);
  });
}

// 更新统计信息
function updateStats() {
  const total = allLogs.length;
  const success = allLogs.filter(log => log.status === 200).length;
  const failed = total - success;

  // 计算平均耗时
  const durations = allLogs.map(log => log.duration).filter(d => typeof d === 'number');
  const avgDuration = durations.length > 0
    ? (durations.reduce((a, b) => a + b, 0) / durations.length).toFixed(2)
    : 0;

  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-success').textContent = success;
  document.getElementById('stat-failed').textContent = failed;
  document.getElementById('stat-avg-duration').textContent = avgDuration + 's';
}

// 确认清空日志
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

// 清空日志
async function clearLogs() {
  try {
    const apiKey = await ensureApiKey();
    if (!apiKey) return;

    const res = await fetch('/api/v1/admin/logs/clear', {
      method: 'POST',
      headers: {
        ...buildAuthHeaders(apiKey),
        'Content-Type': 'application/json'
      }
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

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

// HTML 转义
function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Toast 提示（如果 toast.js 未加载则使用 alert）
function showToast(message, type) {
  if (typeof window.showToast === 'function') {
    window.showToast(message, type);
  } else if (typeof toast === 'object' && typeof toast.show === 'function') {
    toast.show(message, type);
  } else {
    alert(message);
  }
}
