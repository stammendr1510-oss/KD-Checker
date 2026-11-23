"""Synthetic KD-PE-107 reference bundle and effectiveness table.

The module builds only artificial PDFs in memory.  It never opens a real
document, never uses the network and never writes files.  The five scenarios
are engineering control examples, not a substitute for acceptance on real
drawings.
"""
from __future__ import annotations

import fitz

import kd_crosscheck as cross


SCENARIOS = (
    ("group_row", "TA2;TA3 / TA2 newline TA3"),
    ("conflict", "Repeated designation with incompatible product attributes"),
    ("confusable", "Visually confusable model characters"),
    ("families", "K / KA / KL / HL / KLD classification"),
    ("markers", "Note/reference markers do not hide a device"),
)


def table_pdf(rows, *, page_width=600, page_height=800):
    """Build a PE3-like table with grid-backed rows."""
    with fitz.open() as pdf:
        page = pdf.new_page(width=page_width, height=page_height)
        for index in range(len(rows) + 1):
            y = 70 + index * 50
            page.draw_line((50, y), (480, y))
        for x in (50, 115, 420, 455, 480):
            page.draw_line((x, 70), (x, 70 + len(rows) * 50))
        for index, (designation, quantity, name) in enumerate(rows):
            y = 70 + index * 50 + 18
            page.insert_text((60, y), designation, fontsize=6)
            page.insert_text((438, y), str(quantity), fontsize=6)
            if name:
                page.insert_text((200, y), name, fontsize=6)
        return pdf.tobytes()


def scheme_pdf(lines, *, page_width=600, page_height=800):
    """Build an E3-like text page."""
    with fitz.open() as pdf:
        page = pdf.new_page(width=page_width, height=page_height)
        for index, line in enumerate(lines):
            page.insert_text((60, 90 + index * 14), line, fontsize=8)
        return pdf.tobytes()


def _codes(findings):
    return [finding["code"] for finding in findings]


def _kd_105_missing(findings):
    missing = []
    for finding in findings:
        if finding.get("code") == "KD-PE-105":
            missing.extend(
                item["designation"]
                for item in finding.get("details", {}).get("missing", [])
            )
    return missing


def _scenario_group_row():
    e3 = scheme_pdf(["TA2", "TA3"])
    pe3 = table_pdf([("TA2\nTA3", 2, "Current sensor")])
    findings = cross.run("ref.Э3.pdf", e3, "ref.ПЭ3.pdf", pe3)
    missing = _kd_105_missing(findings)
    info = next((finding for finding in findings if finding["code"] == "KD-PE-100"), None)
    overlap_ok = bool(info and "совпадений — 2" in info["message"])
    ok = not missing and overlap_ok and "KD-PE-106" not in _codes(findings)
    actual = (
        f"KD-PE-105 missing={missing or 'none'}; "
        f"overlap2={overlap_ok}; codes={_codes(findings)}"
    )
    return ok, actual


def _scenario_conflict():
    pe3 = table_pdf([
        ("KLD1...KLD3", 3, "Rele module KPR-SCE-24VDC-1C"),
        ("KLD3", 1, "Rele module KPR-SCE-230VAC-1C"),
    ])
    findings = cross.check_spec_duplicates("ref.ПЭ3.pdf", pe3)
    conflict = next((finding for finding in findings if finding["code"] == "KD-PE-106"), None)
    ok = bool(
        conflict
        and conflict["severity"] == "warning"
        and "KLD3" in conflict["message"]
        and "24VDC" in conflict["message"]
        and "230VAC" in conflict["message"]
        and conflict["location"].count("ПЭ3:") == 2
    )
    actual = (
        f"KD-PE-106={conflict['severity'] if conflict else 'absent'}; "
        f"rows={conflict['location'] if conflict else 'none'}"
    )
    return ok, actual


def _scenario_confusable():
    e3 = scheme_pdf(["SO1.PLC.01"])
    pe3 = table_pdf([("K1", 1, "PLC module S01.PLC.01")])
    findings = cross.run("ref.Э3.pdf", e3, "ref.ПЭ3.pdf", pe3)
    candidate = next((finding for finding in findings if finding["code"] == "KD-PE-107"), None)
    ok = bool(
        candidate
        and candidate["severity"] == "info"
        and "SO1.PLC.01" in candidate["message"]
        and "S01.PLC.01" in candidate["message"]
        and "O/0" in candidate["message"]
    )
    actual = (
        f"KD-PE-107={candidate['severity'] if candidate else 'absent'}; "
        f"pair={'O/0' if candidate and 'O/0' in candidate['message'] else 'none'}"
    )
    return ok, actual


def _scenario_families():
    labels = ["K1", "KA1", "KL1", "HL1", "KLD1"]
    e3 = scheme_pdf(labels)
    pe3 = table_pdf([("XT1", 1, "Terminal block")])
    findings = cross.run("ref.Э3.pdf", e3, "ref.ПЭ3.pdf", pe3)
    missing = set(_kd_105_missing(findings))
    ok = set(labels) <= missing
    actual = f"KD-PE-105 missing={sorted(missing)}"
    return ok, actual


def _scenario_markers():
    e3 = scheme_pdf(["K1 (лист 3)", "K2:1 - XT1:2"])
    pe3 = table_pdf([("XT1", 1, "Terminal block")])
    findings = cross.run("ref.Э3.pdf", e3, "ref.ПЭ3.pdf", pe3)
    missing = set(_kd_105_missing(findings))
    ok = "K1" in missing and "K2" not in missing
    actual = f"KD-PE-105 missing={sorted(missing)}; K2_is_reference={'K2' not in missing}"
    return ok, actual


_RUNNERS = {
    "group_row": _scenario_group_row,
    "conflict": _scenario_conflict,
    "confusable": _scenario_confusable,
    "families": _scenario_families,
    "markers": _scenario_markers,
}


def run_scenarios():
    """Return one result per reference scenario, never raising for a FAIL."""
    results = []
    for key, title in SCENARIOS:
        try:
            ok, actual = _RUNNERS[key]()
        except Exception as error:  # noqa: BLE001 - a crash is a visible FAIL
            ok, actual = False, f"exception: {type(error).__name__}"
        results.append({
            "scenario": key,
            "title": title,
            "expected": "PASS",
            "actual": actual,
            "result": "PASS" if ok else "FAIL",
        })
    return results


def render_table(results=None):
    """Render a compact Markdown table for the final report."""
    rows = results if results is not None else run_scenarios()
    lines = [
        "| Сценарий | Ожидается | Фактически | Результат |",
        "|---|---|---|---|",
    ]
    for item in rows:
        lines.append(
            f"| {item['scenario']} | {item['expected']} | {item['actual']} | {item['result']} |"
        )
    return "\n".join(lines)


def all_passed(results=None):
    rows = results if results is not None else run_scenarios()
    return all(item["result"] == "PASS" for item in rows)
