from pathlib import Path


def test_repo_metadata_files_exist():
    assert Path("readme.md").exists()
    assert Path("docs/README.en.md").exists()
    assert Path("uv.lock").exists()
    assert Path(".github/workflows/security.yml").exists()
    assert Path(".github/pull_request_template.md").exists()
