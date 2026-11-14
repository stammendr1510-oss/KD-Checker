"""Checks that the comparison interface is wired into the existing history page.

API behavior and matching rules have separate regression tests.
"""
from pathlib import Path


PAGE = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")


def test_history_exposes_comparison_controls():
    assert 'id="previousRunId"' in PAGE
    assert 'id="comparisonResults"' in PAGE
    assert "Сравнить до / после" in PAGE
    assert "async function showComparison(currentId)" in PAGE


def test_comparison_screen_uses_api_and_shows_all_four_categories():
    assert "'/compare/'+previousId" in PAGE
    assert "result.summary.new" in PAGE
    assert "result.summary.persisting" in PAGE
    assert "result.summary.disappeared" in PAGE
    assert "result.summary.ambiguous" in PAGE
    assert "esc(result.notice)" in PAGE


def test_user_visible_findings_are_escaped_before_html_insertion():
    assert "esc(issue.code)" in PAGE
    assert "esc(issue.file)" in PAGE
    assert "esc(issue.location)" in PAGE
    assert "esc(issue.message)" in PAGE
    assert "Сравнение недоступно:" in PAGE
