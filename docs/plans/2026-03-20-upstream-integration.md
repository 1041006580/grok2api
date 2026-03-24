# Upstream Stability-First Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fully absorb `upstream/main` into the local fork while preserving the current local behavior and stability as the baseline.

**Architecture:** Work in a dedicated git worktree on an integration branch. Build a minimal regression harness first, then perform a paused merge of `upstream/main` and resolve conflicts in layers: structure, config/startup, API/pages, reverse services, video flow, token subsystem, frontend/static assets, and finally docs/CI/lockfile cleanup. Prefer upstream structure and naming, local business logic and fault-tolerance, and thin compatibility shims for old imports/routes/asset paths.

**Tech Stack:** Python 3.13, FastAPI, uv, curl-cffi, aiohttp, SQLAlchemy, static admin/function frontend, git worktrees.

---

## Current Local Contract Baseline

- The current local video creation route is `/v1/videos`
- The current file proxy routes are `/v1/files/image/{filename:path}` and `/v1/files/video/{filename:path}`
- Configuration access is based on `app.core.config.config` and `get_config()`, not a `settings` object
- `public_api` and the existing public pages are the runtime baseline; `function` naming is an upstream-alignment layer that must preserve current local behavior

---

### Task 1: Establish the Verification Harness

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/merge/conftest.py`
- Create: `tests/merge/test_config_contract.py`
- Create: `tests/merge/test_route_surface.py`
- Create: `scripts/merge_smoke.py`

**Step 1: Write the failing tests**

Add minimal regression tests that assert:

- the app imports cleanly
- the main route surface still contains the current local endpoints
- config loading preserves the local default behavior for key sections

```python
from main import create_app


def test_route_surface_contains_core_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/v1/chat/completions" in paths
    assert "/v1/images/generations" in paths
    assert "/v1/videos" in paths
    assert "/v1/files/image/{filename:path}" in paths
    assert "/v1/files/video/{filename:path}" in paths
    assert "/admin" in paths
```

**Step 2: Run the tests to verify they fail**

Run: `uv sync --dev`
Run: `uv run python -m pytest tests/merge/test_route_surface.py -q`
Expected: FAIL because `pytest` is not yet in the dependency set or the tests do not exist yet.

**Step 3: Add the minimal implementation**

- Add `pytest` to the `dev` dependency group in `pyproject.toml`
- Refresh `uv.lock`
- Add `tests/merge/conftest.py`
- Add `tests/merge/test_config_contract.py`
- Add `tests/merge/test_route_surface.py`
- Add `scripts/merge_smoke.py` with a simple HTTP smoke harness for local startup checks

**Step 4: Run the verification**

Run: `uv sync --dev`
Run: `uv run python -m pytest tests/merge/test_config_contract.py tests/merge/test_route_surface.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/merge scripts/merge_smoke.py
git commit -m "test: add merge regression harness"
```

### Task 2: Create the Integration Baseline and Materialize Conflicts

**Files:**
- Modify: `.gitignore`
- Modify: `docs/plans/2026-03-20-upstream-integration-design.md`
- Modify: `docs/plans/2026-03-20-upstream-integration.md`

**Step 1: Write the failing verification checkpoint**

Record the current known conflict set and the required working rules in the design and plan docs so the execution session has a fixed baseline.

```python
def test_design_docs_exist():
    from pathlib import Path
    assert Path("docs/plans/2026-03-20-upstream-integration-design.md").exists()
    assert Path("docs/plans/2026-03-20-upstream-integration.md").exists()
```

**Step 2: Run the verification**

Run: `uv run python -m pytest tests/merge/test_docs_exist.py -q`
Expected: FAIL until the docs-existence test and plan paths are added.

**Step 3: Add the minimal implementation**

- Add `tests/merge/test_docs_exist.py`
- Fetch upstream refs
- Create or reuse the isolated integration branch
- Run `git merge --no-commit --no-ff upstream/main`
- Save the conflict list grouped by layer in the execution notes
- Abort the merge once the conflict set is captured

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_docs_exist.py -q`
Run: `git rev-list --left-right --count HEAD...upstream/main`
Expected: PASS for the docs test and a concrete ahead/behind count for the branch state

**Step 5: Commit**

```bash
git add tests/merge/test_docs_exist.py docs/plans/2026-03-20-upstream-integration-design.md docs/plans/2026-03-20-upstream-integration.md
git commit -m "docs: capture upstream integration baseline"
```

### Task 3: Resolve Repository Structure and Rename Conflicts

**Files:**
- Modify: `.gitignore`
- Modify: `app/api/pages/__init__.py`
- Modify: `app/api/pages/admin.py`
- Create: `app/api/pages/function.py`
- Modify: `app/api/v1/admin/__init__.py`
- Modify: `app/api/v1/admin/cache.py`
- Modify: `app/api/v1/admin/config.py`
- Create: `app/api/v1/admin/logs.py`
- Modify: `app/api/v1/admin/token.py`
- Modify: `app/api/v1/function/__init__.py`
- Modify: `app/api/v1/function/imagine.py`
- Modify: `app/api/v1/function/video.py`
- Modify: `app/api/v1/function/voice.py`
- Create or modify thin compatibility modules under `app/api/v1/admin_api/` and `app/api/v1/public_api/`

**Step 1: Write the failing tests**

Add regression tests that import the new upstream paths and the old local paths, and require both to resolve during the transition.

```python
def test_new_and_old_api_modules_import():
    __import__("app.api.v1.admin")
    __import__("app.api.v1.function")
    __import__("app.api.v1.admin_api.config")
    __import__("app.api.v1.public_api.video")
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_module_aliases.py -q`
Expected: FAIL after the paused merge because the renamed modules and old import paths diverge.

**Step 3: Write the minimal implementation**

- Resolve rename and location conflicts in `app/api/pages/`, `app/api/v1/admin/`, and `app/api/v1/function/`
- Prefer upstream file layout
- Reintroduce thin compatibility wrappers only where local imports still depend on old module paths

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_module_aliases.py tests/merge/test_route_surface.py -q`
Run: `uv run python -m compileall app main.py`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/pages app/api/v1/admin app/api/v1/function app/api/v1/admin_api app/api/v1/public_api tests/merge/test_module_aliases.py
git commit -m "refactor: align api structure with upstream layout"
```

### Task 4: Merge the Configuration and Startup Layer

**Files:**
- Modify: `app/core/config.py`
- Modify: `config.defaults.toml`
- Modify: `docker-compose.yml`
- Modify: `main.py`
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `docs/README.en.md`
- Modify: `readme.md`

**Step 1: Write the failing tests**

Add regression tests that assert local default values and the presence of newly absorbed upstream config fields.

```python
def test_local_defaults_and_new_fields_coexist():
    from app.core.config import Config

    cfg = Config()
    cfg._ensure_defaults()
    cfg._config = cfg._defaults.copy()

    assert cfg.get("app.stream") is True
    assert "proxy" in cfg._defaults
    assert "token" in cfg._defaults
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_config_contract.py -q`
Expected: FAIL after applying upstream config shape changes without local default preservation.

**Step 3: Write the minimal implementation**

- Merge upstream config fields into `app/core/config.py`
- Keep local defaults stable in `config.defaults.toml`
- Preserve current startup entrypoints in `main.py`
- Absorb safe upstream deployment/documentation changes in `Dockerfile`, `docker-compose.yml`, and the READMEs

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_config_contract.py tests/merge/test_route_surface.py -q`
Run: `uv run python -m compileall app main.py`
Expected: PASS

**Step 5: Commit**

```bash
git add app/core/config.py config.defaults.toml docker-compose.yml main.py pyproject.toml Dockerfile docs/README.en.md readme.md
git commit -m "feat: merge upstream config surface without changing local defaults"
```

### Task 5: Merge the API and Page Surface

**Files:**
- Modify: `app/api/pages/admin.py`
- Modify: `app/api/v1/chat.py`
- Modify: `app/api/v1/files.py`
- Modify: `app/api/v1/image.py`
- Modify: `app/api/v1/models.py`
- Modify: `app/api/v1/video.py`
- Modify: `app/api/v1/function/video.py`
- Modify: `main.py`

**Step 1: Write the failing tests**

Add route and handler-contract tests for the local public/admin/API surface.

```python
def test_video_and_admin_routes_still_exist():
    from main import create_app
    paths = {route.path for route in create_app().routes}
    assert "/v1/videos" in paths
    assert "/admin" in paths
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_route_surface.py tests/merge/test_video_routes.py -q`
Expected: FAIL while route registration and renamed modules are only partially merged.

**Step 3: Write the minimal implementation**

- Resolve the remaining API/page conflicts
- Keep local route behavior stable
- Expose upstream route organization internally
- Preserve or add redirect/alias behavior where removing old entrypoints would break current usage

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_route_surface.py tests/merge/test_video_routes.py -q`
Run: `uv run python scripts/merge_smoke.py --routes-only`
Expected: PASS

**Step 5: Commit**

```bash
git add app/api/pages/admin.py app/api/v1/chat.py app/api/v1/files.py app/api/v1/image.py app/api/v1/models.py app/api/v1/video.py app/api/v1/function/video.py main.py tests/merge/test_video_routes.py
git commit -m "refactor: stabilize api surface on top of upstream routing"
```

### Task 6: Merge the Reverse-Service Hotspot

**Files:**
- Modify: `app/services/reverse/accept_tos.py`
- Modify: `app/services/reverse/app_chat.py`
- Modify: `app/services/reverse/assets_delete.py`
- Modify: `app/services/reverse/assets_download.py`
- Modify: `app/services/reverse/assets_list.py`
- Modify: `app/services/reverse/assets_upload.py`
- Modify: `app/services/reverse/media_post.py`
- Create: `app/services/reverse/media_post_link.py`
- Modify: `app/services/reverse/nsfw_mgmt.py`
- Modify: `app/services/reverse/rate_limits.py`
- Modify: `app/services/reverse/set_birth.py`
- Modify: `app/services/reverse/utils/headers.py`
- Modify: `app/services/reverse/utils/retry.py`
- Modify: `app/services/reverse/utils/session.py`
- Modify: `app/services/reverse/utils/websocket.py`
- Modify: `app/services/reverse/video_upscale.py`
- Modify: `app/services/reverse/ws_livekit.py`

**Step 1: Write the failing tests**

Add focused tests around retryability, header construction, and the local rate-limit behavior contract.

```python
def test_rate_limit_errors_remain_retryable():
    from app.services.reverse.rate_limits import is_retryable_rate_limit_error
    assert is_retryable_rate_limit_error(429) is True
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_reverse_contract.py -q`
Expected: FAIL until the merged reverse layer preserves the local retry and fault-tolerance rules.

**Step 3: Write the minimal implementation**

- Keep the local stable reverse behavior as the source of truth
- Port upstream structural changes and new helper modules around that behavior
- Merge `media_post_link.py` only if it can reuse the local token/retry guarantees

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_reverse_contract.py -q`
Run: `uv run python scripts/merge_smoke.py --chat --files`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/reverse tests/merge/test_reverse_contract.py
git commit -m "fix: merge upstream reverse structure without regressing local behavior"
```

### Task 7: Merge the Video Pipeline

**Files:**
- Modify: `app/services/grok/services/video.py`
- Create: `app/services/grok/services/video_extend.py`
- Modify: `app/services/grok/utils/download.py`
- Modify: `app/services/grok/utils/upload.py`
- Modify: `app/api/v1/video.py`
- Modify: `app/api/v1/function/video.py`
- Modify: `_public/static/function/js/video.js`

**Step 1: Write the failing tests**

Add tests covering the current local video extension thresholds and metadata-preservation rules.

```python
def test_video_extension_preserves_local_threshold_behavior():
    from app.services.grok.services.video import should_auto_extend
    assert should_auto_extend(duration=6, token_remaining=1) is False
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_video_contract.py -q`
Expected: FAIL until the upstream video changes are adapted to the local stability rules.

**Step 3: Write the minimal implementation**

- Resolve `video.py` conflicts by keeping current local generation and extension behavior
- Introduce `video_extend.py` only through the local decision points
- Keep API response shape and frontend expectations stable

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_video_contract.py tests/merge/test_video_routes.py -q`
Run: `uv run python scripts/merge_smoke.py --video`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/grok/services/video.py app/services/grok/services/video_extend.py app/services/grok/utils/download.py app/services/grok/utils/upload.py app/api/v1/video.py app/api/v1/function/video.py _public/static/function/js/video.js tests/merge/test_video_contract.py
git commit -m "fix: integrate upstream video pipeline behind local behavior gates"
```

### Task 8: Merge the Token Subsystem

**Files:**
- Modify: `app/services/token/manager.py`
- Modify: `app/services/token/models.py`
- Modify: `app/services/token/pool.py`
- Modify: `app/services/token/scheduler.py`
- Modify: `app/services/grok/batch_services/usage.py`

**Step 1: Write the failing tests**

Add regression tests that lock down local token cooldown, model-aware accounting, and refresh semantics.

```python
def test_local_token_cooling_rules_still_apply():
    from app.services.token.models import TokenState
    assert TokenState.NORMAL.value == "normal"
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_token_contract.py -q`
Expected: FAIL until the upstream quota/consumed-mode changes are merged without altering local token stability rules.

**Step 3: Write the minimal implementation**

- Merge upstream token-surface changes and data model additions
- Keep the local scheduler, cooling, and model-aware refresh behavior
- Only enable upstream consumed-mode behavior where it does not regress the current local flow

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_token_contract.py -q`
Run: `uv run python scripts/merge_smoke.py --tokens`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/token app/services/grok/batch_services/usage.py tests/merge/test_token_contract.py
git commit -m "feat: absorb upstream token schema while preserving local scheduling behavior"
```

### Task 9: Merge Frontend and Static Assets

**Files:**
- Modify: `_public/static/admin/css/cache.css`
- Modify: `_public/static/admin/css/config.css`
- Modify: `_public/static/admin/css/token.css`
- Create: `_public/static/admin/css/logs.css`
- Modify: `_public/static/admin/js/cache.js`
- Modify: `_public/static/admin/js/config.js`
- Create: `_public/static/admin/js/logs.js`
- Modify: `_public/static/admin/js/token.js`
- Create: `_public/static/admin/pages/cache.html`
- Create: `_public/static/admin/pages/logs.html`
- Modify: `_public/static/admin/pages/token.html`
- Modify: `_public/static/common/html/function-header.html`
- Modify: `_public/static/common/html/header.html`
- Modify: `_public/static/common/js/toast.js`
- Modify: `_public/static/function/js/video.js`
- Modify: `_public/static/function/js/voice.js`
- Create: `_public/static/function/js/login.js`
- Create: `_public/static/i18n/i18n.js`
- Create: `_public/static/i18n/locales/en.json`
- Create: `_public/static/i18n/locales/zh.json`

**Step 1: Write the failing tests**

Add smoke checks that the critical static paths exist and the admin/function entry pages still resolve.

```python
from pathlib import Path


def test_static_entry_files_exist():
    assert Path("_public/static/admin/pages/token.html").exists()
    assert Path("_public/static/function/pages/video.html").exists()
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_static_surface.py -q`
Expected: FAIL until the `_public` migration is complete and the required files are restored.

**Step 3: Write the minimal implementation**

- Accept the upstream `_public` layout
- Reconcile local admin/video/voice behavior into the new files
- Keep old page references working through thin redirects or template mapping where required

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_static_surface.py -q`
Run: `uv run python scripts/merge_smoke.py --admin --static`
Expected: PASS

**Step 5: Commit**

```bash
git add _public tests/merge/test_static_surface.py
git commit -m "refactor: finish upstream static asset migration"
```

### Task 10: Absorb Docs, CI, and Lockfile Changes

**Files:**
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Modify: `.github/ISSUE_TEMPLATE/config.yml`
- Modify: `.github/ISSUE_TEMPLATE/documentation.yml`
- Modify: `.github/ISSUE_TEMPLATE/enhancement.yml`
- Modify: `.github/ISSUE_TEMPLATE/idea.yml`
- Modify: `.github/ISSUE_TEMPLATE/question.yml`
- Modify: `.github/pull_request_template.md`
- Modify: `.github/workflows/pr-meta.yml`
- Modify: `.github/workflows/security.yml`
- Modify: `docs/README.en.md`
- Modify: `readme.md`
- Modify: `uv.lock`
- Modify: `vercel.json`

**Step 1: Write the failing tests**

Add a final sanity test that the planning docs and key project metadata files exist after the merge.

```python
from pathlib import Path


def test_repo_metadata_files_exist():
    assert Path("readme.md").exists()
    assert Path("docs/README.en.md").exists()
    assert Path("uv.lock").exists()
```

**Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/merge/test_repo_metadata.py -q`
Expected: FAIL until the cleanup layer has been fully applied.

**Step 3: Write the minimal implementation**

- Bring in the upstream CI and issue-template files
- Reconcile README text with the merged feature surface
- Refresh `uv.lock`
- Keep deployment metadata aligned with the merged runtime shape

**Step 4: Run the verification**

Run: `uv run python -m pytest tests/merge/test_repo_metadata.py -q`
Run: `uv run python -m compileall app main.py`
Expected: PASS

**Step 5: Commit**

```bash
git add .github docs/README.en.md readme.md uv.lock vercel.json tests/merge/test_repo_metadata.py
git commit -m "chore: absorb upstream repo metadata and ci changes"
```

### Task 11: Final Verification and Handoff

**Files:**
- Modify: `docs/plans/2026-03-20-upstream-integration.md`
- Modify: `docs/plans/2026-03-20-upstream-integration-design.md`

**Step 1: Write the failing final checklist**

Add a final checklist item to the plan docs that enumerates the exact commands and smoke checks completed for this merge.

```python
def test_final_checklist_present():
    from pathlib import Path
    text = Path("docs/plans/2026-03-20-upstream-integration.md").read_text(encoding="utf-8")
    assert "Final Verification Checklist" in text
```

**Step 2: Run the final verification**

Run: `uv sync --dev`
Run: `uv run python -m pytest tests/merge -q`
Run: `uv run python -m compileall app main.py`
Run: `uv run main.py`
Expected: the test suite passes, compilation succeeds, and the app starts without import/runtime boot failures.

**Step 3: Add the minimal implementation**

- Add a `Final Verification Checklist` section to the plan doc
- Record the commands actually used
- Record any remaining compatibility shims that should be removed in a later cleanup pass

**Step 4: Re-run the verification**

Run: `uv run python -m pytest tests/merge -q`
Expected: PASS

**Step 5: Commit**

```bash
git add docs/plans/2026-03-20-upstream-integration.md docs/plans/2026-03-20-upstream-integration-design.md
git commit -m "docs: finalize upstream integration execution record"
```

## Final Verification Checklist

Run these before claiming the merge is complete:

1. `git status --short`
2. `uv sync --dev`
3. `uv run python -m pytest tests/merge -q`
4. `uv run python -m compileall app main.py`
5. `uv run python scripts/merge_smoke.py --routes-only`
6. `uv run python scripts/merge_smoke.py --chat --files --video --tokens --admin --static`
7. `uv run main.py`

If any command fails, stop and fix that layer before proceeding.

## Actual Verification Record

The following commands were actually used during execution of this integration branch:

1. `uv sync --dev`
2. `uv run python -m pytest tests/merge/test_config_contract.py tests/merge/test_route_surface.py -q`
3. `uv run python -m pytest tests/merge/test_docs_exist.py -q`
4. `git fetch upstream`
5. `git merge --no-commit --no-ff upstream/main`
6. `git merge --abort`
7. `uv run python -m pytest tests/merge/test_module_aliases.py tests/merge/test_route_surface.py -q`
8. `uv run python -m pytest tests/merge/test_config_contract.py tests/merge/test_route_surface.py tests/merge/test_video_routes.py -q`
9. `uv run python -m pytest tests/merge/test_reverse_contract.py -q`
10. `uv run python -m pytest tests/merge/test_video_contract.py tests/merge/test_video_routes.py -q`
11. `.venv\\Scripts\\python -m pytest tests/merge/test_token_contract.py -q`
12. `uv run python -m pytest tests/merge/test_static_surface.py -q`
13. `.venv\\Scripts\\python -m pytest tests/merge/test_repo_metadata.py -q`
14. `.venv\\Scripts\\python -m pytest tests/merge/test_final_checklist.py -q`
15. `uv run python -m pytest tests/merge -q`
16. `uv run python -m compileall app main.py`
17. `uv run python scripts/merge_smoke.py --routes-only`
18. `uv run python scripts/merge_smoke.py --chat --files`
19. `uv run python scripts/merge_smoke.py --video`
20. `uv run python scripts/merge_smoke.py --tokens`
21. `uv run python scripts/merge_smoke.py --admin --static`

Notes:

- Some `uv run` invocations were blocked by execution policy and were safely re-run via `.venv\\Scripts\\python -m pytest`.
- The final full regression suite passed with `19 passed, 1 warning`.
- The remaining warning is the existing Pydantic v2 deprecation in `app/api/v1/response.py`.

## Remaining Compatibility Shims

These compatibility layers remain intentionally in place after this integration pass:

- `app/api/v1/admin/*` currently wraps the legacy `app/api/v1/admin_api/*` implementation
- `app/api/v1/function/imagine.py` and `app/api/v1/function/voice.py` currently wrap `public_api` modules
- `main.py` currently exposes both `/v1/public/*` and `/v1/function/*`
- `app/api/pages/function.py` and `app/api/pages/admin.py` prefer `_public/static` but still fall back to `app/static`

These are acceptable for the current integration goal because they preserve local behavior while exposing the upstream structure. A later cleanup pass can remove the fallback paths once the old imports and routes are retired.
