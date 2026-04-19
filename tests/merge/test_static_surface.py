from pathlib import Path


def test_static_entry_files_exist():
    assert Path("_public/static/admin/pages/token.html").exists()
    assert Path("_public/static/function/pages/video.html").exists()


def test_token_page_exposes_basic_and_super_filter_tabs():
    token_html = Path("_public/static/admin/pages/token.html").read_text(encoding="utf-8")
    token_js = Path("_public/static/admin/js/token.js").read_text(encoding="utf-8")

    assert 'data-filter="basic-token"' in token_html
    assert 'id="tab-count-basic-token"' in token_html
    assert 'data-filter="super-token"' in token_html
    assert 'id="tab-count-super-token"' in token_html

    assert "currentFilter === 'basic-token'" in token_js
    assert "currentFilter === 'super-token'" in token_js
    assert "'basic-token':" in token_js
    assert "'super-token':" in token_js


def test_video_pages_expose_xai_video_model_option():
    function_page = Path("_public/static/function/pages/video.html").read_text(encoding="utf-8")
    public_page = Path("app/static/public/pages/video.html").read_text(encoding="utf-8")

    assert "grok-imagine-video" in function_page
    assert "grok-imagine-video" in public_page


def test_video_scripts_send_selected_model_and_public_page_handles_xai_mode():
    function_js = Path("_public/static/function/js/video.js").read_text(encoding="utf-8")
    public_js = Path("app/static/public/js/video.js").read_text(encoding="utf-8")

    assert "model: modelSelect ? modelSelect.value : 'grok-imagine-1.0-video'" in function_js
    assert "model: modelSelect ? modelSelect.value : 'grok-imagine-1.0-video'" in public_js
    assert "function setLengthOptions(" in public_js
    assert "function updatePublicVideoModelState()" in public_js
    assert "const XAI_VIDEO_MODEL_ID = 'grok-imagine-video';" in public_js


def test_video_scripts_expose_moderation_rejection_ui_handling():
    function_js = Path("_public/static/function/js/video.js").read_text(encoding="utf-8")
    public_js = Path("app/static/public/js/video.js").read_text(encoding="utf-8")

    for js in (function_js, public_js):
        assert "content_moderation_rejected" in js
        assert "内容审核未通过，视频无法生成" in js


def test_video_page_css_assigns_model_hint_a_dedicated_grid_slot():
    function_css = Path("_public/static/function/css/video.css").read_text(encoding="utf-8")
    public_css = Path("app/static/public/css/video.css").read_text(encoding="utf-8")

    for css in (function_css, public_css):
        assert ".model-block {" in css
        assert "grid-column: 2 / span 2;" in css
        assert "grid-row: 3;" in css
        assert ".upload-block {" in css
        assert "grid-row: 4;" in css
        assert ".clear-block {" in css
        assert "#modelRuleHint {" in css


def test_xai_keys_page_uses_batch_import_without_name_input():
    admin_page = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    public_page = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    admin_js = Path("app/static/admin/js/xai-keys.js").read_text(encoding="utf-8")
    public_js = Path("_public/static/admin/js/xai-keys.js").read_text(encoding="utf-8")

    for html in (admin_page, public_page):
        assert 'id="xai-key-name"' not in html
        assert 'id="xai-key-edit-name"' not in html
        assert 'id="xai-key-import-text"' in html
        assert ">名称<" not in html
        assert ">Name<" not in html
        assert 'id="xai-key-edit-modal"' in html

    for js in (admin_js, public_js):
        assert "async function importXAIKeys()" in js
        assert "/v1/admin/xai-keys/import" in js
        assert "async function openEditModal(keyId)" in js
        assert "async function saveEditXAIKey()" in js


def test_xai_keys_page_defaults_to_chinese_copy():
    admin_page = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    public_page = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    public_js = Path("_public/static/admin/js/xai-keys.js").read_text(encoding="utf-8")

    for html in (admin_page, public_page):
        assert "管理 xAI 密钥" in html
        assert "导入密钥" in html
        assert "掩码值" in html
        assert "操作" in html
        assert "批量导入 xAI 密钥" in html

    assert "启用" in public_js
    assert "停用" in public_js
    assert "编辑" in public_js
    assert "删除" in public_js
    assert "已导入" in public_js


def test_video_pages_render_failed_preview_state_on_errors():
    public_js = Path("app/static/public/js/video.js").read_text(encoding="utf-8")
    function_js = Path("_public/static/function/js/video.js").read_text(encoding="utf-8")

    for js in (public_js, function_js):
        assert "function renderFailureState(message)" in js
        assert "视频生成失败，请重试" in js
        assert "连接错误，生成流中断，请重试" in js


def test_imagine_pages_expose_xai_image_model_option():
    public_page = Path("app/static/public/pages/imagine.html").read_text(encoding="utf-8")
    function_page = Path("_public/static/function/pages/imagine.html").read_text(encoding="utf-8")
    public_js = Path("app/static/public/js/imagine.js").read_text(encoding="utf-8")
    function_js = Path("_public/static/function/js/imagine.js").read_text(encoding="utf-8")

    assert "grok-imagine-image" in public_page
    assert "grok-imagine-image" in function_page
    assert "const XAI_IMAGE_MODEL_ID = 'grok-imagine-image';" in public_js
    assert "const XAI_IMAGE_MODEL_ID = 'grok-imagine-image';" in function_js


def test_chat_pages_load_models_with_auth_headers_after_auth_bootstrap():
    public_js = Path("app/static/public/js/chat.js").read_text(encoding="utf-8")
    function_js = Path("_public/static/function/js/chat.js").read_text(encoding="utf-8")

    assert "async function loadModels(authHeader)" in public_js
    assert "fetch('/v1/public/models'" in public_js
    assert "grok-4.20-multi-agent" in public_js
    assert "headers: buildAuthHeaders(authHeader)" in public_js
    assert "await loadModels(authResult);" in public_js

    assert "async function loadModels(authHeader)" in function_js
    assert "fetch('/v1/function/models'" in function_js
    assert "headers: buildAuthHeaders(authHeader)" in function_js
    assert "await loadModels(authResult);" in function_js


def test_chat_pages_handle_xai_responses_sse_events():
    public_js = Path("app/static/public/js/chat.js").read_text(encoding="utf-8")
    function_js = Path("_public/static/function/js/chat.js").read_text(encoding="utf-8")

    for js in (public_js, function_js):
        assert "json.type === 'response.output_text.delta'" in js
        assert "json.type === 'response.output_text.done'" in js
        assert "json.type === 'response.completed'" in js
        assert "json.type === 'response.created'" in js
        assert "json.type === 'response.in_progress'" in js
        assert "reasoning_tokens" in js
        assert "data-synthetic-think" in js


def test_admin_surface_exposes_xai_keys_page_and_nav():
    public_header = Path("app/static/common/html/header.html").read_text(encoding="utf-8")
    function_header = Path("_public/static/common/html/header.html").read_text(encoding="utf-8")
    public_page = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    function_page = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")

    assert "/admin/xai-keys" in public_header
    assert "/admin/xai-keys" in function_header
    assert "xAI 密钥管理" in public_page
    assert "xAI 密钥管理" in function_page


def test_xai_keys_page_exposes_admin_table_and_actions():
    html = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    js = Path("_public/static/admin/js/xai-keys.js").read_text(encoding="utf-8")

    assert 'id="xai-keys-table-body"' in html
    assert "fetch('/v1/admin/xai-keys'" in js
    assert "async function openCreateModal()" in js


def test_app_xai_keys_page_exposes_admin_table_and_actions():
    html = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
    js = Path("app/static/admin/js/xai-keys.js").read_text(encoding="utf-8")

    assert 'id="xai-keys-table-body"' in html
    assert "async function saveXAIKey()" in js


def test_token_admin_scripts_preserve_scroll_position_on_row_refresh():
    app_js = Path("app/static/admin/js/token.js").read_text(encoding="utf-8")
    public_js = Path("_public/static/admin/js/token.js").read_text(encoding="utf-8")

    assert "captureScrollPosition(" in app_js
    assert "restoreScrollPosition(" in app_js
    assert "await loadData({ preserveScroll: true })" in app_js

    assert "captureScrollPosition(" in public_js
    assert "restoreScrollPosition(" in public_js
    assert "await loadData({ preserveScroll: true })" in public_js
