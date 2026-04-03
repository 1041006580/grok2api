# xAI Video 页面接入 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `grok-imagine-video (xAI API)` as a selectable option in both public and function video pages while keeping the existing SSO video flow unchanged.

**Architecture:** Keep the current public/function page split, add a shared conceptual model selector to both UIs, and branch client-side submission logic by selected model. Old video models continue using the existing start/SSE endpoints; the new xAI model uses the existing `/v1/videos` completed-response endpoint with a dedicated payload mapper.

**Tech Stack:** FastAPI, static HTML/JS pages, existing `/v1/videos` backend route, pytest

---

### Task 1: Add failing tests for xAI model option in both video pages

**Files:**
- Modify: `tests/merge/test_static_surface.py`
- Test: `tests/merge/test_static_surface.py`

**Step 1: Write the failing test**

```python
from pathlib import Path


def test_video_pages_expose_xai_video_model_option():
    function_page = Path("_public/static/function/pages/video.html").read_text(encoding="utf-8")
    public_page = Path("app/static/public/pages/video.html").read_text(encoding="utf-8")
    assert "grok-imagine-video" in function_page
    assert "grok-imagine-video" in public_page
```

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py -q`
Expected: FAIL because neither page exposes the new model option yet.

**Step 3: Write minimal implementation**

- Add a model selector to both video HTML pages
- Include `grok-imagine-video (xAI API)` as an option

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/merge/test_static_surface.py _public/static/function/pages/video.html app/static/public/pages/video.html
git commit -m "feat: expose xai video model in video pages"
```

### Task 2: Add failing tests for xAI mode UI rule switching

**Files:**
- Modify: `tests/test_upstream_low_risk_merge.py`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing test**

```python
class VideoPageModelRulesTests(unittest.TestCase):
    def test_function_video_page_mentions_xai_duration_limit(self):
        from pathlib import Path
        html = Path("_public/static/function/pages/video.html").read_text(encoding="utf-8")
        assert "1-15" in html
```
```

Add a companion assertion for the public page or JS if that is where the rule text lives.

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "VideoPageModelRulesTests" -q`
Expected: FAIL because the pages do not describe xAI-specific constraints yet.

**Step 3: Write minimal implementation**

- Add hint text and/or JS constants for xAI mode rules
- Ensure the pages communicate `1-15s`, single image reference, and xAI API behavior

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "VideoPageModelRulesTests" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_upstream_low_risk_merge.py _public/static/function/pages/video.html app/static/public/pages/video.html _public/static/function/js/video.js app/static/public/js/video.js
git commit -m "feat: describe xai video page constraints"
```

### Task 3: Implement function-page model selector and state switching

**Files:**
- Modify: `_public/static/function/pages/video.html`
- Modify: `_public/static/function/js/video.js`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing test**

Add a targeted test that inspects the function page JS/HTML for:

- a model selector element
- xAI model id support
- xAI duration bound of 15 seconds

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "function video page" -q`
Expected: FAIL

**Step 3: Write minimal implementation**

- Add a model selector to the function page
- Add JS constants for:
  - legacy models
  - `grok-imagine-video`
  - xAI-specific min/max duration
- Update displayed metadata when model changes
- Hide/disable controls that do not apply to the xAI route

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "function video page" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add _public/static/function/pages/video.html _public/static/function/js/video.js tests/test_upstream_low_risk_merge.py
git commit -m "feat: add xai video mode to function page"
```

### Task 4: Implement public-page model selector and state switching

**Files:**
- Modify: `app/static/public/pages/video.html`
- Modify: `app/static/public/js/video.js`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing test**

Add the public-page equivalent assertions:

- model selector exists
- xAI model option exists
- xAI rules are represented in page/JS behavior markers

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "public video page" -q`
Expected: FAIL

**Step 3: Write minimal implementation**

- Mirror the function-page selector and model-switch behavior
- Keep public-page auth handling unchanged
- Keep legacy SSO flow untouched

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "public video page" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add app/static/public/pages/video.html app/static/public/js/video.js tests/test_upstream_low_risk_merge.py
git commit -m "feat: add xai video mode to public page"
```

### Task 5: Add failing tests for xAI submission path on the function page

**Files:**
- Modify: `tests/test_upstream_low_risk_merge.py`
- Modify: `_public/static/function/js/video.js`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing test**

Add a test that validates the function-page JS now has a branch that:

- detects `grok-imagine-video`
- sends requests to `/v1/videos`
- maps page inputs into `{model, prompt, size, seconds, quality, image_reference}`

Use string-level assertions if there is no JS test harness in the repo.

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "xai submission path function" -q`
Expected: FAIL

**Step 3: Write minimal implementation**

- Add a dedicated function-page request builder for xAI mode
- Keep legacy task creation flow intact
- For xAI mode, bypass `/v1/function/video/start` and call `/v1/videos`
- Render the returned final URL directly in preview

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "xai submission path function" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add _public/static/function/js/video.js tests/test_upstream_low_risk_merge.py
git commit -m "feat: route function video xai mode through videos api"
```

### Task 6: Add failing tests for xAI submission path on the public page

**Files:**
- Modify: `tests/test_upstream_low_risk_merge.py`
- Modify: `app/static/public/js/video.js`
- Test: `tests/test_upstream_low_risk_merge.py`

**Step 1: Write the failing test**

Add a public-page equivalent assertion set:

- xAI mode uses `/v1/videos`
- legacy mode still uses `/v1/public/video/start`
- parameter mapping is model-aware

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "xai submission path public" -q`
Expected: FAIL

**Step 3: Write minimal implementation**

- Add public-page xAI request builder and response renderer
- Preserve public auth behavior
- Preserve legacy SSE flow

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -k "xai submission path public" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add app/static/public/js/video.js tests/test_upstream_low_risk_merge.py
git commit -m "feat: route public video xai mode through videos api"
```

### Task 7: Verify backend/page compatibility

**Files:**
- Read: `app/api/v1/video.py`
- Read: `app/services/grok/services/xai_video.py`
- Test: `tests/test_upstream_low_risk_merge.py`
- Test: `tests/merge/test_static_surface.py`

**Step 1: Write the failing test**

Add or update assertions that:

- page-side model ids match backend-supported ids
- xAI duration constraints match backend constraints
- page-side image reference count assumptions stay aligned with `/v1/videos`

**Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py tests/merge/test_static_surface.py -q`
Expected: FAIL if the UI and backend contract are out of sync.

**Step 3: Write minimal implementation**

- Adjust labels, client-side validation, and payload mapping to match backend contract exactly

**Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py tests/merge/test_static_surface.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_upstream_low_risk_merge.py tests/merge/test_static_surface.py _public/static/function/pages/video.html _public/static/function/js/video.js app/static/public/pages/video.html app/static/public/js/video.js
git commit -m "test: align xai video page contract with backend"
```

### Task 8: Full verification

**Files:**
- Modify: none
- Test: `tests/test_upstream_low_risk_merge.py`
- Test: `tests/merge/test_static_surface.py`
- Test: `tests/merge`

**Step 1: Run targeted verification**

Run:

```bash
.venv\\Scripts\\python -m pytest tests/test_upstream_low_risk_merge.py -q
.venv\\Scripts\\python -m pytest tests/merge/test_static_surface.py -q
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

1. Open `/video` in function mode
2. Switch to `grok-imagine-video (xAI API)`
3. Confirm duration range changes to `1-15s`
4. Submit a minimal prompt
5. Confirm direct completed response renders a final URL/video
6. Switch back to legacy model and confirm SSE flow still works

Repeat the same for the public page route if enabled in that environment.

**Step 4: Commit final verification-safe state**

```bash
git add _public/static/function/pages/video.html _public/static/function/js/video.js app/static/public/pages/video.html app/static/public/js/video.js tests/test_upstream_low_risk_merge.py tests/merge/test_static_surface.py
git commit -m "feat: expose xai video generation in public and function pages"
```
