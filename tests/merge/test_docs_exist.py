from pathlib import Path


def test_design_docs_exist():
    assert Path("docs/plans/2026-03-20-upstream-integration-design.md").exists()
    assert Path("docs/plans/2026-03-20-upstream-integration.md").exists()
