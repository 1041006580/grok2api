# xAI Keys Layout Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the xAI Keys admin page visually match the Token admin page, including layout, typography, buttons, table shell, empty state, and modal styling.

**Architecture:** Reuse the existing `token.html` page shell and `token.css` styling primitives instead of inventing a new xAI-specific admin visual system. Keep xAI-specific columns and actions intact, and only apply the minimum HTML/JS adjustments needed for the reused table and modal structure to render correctly in both `app` and `_public`.

**Tech Stack:** Static HTML, vanilla JavaScript, Tailwind utility classes already used in repo, shared admin CSS, pytest merge contract tests.

---

### Task 1: Add failing UI contract tests for xAI page parity

**Files:**
- Modify: `tests/merge/test_xai_key_pool_contract.py`
- Reference: `app/static/admin/pages/xai-keys.html`
- Reference: `_public/static/admin/pages/xai-keys.html`

**Step 1: Write the failing test**

Add two tests near the end of `tests/merge/test_xai_key_pool_contract.py`:

```python
def test_app_xai_keys_page_reuses_token_layout_shell():
    html = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    assert '/static/admin/css/token.css' in html
    assert 'text-2xl font-semibold tracking-tight' in html
    assert 'id="loading"' in html
    assert 'id="empty-state"' in html
    assert 'modal-overlay hidden' in html
    assert 'modal-content modal-md' in html


def test_public_xai_keys_page_reuses_token_layout_shell():
    html = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    assert '/static/admin/css/token.css' in html
    assert 'text-2xl font-semibold tracking-tight' in html
    assert 'id="loading"' in html
    assert 'id="empty-state"' in html
    assert 'modal-overlay hidden' in html
    assert 'modal-content modal-md' in html
```

Remember to add:

```python
from pathlib import Path
```

if the file does not already import it.

**Step 2: Run test to verify it fails**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: FAIL because current xAI pages do not yet include `token.css`, `loading`, `empty-state`, or token-style modal shell.

**Step 3: Commit**

Do not commit yet. Continue to implementation after the red test is confirmed.

### Task 2: Align the app xAI page shell with the token page

**Files:**
- Modify: `app/static/admin/pages/xai-keys.html`
- Modify: `app/static/admin/js/xai-keys.js`
- Reference: `app/static/admin/pages/token.html`
- Reference: `app/static/admin/css/token.css`

**Step 1: Update the page HTML to reuse token page structure**

Change `app/static/admin/pages/xai-keys.html` so it:

- includes `/static/admin/css/token.css`
- uses the same outer title section style as `token.html`
- changes the heading class to `text-2xl font-semibold tracking-tight`
- uses the same table shell pattern as token page:

```html
<div class="rounded-lg overflow-hidden bg-white mb-4 overflow-x-auto">
  <table class="geist-table min-w-[760px]">
    ...
  </table>
  <div id="loading" class="text-center py-12 text-[var(--accents-4)]">加载中...</div>
  <div id="empty-state" class="hidden table-empty">暂无 xAI Key，请先新增一条。</div>
</div>
```

- changes the create modal to token-style structure:

```html
<div id="xai-key-modal" class="modal-overlay hidden">
  <div class="modal-content modal-md" id="xai-key-modal-content">
    <div class="modal-header">
      <h3 class="modal-title">新增 xAI Key</h3>
      <button onclick="closeCreateModal()" class="modal-close">...</button>
    </div>
    ...
  </div>
</div>
```

- uses token-sized footer buttons:

```html
<button ... class="geist-button-outline text-xs px-3">取消</button>
<button ... class="geist-button text-xs px-3">保存</button>
```

**Step 2: Update the page JS to match the new shell**

Adjust `app/static/admin/js/xai-keys.js` so that:

- `openCreateModal()` removes `hidden` and then adds `is-open`
- `closeCreateModal()` removes `is-open`, then hides after a short timeout
- `renderXAIKeys()` toggles `loading` and `empty-state`
- when rows render, action buttons use token-sized button classes only, for example:

```javascript
class="geist-button-outline text-xs px-3"
class="geist-button-danger text-xs px-3"
```

Do not change API endpoints or payload structure.

**Step 3: Run test to verify app-side changes satisfy the contract**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: the app-page assertions now pass; the public-page assertions may still fail.

### Task 3: Mirror the same alignment into `_public`

**Files:**
- Modify: `_public/static/admin/pages/xai-keys.html`
- Modify: `_public/static/admin/js/xai-keys.js`
- Reference: `_public/static/admin/pages/token.html`

**Step 1: Apply the same HTML shell alignment**

Mirror the same structural changes from the app page into `_public/static/admin/pages/xai-keys.html`, preserving existing `data-i18n` attributes and English copy where already present.

**Step 2: Apply the same JS shell alignment**

Mirror the modal open/close and loading/empty-state handling into `_public/static/admin/js/xai-keys.js`, preserving current public-page text strings.

**Step 3: Run the focused contract test**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: PASS.

### Task 4: Run regression verification and commit

**Files:**
- Verify only

**Step 1: Run the relevant regression suite**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py tests\merge\test_token_contract.py -q
```

Expected: PASS with no failures.

**Step 2: Check the final diff**

Run:

```bash
git diff -- tests/merge/test_xai_key_pool_contract.py app/static/admin/pages/xai-keys.html app/static/admin/js/xai-keys.js _public/static/admin/pages/xai-keys.html _public/static/admin/js/xai-keys.js docs/plans/2026-04-09-xai-keys-layout-align-design.md docs/plans/2026-04-09-xai-keys-layout-align.md
```

Expected: only the planned UI-alignment and plan-doc files changed.

**Step 3: Commit**

```bash
git add tests/merge/test_xai_key_pool_contract.py app/static/admin/pages/xai-keys.html app/static/admin/js/xai-keys.js _public/static/admin/pages/xai-keys.html _public/static/admin/js/xai-keys.js docs/plans/2026-04-09-xai-keys-layout-align-design.md docs/plans/2026-04-09-xai-keys-layout-align.md
git commit -m "feat: align xai keys admin layout with token page"
```
