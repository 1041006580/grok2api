from pathlib import Path


def test_final_verification_record_present():
    text = Path("docs/plans/2026-03-20-upstream-integration.md").read_text(
        encoding="utf-8"
    )
    assert "Actual Verification Record" in text
    assert "Remaining Compatibility Shims" in text
