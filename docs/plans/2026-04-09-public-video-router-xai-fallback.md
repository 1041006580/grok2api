# Public Video Router and xAI Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix `/v1/public` so it uses the public video handlers, and add safe xAI video key fallback for retryable create-time failures while keeping poll-time requests bound to the original key.

**Architecture:** Correct the FastAPI router mount so `/v1/public` uses `public_api.router` and `/v1/function` keeps using `function_router`. Extend `XAIKeyManager` with a minimal active-key iterator and teach `XAIVideoService` to try active keys sequentially during create-time retryable upstream failures, while keeping poll-time requests pinned to the original key with only short bounded retries.

**Tech Stack:** FastAPI routing, vanilla Python service layer, aiohttp upstream calls, pytest merge contract tests.

---

### Task 1: Add failing route mapping tests

**Files:**
- Modify: `tests/merge/test_route_surface.py`
- Reference: `main.py`

**Step 1: Write the failing test**

Add two tests:

```python
def test_public_video_routes_use_public_api_module():
    app = create_app()
    route_map = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if hasattr(route, "endpoint")
    }

    assert route_map["/v1/public/video/start"] == "app.api.v1.public_api.video"
    assert route_map["/v1/public/video/sse"] == "app.api.v1.public_api.video"


def test_function_video_routes_keep_function_module():
    app = create_app()
    route_map = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if hasattr(route, "endpoint")
    }

    assert route_map["/v1/function/video/start"] == "app.api.v1.function.video"
    assert route_map["/v1/function/video/sse"] == "app.api.v1.function.video"
```

**Step 2: Run test to verify it fails**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_route_surface.py -q
```

Expected: FAIL because `/v1/public/*` currently points to the function module.

### Task 2: Add failing xAI fallback tests

**Files:**
- Modify: `tests/merge/test_xai_key_pool_contract.py`
- Reference: `app/services/grok/services/xai_video.py`
- Reference: `app/services/grok/services/xai_key_manager.py`

**Step 1: Write the failing tests**

Add focused async tests for the service:

1. create-stage fallback across keys on 429
2. poll-stage retries stay on original key

Suggested structure:

```python
def test_xai_video_service_start_generation_falls_back_to_next_key_on_retryable_error():
    ...


def test_xai_video_service_generate_retries_polling_on_same_key_without_switching():
    ...
```

Use fake key records and patch `_request_json()` or `aiohttp.ClientSession` so you can precisely control:

- first key → `UpstreamException(details={"status": 429, ...})`
- second key → success

For polling:

- `start_generation()` returns request_id once
- `get_generation()` fails with retryable status twice
- then succeeds
- assert the bound key remains the original key

**Step 2: Run the focused test file**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: FAIL because no fallback/retry logic exists yet.

### Task 3: Implement minimal active-key iteration and retryability helpers

**Files:**
- Modify: `app/services/grok/services/xai_key_manager.py`
- Modify: `app/services/grok/services/xai_video.py`

**Step 1: Add active-key iteration in manager**

In `xai_key_manager.py`, add a small helper such as:

```python
def iter_active_keys(self) -> List[XAIKeyInfo]:
    return [
        key for key in self._keys
        if key.enabled and (key.status is None or key.status == XAIKeyStatus.ACTIVE.value)
    ]
```

Keep `acquire_key()` behavior unchanged, but implement it using `iter_active_keys()` if helpful.

**Step 2: Add retryability helpers in service**

In `xai_video.py`, add small private helpers such as:

```python
@staticmethod
def _status_from_error(exc: Exception) -> Optional[int]:
    ...

@classmethod
def _is_retryable_create_error(cls, exc: Exception) -> bool:
    ...

@classmethod
def _is_retryable_poll_error(cls, exc: Exception) -> bool:
    ...
```

Retryable statuses for now:

- `429`
- `500`
- `502`
- `503`
- `504`

### Task 4: Implement create-stage key fallback

**Files:**
- Modify: `app/services/grok/services/xai_video.py`

**Step 1: Refactor request sending to accept an explicit key**

Introduce a helper that can make a request using a provided `key_record`, without permanently changing the poll-time bound key unless desired.

For example:

```python
def _headers_for(self, key_record: XAIKeyInfo) -> Dict[str, str]:
    ...
```

and let `_request_json(...)` accept an explicit `key_record`.

**Step 2: Update `start_generation()` to try active keys sequentially**

Pseudo-shape:

```python
active_keys = self._key_manager.iter_active_keys()
last_error = None
for key_record in active_keys:
    try:
        result = await self._request_json(..., key_record=key_record)
        self._key_record = key_record
        return result
    except Exception as exc:
        if not self._is_retryable_create_error(exc):
            raise
        last_error = exc
if last_error:
    raise last_error
raise ValidationException(...)
```

Important:

- only bind `self._key_record` after a successful create call
- do not silently swallow non-retryable upstream errors

**Step 3: Run tests**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: create-stage fallback test passes; polling-related test may still fail.

### Task 5: Implement poll-stage same-key bounded retries

**Files:**
- Modify: `app/services/grok/services/xai_video.py`

**Step 1: Add bounded retry loop to `get_generation()` or `generate()` polling path**

Use the bound `self._key_record` only.

Suggested defaults:

- max attempts: 3
- delays: 0.5, 1.0, 2.0 (or a simple doubling backoff)

Pseudo-shape:

```python
attempt = 0
while True:
    try:
        return await self._request_json(..., key_record=self._key_record)
    except Exception as exc:
        attempt += 1
        if not self._is_retryable_poll_error(exc) or attempt >= 3:
            raise
        await asyncio.sleep(...)
```

Do not iterate another key during polling.

**Step 2: Run focused tests**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_xai_key_pool_contract.py -q
```

Expected: PASS.

### Task 6: Fix public router mounting

**Files:**
- Modify: `main.py`

**Step 1: Import the public router**

Add:

```python
from app.api.v1.public_api import router as public_router
```

**Step 2: Correct the mounts**

Change:

```python
app.include_router(function_router, prefix="/v1/public")
app.include_router(function_router, prefix="/v1/function")
```

to:

```python
app.include_router(public_router, prefix="/v1/public")
app.include_router(function_router, prefix="/v1/function")
```

**Step 3: Run route tests**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_route_surface.py tests\merge\test_video_routes.py -q
```

Expected: PASS.

### Task 7: Run targeted regression suite and commit

**Files:**
- Verify only

**Step 1: Run all relevant merge tests**

Run:

```bash
D:\project\grok2api\.venv\Scripts\python -m pytest tests\merge\test_route_surface.py tests\merge\test_video_routes.py tests\merge\test_xai_key_pool_contract.py tests\merge\test_token_contract.py -q
```

Expected: PASS.

**Step 2: Inspect final diff**

Run:

```bash
git diff -- main.py app/services/grok/services/xai_key_manager.py app/services/grok/services/xai_video.py tests/merge/test_route_surface.py tests/merge/test_video_routes.py tests/merge/test_xai_key_pool_contract.py docs/plans/2026-04-09-public-video-router-xai-fallback-design.md docs/plans/2026-04-09-public-video-router-xai-fallback.md
```

Expected: only routing, xAI service, tests, and plan docs are changed.

**Step 3: Commit**

```bash
git add main.py app/services/grok/services/xai_key_manager.py app/services/grok/services/xai_video.py tests/merge/test_route_surface.py tests/merge/test_video_routes.py tests/merge/test_xai_key_pool_contract.py docs/plans/2026-04-09-public-video-router-xai-fallback-design.md docs/plans/2026-04-09-public-video-router-xai-fallback.md
git commit -m "fix: correct public video routing and xai fallback"
```
