let xaiAdminKey = '';
let xaiKeys = [];

const xaiById = (id) => document.getElementById(id);

function maskBoolean(enabled) {
  return enabled ? 'Enabled' : 'Disabled';
}

async function openCreateModal() {
  const modal = xaiById('xai-key-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
}

function closeCreateModal() {
  const modal = xaiById('xai-key-modal');
  if (!modal) return;
  modal.classList.add('hidden');
}

function renderXAIKeys() {
  const tbody = xaiById('xai-keys-table-body');
  const empty = xaiById('xai-keys-empty');
  if (!tbody) return;

  if (!Array.isArray(xaiKeys) || xaiKeys.length === 0) {
    tbody.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    return;
  }

  if (empty) empty.classList.add('hidden');
  tbody.innerHTML = xaiKeys.map((item) => `
    <tr>
      <td class="text-left px-4 py-3">${item.name || '-'}</td>
      <td class="text-left px-4 py-3 font-mono text-xs">${item.value || ''}</td>
      <td class="text-center px-4 py-3">${maskBoolean(item.enabled)}</td>
      <td class="text-center px-4 py-3">
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

async function saveXAIKey() {
  const nameInput = xaiById('xai-key-name');
  const keyInput = xaiById('xai-key-value');
  const enabledInput = xaiById('xai-key-enabled');
  const payload = {
    name: nameInput ? nameInput.value.trim() : '',
    key: keyInput ? keyInput.value.trim() : '',
    enabled: enabledInput ? enabledInput.checked : true,
  };
  const res = await fetch('/v1/admin/xai-keys', {
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
  if (nameInput) nameInput.value = '';
  if (keyInput) keyInput.value = '';
  if (enabledInput) enabledInput.checked = true;
  closeCreateModal();
  await loadXAIKeys();
  showToast('xAI Key saved', 'success');
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
window.saveXAIKey = saveXAIKey;
window.toggleXAIKeyEnabled = toggleXAIKeyEnabled;
window.deleteXAIKey = deleteXAIKey;
window.onload = initXAIKeysPage;
