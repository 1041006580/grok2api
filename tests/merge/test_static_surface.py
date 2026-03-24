from pathlib import Path


def test_static_entry_files_exist():
    assert Path("_public/static/admin/pages/token.html").exists()
    assert Path("_public/static/function/pages/video.html").exists()
