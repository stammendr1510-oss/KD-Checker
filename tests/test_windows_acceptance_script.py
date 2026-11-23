"""Static and optional parser checks for the Windows acceptance script.

The actual Windows 11 run is NOT VERIFIED on Linux.  These tests prevent the
two confirmed regressions from returning: non-ASCII encoding and writing logs
into the git clone destination before clone.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "windows11_acceptance.ps1"


def script_text():
    return SCRIPT.read_text(encoding="ascii")


def test_script_is_pure_ascii_for_windows_powershell_51():
    data = SCRIPT.read_bytes()
    assert all(byte < 128 for byte in data)
    assert not data.startswith(b"\xef\xbb\xbf")


def test_script_has_an_explicit_syntax_check():
    text = script_text()
    assert "ParseFile" in text
    assert "$syntaxErrors" in text


def test_results_are_outside_the_clone_directory():
    text = script_text()
    assert '$logs = Join-Path $ResultsDir "logs"' in text
    assert 'Join-Path $WorkDir "logs"' not in text
    assert "windows11-acceptance-summary.json" in text


def test_clone_does_not_require_a_precreated_workdir():
    text = script_text()
    assert "git clone --no-checkout $RepoUrl $WorkDir" in text
    assert "WorkDir is not empty" in text
    assert "Nothing was deleted" in text


def test_script_has_no_destructive_cleanup():
    text = script_text()
    assert "Remove-Item" not in text
    assert "git reset --hard" not in text
    assert "git clean" not in text


def test_exit_codes_are_distinct():
    text = script_text()
    assert "exit 1" in text and "exit 2" in text and "exit 0" in text
    assert "NOT_VERIFIED" in text


def test_no_cyrillic_literal_in_script():
    data = SCRIPT.read_bytes()
    assert b"\xd0" not in data and b"\xd1" not in data


def test_powershell_parser_if_available():
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell is not installed; native Windows parser check is NOT VERIFIED")
    command = (
        "$errors=$null; $tokens=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{SCRIPT}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run([executable, "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
