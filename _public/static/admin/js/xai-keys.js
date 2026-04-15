let xaiAdminKey = '';
let xaiKeys = [];

const xaiById = (id) => document.getElementById(id);

function maskBoolean(enabled) {
  return enabled ? 'Enabled' : 'Disabled';
}

function resetCreateForm() {
  const keyInput = xaiById('xai-key-import-text');
  const enabledInput = xaiById('xai-key-enabled');

  if (keyInput) keyInput.value = '';
  if (enabledInput) enabledInput.checked = true;
}

async function openCreateModal() {
  const modal = xaiById('xai-key-modal');
  if (!modal) return;
  resetCreateForm();
  modal.classList.remove('hidden');
  requestAnimationFrame(() => {
    modal.classList.add('is-open');
  });
}

function closeCreateModal() {
  const modal = xaiById('xai-key-modal');
  if (!modal) return;
  modal.classList.remove('is-open');
  setTimeout(() => {
    modal.classList.add('hidden');
  }, 200);
}

function renderXAIKeys() {
  const tbody = xaiById('xai-keys-table-body');
  const loading = xaiById('loading');
  const empty = xaiById('empty-state');
  if (!tbody) return;

  if (loading) loading.classList.add('hidden');

  if (!Array.isArray(xaiKeys) || xaiKeys.length === 0) {
    tbody.replaceChildren();
    if (empty) empty.classList.remove('hidden');
    return;
  }

  if (empty) empty.classList.add('hidden');
  tbody.innerHTML = xaiKeys.map((item) => `
    <tr>
      <td class="text-left">${item.name || '-'}</td>
      <td class="text-left font-mono text-xs break-all">${item.value || ''}</td>
      <td>${maskBoolean(item.enabled)}</td>
      <td>
        <div class="flex items-center justify-center gap-2">
          <button type="button" class="geist-button-outline text-xs px-3" onclick="toggleXAIKeyEnabled('${item.id}', ${item.enabled ? 'false' : 'true'})">
            ${item.enabled ? 'Disable' : 'Enable'}
          </button>
          <button type="button" class="geist-button-danger text-xs px-3" onclick="deleteXAIKey('${item.id}')">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');
}

async function loadXAIKeys() {
  const loading = xaiById('loading');
  const empty = xaiById('empty-state');
  if (loading) loading.classList.remove('hidden');
  if (empty) empty.classList.add('hidden');

  const res = await fetch('/v1/admin/xai-keys', {
    headers: buildAuthHeaders(xaiAdminKey)
  });
  const data = await res.json();
  if (!res.ok || data.status !== 'success') {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
  }
  xaiKeys = Array.isArray(data.keys) ? data.keys : [];
  renderXAIKeys();
}

async function importXAIKeys() {
  const keyInput = xaiById('xai-key-import-text');
  const enabledInput = xaiById('xai-key-enabled');
  const payload = {
    text: keyInput ? keyInput.value : '',
    enabled: enabledInput ? enabledInput.checked : true,
  };
  const res = await fetch('/v1/admin/xai-keys/import', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...buildAuthHeaders(xaiAdminKey)
    },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok || data.status !== 'success') {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
  }
  if (keyInput) keyInput.value = '';
  if (enabledInput) enabledInput.checked = true;
  closeCreateModal();
  await loadXAIKeys();
  const imported = Number(data.imported || 0);
  showToast(`Imported ${imported} xAI keys`, 'success');
}

async function saveXAIKey() {
  return importXAIKeys();
}

async function toggleXAIKeyEnabled(keyId, enabled) {
  const res = await fetch(`/v1/admin/xai-keys/${keyId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...buildAuthHeaders(xaiAdminKey)
    },
    body: JSON.stringify({ enabled })
  });
  const data = await res.json();
  if (!res.ok || data.status !== 'success') {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
  }
  await loadXAIKeys();
}

async function deleteXAIKey(keyId) {
  const res = await fetch(`/v1/admin/xai-keys/${keyId}`, {
    method: 'DELETE',
    headers: buildAuthHeaders(xaiAdminKey)
  });
  const data = await res.json();
  if (!res.ok || data.status !== 'success') {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
  }
  await loadXAIKeys();
}

async function initXAIKeysPage() {
  xaiAdminKey = await ensureAdminKey();
  if (xaiAdminKey === null) return;
  try {
    await loadXAIKeys();
  } catch (error) {
    showToast(`Load failed: ${error.message}`, 'error');
  }
}

window.openCreateModal = openCreateModal;
window.closeCreateModal = closeCreateModal;
window.importXAIKeys = importXAIKeys;
window.saveXAIKey = saveXAIKey;
window.toggleXAIKeyEnabled = toggleXAIKeyEnabled;
window.deleteXAIKey = deleteXAIKey;
window.onload = initXAIKeysPage;
