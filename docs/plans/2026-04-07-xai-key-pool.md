# xAI Key 池与独立管理页 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single xAI video API key with a persistent xAI key pool, add a dedicated admin management page/API, and fix token-row refresh scroll reset in the admin token page.

**Architecture:** Introduce a dedicated `XAIKeyManager` backed by structured `xai.keys` config persistence, route all xAI video requests through that manager, and expose a separate `/admin/xai-keys` + `/v1/admin/xai-keys` management surface. Keep storage reuse pragmatic by persisting key records inside config, while mirroring the existing token admin UI/API patterns for add/remove/manual enable-disable actions.

**Tech Stack:** FastAPI, static HTML/JS admin pages, existing config persistence layer, pytest

---

### Task 1: Add failing route and static-surface tests for the xAI Keys admin entrypoints

**Files:**
- Modify: `tests/merge/test_route_surface.py`
- Modify: `tests/merge/test_static_surface.py`
- Test: `tests/merge/test_route_surface.py`
- Test: `tests/merge/test_static_surface.py`

**Step 1: Write the failing tests**

Add route-surface assertions:

```python
from main import create_app


def test_route_surface_contains_xai_keys_admin_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/admin/xai-keys" in paths
    assert "/v1/admin/xai-keys" in paths
    assert "/v1/admin/xai-keys/{key_id}" in paths
```

Add static-surface assertions:

```python
from pathlib import Path


def test_admin_surface_exposes_xai_keys_page_and_nav():
    header = Path("_public/static/common/html/header.html").read_text(encoding="utf-8")
    public_page = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    function_page = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")

    assert "/admin/xai-keys" in header
    assert "xAI Keys" in public_page
    assert "xAI Keys" in function_page
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_route_surface.py tests/merge/test_static_surface.py -q`
Expected: FAIL because the page route, API route, and static files do not exist yet.

**Step 3: Write the minimal implementation**

- Add `/admin/xai-keys` page routing in `app/api/pages/admin.py`
- Register placeholder admin API routing in `app/api/v1/admin_api/__init__.py` and `app/api/v1/admin/__init__.py`
- Add placeholder static pages in both `app/static/admin/pages/xai-keys.html` and `_public/static/admin/pages/xai-keys.html`
- Add a new header nav link in both header HTML files

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_route_surface.py tests/merge/test_static_surface.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_route_surface.py tests/merge/test_static_surface.py app/api/pages/admin.py app/api/v1/admin_api/__init__.py app/api/v1/admin/__init__.py app/static/common/html/header.html _public/static/common/html/header.html app/static/admin/pages/xai-keys.html _public/static/admin/pages/xai-keys.html
git commit -m "feat: add xai keys admin route surface"
```

### Task 2: Add failing contract tests for the xAI key manager and config-backed persistence

**Files:**
- Create: `tests/merge/test_xai_key_pool_contract.py`
- Create: `app/services/grok/services/xai_key_manager.py`
- Modify: `config.defaults.toml`
- Test: `tests/merge/test_xai_key_pool_contract.py`

**Step 1: Write the failing tests**

Create manager contract tests that lock in the config schema and basic selection behavior:

```python
from app.services.grok.services.xai_key_manager import XAIKeyManager, XAIKeyStatus


def test_xai_key_manager_loads_from_xai_keys_config():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-key-1", "name": "key-1", "enabled": True},
                    {"id": "k2", "key": "xai-key-2", "name": "key-2", "enabled": False},
                ]
            }
        }
    )

    items = manager.list_keys()
    assert [item.id for item in items] == ["k1", "k2"]
    assert items[0].enabled is True
    assert items[1].enabled is False


def test_xai_key_manager_selects_only_enabled_keys():
    manager = XAIKeyManager.from_config(
        {"xai": {"keys": [{"id": "k1", "key": "xai-key-1", "enabled": False}]}}
    )
    assert manager.acquire_key() is None
```

Add a defaults assertion:

```python
from pathlib import Path


def test_config_defaults_exposes_xai_keys_pool():
    config_text = Path("config.defaults.toml").read_text(encoding="utf-8")
    assert "keys = []" in config_text
    assert "api_key =" not in config_text
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_xai_key_pool_contract.py -q`
Expected: FAIL because the manager and new config shape do not exist yet.

**Step 3: Write the minimal implementation**

- Create `app/services/grok/services/xai_key_manager.py` with:
  - `XAIKeyStatus`
  - `XAIKeyInfo`
  - `XAIKeyManager`
  - `from_config()`
  - `list_keys()`
  - `acquire_key()`
- Replace `xai.api_key` in `config.defaults.toml` with `xai.keys = []`

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_xai_key_pool_contract.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_xai_key_pool_contract.py app/services/grok/services/xai_key_manager.py config.defaults.toml
git commit -m "feat: add config-backed xai key manager"
```

### Task 3: Add failing admin API tests for xAI key CRUD and manual enable-disable

**Files:**
- Modify: `tests/test_upstream_low_risk_merge.py`
- Create: `app/api/v1/admin_api/xai_keys.py`
- Create: `app/api/v1/admin/xai_keys.py`
- Modify: `app/api/v1/admin_api/__init__.py`
- Modify: `app/api/v1/admin/__init__.py`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing tests**

Add targeted API tests:

```python
class XAIKeysAdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_xai_keys_get_returns_masked_keys(self):
        from app.api.v1.admin_api.xai_keys import get_xai_keys

        response = await get_xai_keys()
        self.assertIn("keys", response)

    async def test_admin_xai_keys_patch_can_toggle_enabled(self):
        from app.api.v1.admin_api.xai_keys import update_xai_key

        payload = await update_xai_key("k1", {"enabled": False})
        self.assertEqual(payload["status"], "success")
```

Also add import-level route smoke assertions if needed:

```python
def test_admin_api_router_includes_xai_keys_module():
    import app.api.v1.admin_api.xai_keys  # noqa: F401
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "XAIKeysAdminApiTests or xai_keys_module" -q`
Expected: FAIL because the API module and handlers do not exist yet.

**Step 3: Write the minimal implementation**

- Create `app/api/v1/admin_api/xai_keys.py` with:
  - `get_xai_keys`
  - `create_xai_key`
  - `update_xai_key`
  - `delete_xai_key`
- Create `app/api/v1/admin/xai_keys.py` as the alias module exporting the same router
- Include the router in both admin `__init__` modules
- Return masked keys from `GET`
- Support `enabled` and `name` updates from `PATCH`

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "XAIKeysAdminApiTests or xai_keys_module" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_upstream_low_risk_merge.py app/api/v1/admin_api/xai_keys.py app/api/v1/admin/xai_keys.py app/api/v1/admin_api/__init__.py app/api/v1/admin/__init__.py
git commit -m "feat: add xai keys admin api"
```

### Task 4: Add failing tests for xAI video runtime integration to use the key pool instead of `xai.api_key`

**Files:**
- Modify: `tests/merge/test_xai_key_pool_contract.py`
- Modify: `tests/test_upstream_low_risk_merge.py`
- Modify: `app/services/grok/services/xai_video.py`
- Modify: `app/api/v1/video.py`
- Test: `tests/merge/test_xai_key_pool_contract.py`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing tests**

Add a unit-level service test:

```python
async def test_xai_video_service_builds_headers_from_manager_key():
    from app.services.grok.services.xai_video import XAIVideoService

    service = XAIVideoService()
    service._key_record = type("KeyRef", (), {"key": "xai-key-1"})()
    headers = service._headers()
    assert headers["Authorization"] == "Bearer xai-key-1"
```

Add route-level guard tests:

```python
async def test_videos_route_requires_available_xai_key_pool(self):
    from app.api.v1.video import create_video

    class FakeRequest:
        headers = {"content-type": "application/json"}
        async def json(self):
            return {"model": "grok-imagine-video", "prompt": "test"}

    with self.assertRaises(Exception) as ctx:
        await create_video(FakeRequest())

    self.assertEqual(getattr(ctx.exception, "code", None), "xai_api_key_missing")
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_xai_key_pool_contract.py tests/test_upstream_low_risk_merge.py -k "xai key pool or xai video service or available_xai_key_pool" -q`
Expected: FAIL because `XAIVideoService` still reads `xai.api_key` directly.

**Step 3: Write the minimal implementation**

- Inject `XAIKeyManager` into `XAIVideoService`
- Replace direct `get_config("xai.api_key", ...)` reads with manager acquisition
- Add a clear “no available xAI key” path in `app/api/v1/video.py`
- Preserve the existing xAI duration and image-reference validation
- Bind the selected key for create + poll in the service instance

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_xai_key_pool_contract.py tests/test_upstream_low_risk_merge.py -k "xai key pool or xai video service or available_xai_key_pool" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_xai_key_pool_contract.py tests/test_upstream_low_risk_merge.py app/services/grok/services/xai_video.py app/api/v1/video.py
git commit -m "feat: route xai video generation through key pool"
```

### Task 5: Add failing tests for function/public video routes to accept the pooled xAI model path

**Files:**
- Modify: `tests/merge/test_video_contract.py`
- Modify: `app/api/v1/function/video.py`
- Modify: `app/api/v1/public_api/video.py`
- Test: `tests/merge/test_video_contract.py`

**Step 1: Write the failing tests**

Add or extend tests that assert:

```python
async def test_function_video_sse_uses_xai_service_for_grok_imagine_video():
    ...
    assert "grok-imagine-video" == session["model"]


async def test_public_video_sse_uses_xai_service_for_grok_imagine_video():
    ...
    assert "grok-imagine-video" == session["model"]
```

Also lock in the pool-backed empty-key behavior:

```python
async def test_public_video_start_rejects_xai_mode_when_pool_empty():
    ...
    assert response.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_video_contract.py -q`
Expected: FAIL because the function/public routes still rely on old single-key assumptions.

**Step 3: Write the minimal implementation**

- Update `VideoStartRequest` flow to validate xAI mode against the new manager-backed availability checks
- Keep legacy SSO flow unchanged
- Keep xAI mode in the function/public SSE endpoints routed through `XAIVideoService`
- Remove any remaining `xai.api_key` checks in those route modules

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_video_contract.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_video_contract.py app/api/v1/function/video.py app/api/v1/public_api/video.py
git commit -m "feat: support pooled xai keys in function and public video routes"
```

### Task 6: Add failing tests for the independent xAI Keys admin page and navigation

**Files:**
- Modify: `tests/merge/test_static_surface.py`
- Modify: `tests/test_upstream_low_risk_merge.py`
- Modify: `app/static/common/html/header.html`
- Modify: `_public/static/common/html/header.html`
- Modify: `app/static/admin/pages/xai-keys.html`
- Modify: `_public/static/admin/pages/xai-keys.html`
- Create: `app/static/admin/js/xai-keys.js`
- Create: `_public/static/admin/js/xai-keys.js`
- Test: `tests/merge/test_static_surface.py`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing tests**

Add static-page assertions:

```python
def test_xai_keys_page_exposes_admin_table_and_actions():
    html = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    js = Path("_public/static/admin/js/xai-keys.js").read_text(encoding="utf-8")
    assert 'id="xai-keys-table-body"' in html
    assert "fetch('/v1/admin/xai-keys'" in js
    assert "async function openCreateModal()" in js
```

Add a public/app mirror assertion:

```python
def test_app_xai_keys_page_exposes_admin_table_and_actions():
    html = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    js = Path("app/static/admin/js/xai-keys.js").read_text(encoding="utf-8")
    assert 'id="xai-keys-table-body"' in html
    assert "async function saveXAIKey()" in js
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py tests/test_upstream_low_risk_merge.py -k "xai keys page" -q`
Expected: FAIL because the page and JS are still placeholders.

**Step 3: Write the minimal implementation**

- Add full page markup in both HTML files
- Add JS in both static trees to:
  - load `/v1/admin/xai-keys`
  - create keys
  - toggle enabled state
  - delete keys
- Keep the UI intentionally close to the token admin page

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py tests/test_upstream_low_risk_merge.py -k "xai keys page" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_static_surface.py tests/test_upstream_low_risk_merge.py app/static/common/html/header.html _public/static/common/html/header.html app/static/admin/pages/xai-keys.html _public/static/admin/pages/xai-keys.html app/static/admin/js/xai-keys.js _public/static/admin/js/xai-keys.js
git commit -m "feat: add xai keys admin page"
```

### Task 7: Add failing tests for token-row refresh scroll preservation

**Files:**
- Modify: `tests/merge/test_static_surface.py`
- Modify: `app/static/admin/js/token.js`
- Modify: `_public/static/admin/js/token.js`
- Test: `tests/merge/test_static_surface.py`

**Step 1: Write the failing tests**

Add string-level regression checks:

```python
def test_token_admin_scripts_preserve_scroll_position_on_row_refresh():
    app_js = Path("app/static/admin/js/token.js").read_text(encoding="utf-8")
    public_js = Path("_public/static/admin/js/token.js").read_text(encoding="utf-8")
    assert "captureScrollPosition(" in app_js
    assert "restoreScrollPosition(" in app_js
    assert "await loadData({ preserveScroll: true" in app_js
    assert "captureScrollPosition(" in public_js
    assert "restoreScrollPosition(" in public_js
    assert "await loadData({ preserveScroll: true" in public_js
```

**Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py -q`
Expected: FAIL because the token scripts currently reload data without preserving scroll.

**Step 3: Write the minimal implementation**

- Add `captureScrollPosition()` and `restoreScrollPosition()` helpers in both token scripts
- Update `loadData()` to accept a `preserveScroll` option
- Change row refresh success paths to `await loadData({ preserveScroll: true })`

**Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_static_surface.py app/static/admin/js/token.js _public/static/admin/js/token.js
git commit -m "fix: preserve token admin scroll position after row refresh"
```

### Task 8: Full verification

**Files:**
- Modify: none
- Test: `tests/merge/test_route_surface.py`
- Test: `tests/merge/test_static_surface.py`
- Test: `tests/merge/test_xai_key_pool_contract.py`
- Test: `tests/merge/test_video_contract.py`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Run targeted verification**

Run:

```bash
.venv\\Scripts\\python -m pytest tests/merge/test_route_surface.py tests/merge/test_static_surface.py tests/merge/test_xai_key_pool_contract.py tests/merge/test_video_contract.py -q
.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -q
```

Expected: PASS

**Step 2: Run broader verification**

Run:

```bash
uv run python -m pytest tests/merge -q
uv run python -m compileall app main.py
```

Expected: PASS

**Step 3: Optional manual verification**

Verify in browser:

1. Open `/admin/xai-keys`
2. Add two xAI keys
3. Disable one key and confirm it becomes unavailable
4. Trigger an xAI video generation path and confirm a key is selected from the pool
5. Re-open `/admin/xai-keys` and confirm status changes persist
6. Open `/admin/token`, scroll deep down, click a row refresh button, and confirm the scroll position stays near the same row

**Step 4: Commit final verification-safe state**

```bash
git add app/api/pages/admin.py app/api/v1/admin_api/__init__.py app/api/v1/admin/__init__.py app/api/v1/admin_api/xai_keys.py app/api/v1/admin/xai_keys.py app/services/grok/services/xai_key_manager.py app/services/grok/services/xai_video.py app/api/v1/video.py app/api/v1/function/video.py app/api/v1/public_api/video.py app/static/common/html/header.html _public/static/common/html/header.html app/static/admin/pages/xai-keys.html _public/static/admin/pages/xai-keys.html app/static/admin/js/xai-keys.js _public/static/admin/js/xai-keys.js app/static/admin/js/token.js _public/static/admin/js/token.js config.defaults.toml tests/merge/test_route_surface.py tests/merge/test_static_surface.py tests/merge/test_xai_key_pool_contract.py tests/merge/test_video_contract.py tests/test_upstream_low_risk_merge.py
git commit -m "feat: add xai key pool and admin management"
```
