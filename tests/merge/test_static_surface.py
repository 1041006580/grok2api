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
