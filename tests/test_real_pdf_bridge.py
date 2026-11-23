"""KD-PE-112: real-PDF bridge integration tests.

Synthetic four-document bundles (ВО/Э3/СП/ПЭ3) built in memory with realistic
layout variation: shifted table columns, grouped PE3 rows, conflicting voltage
specifications and visually confusable model tokens. No real documents, no
hardcoded device codes in the rules under test.
"""
from __future__ import annotations

import fitz
import pytest

import adapters
from engineering_model import (
    Designation, EngineeringModel, SourceRef, SpecRow, extract_attributes,
    run_universal_verifiers,
)
from confusable_designations import confusable_label


E3 = "ABCD.123456.001.Э3.pdf"
PE3 = "ABCD.123456.001.ПЭ3.pdf"
SP = "ABCD.123456.001.СП.pdf"
VO = "ABCD.123456.001.ВО.pdf"


# ---------------------------------------------------------------------------
# Synthetic bundle builders
# ---------------------------------------------------------------------------

def pe3_pdf(rows, *, x_offset=0.0, page_width=600, page_height=800):
    """PE3-like table; x_offset shifts the whole table sideways."""
    with fitz.open() as pdf:
        page = pdf.new_page(width=page_width, height=page_height)
        for index in range(len(rows) + 1):
            y = 70 + index * 50
            page.draw_line((50 + x_offset, y), (480 + x_offset, y))
        for x in (50, 115, 420, 455, 480):
            page.draw_line((x + x_offset, 70), (x + x_offset, 70 + len(rows) * 50))
        # Column headers (designator header enables structural column finding).
        page.insert_text((60 + x_offset, 60), "Poz.", fontsize=6)
        page.insert_text((458 + x_offset, 60), "Kol.", fontsize=6)
        for index, (designation, quantity, name) in enumerate(rows):
            y = 70 + index * 50 + 18
            page.insert_text((60 + x_offset, y), designation, fontsize=6)
            page.insert_text((460 + x_offset, y), str(quantity), fontsize=6)
            if name:
                page.insert_text((200 + x_offset, y), name, fontsize=6)
        return pdf.tobytes()


def e3_pdf(lines, *, page_width=600, page_height=800):
    with fitz.open() as pdf:
        page = pdf.new_page(width=page_width, height=page_height)
        for index, line in enumerate(lines):
            page.insert_text((60, 90 + index * 14), line, fontsize=8)
        return pdf.tobytes()


def sp_pdf(lines):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for index, line in enumerate(lines):
            page.insert_text((60, 90 + index * 14), line, fontsize=8)
        return pdf.tobytes()


# ---------------------------------------------------------------------------
# Attribute extraction (universal, zero hardcoding)
# ---------------------------------------------------------------------------

def test_extract_attributes_voltage_and_type_signatures():
    attr = extract_attributes("Релейный модуль KPR-SCE-24VDC-1C")
    assert attr["voltages"] == frozenset({"24VDC"})
    assert "KPR-SCE-24VDC-1C" in attr["type_tokens"]


def test_extract_attributes_ac_and_plain_voltages():
    assert extract_attributes("Прибор 230 V AC")["voltages"] == frozenset({"230VAC"})
    assert extract_attributes("Лампа 24 В")["voltages"] == frozenset({"24V"})


def test_extract_attributes_empty_on_generic_text():
    assert extract_attributes("Монтажная пластина") == {
        "voltages": frozenset(), "type_tokens": frozenset(),
    }


# ---------------------------------------------------------------------------
# KD-EM-112-1: repeated designation with conflicting attributes
# ---------------------------------------------------------------------------

def _conflict_model():
    model = EngineeringModel()
    for name, row_no in (("Релейный модуль KPR-SCE-24VDC-1C", 2),
                         ("Релейный модуль KPR-SCE-230VAC-1C", 3)):
        ref = SourceRef(PE3, 1, row_no)
        row = SpecRow(raw_text="KLD3", quantity=1,
                      designations=[Designation.classify("KLD3")],
                      provenance=ref, name=name, attributes=extract_attributes(name))
        model.spec_rows.append(row)
    return model


def test_repeated_designation_voltage_conflict_is_warning():
    findings = run_universal_verifiers(_conflict_model())
    conflicts = [f for f in findings if f["code"] == "KD-EM-112-1"]
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == "warning"
    assert "24VDC" in conflicts[0]["message"] and "230VAC" in conflicts[0]["message"]
    assert "строка 2" in conflicts[0]["location"] and "строка 3" in conflicts[0]["location"]


def test_compatible_repeat_is_not_a_conflict():
    model = EngineeringModel()
    for row_no in (2, 3):
        ref = SourceRef(PE3, 1, row_no)
        row = SpecRow(raw_text="HL1", quantity=1,
                      designations=[Designation.classify("HL1")],
                      provenance=ref, name="Лампа сигнальная 24VDC",
                      attributes=extract_attributes("Лампа 24VDC"))
        model.spec_rows.append(row)
    assert [f for f in run_universal_verifiers(model) if f["code"] == "KD-EM-112-1"] == []


def test_rows_without_explicit_attributes_are_not_conflicts():
    model = EngineeringModel()
    for row_no in (2, 3):
        ref = SourceRef(PE3, 1, row_no)
        row = SpecRow(raw_text="HL1", quantity=1,
                      designations=[Designation.classify("HL1")],
                      provenance=ref, name="Изделие без параметров")
        model.spec_rows.append(row)
    assert [f for f in run_universal_verifiers(model) if f["code"] == "KD-EM-112-1"] == []


# ---------------------------------------------------------------------------
# KD-EM-112-2: visually confusable model tokens across Э3 and ПЭ3
# ---------------------------------------------------------------------------

def test_confusable_model_tokens_across_documents():
    model = EngineeringModel()
    model.e3_model_tokens["SO1.PLC.01"] = {SourceRef(E3, 1)}
    model.pe3_model_tokens["S01.PLC.01"] = {SourceRef(PE3, 1, 4)}
    findings = run_universal_verifiers(model)
    candidates = [f for f in findings if f["code"] == "KD-EM-112-2"]
    assert len(candidates) == 1
    assert candidates[0]["severity"] == "info"
    assert "O/0" in candidates[0]["message"]
    assert confusable_label("SO1.PLC.01", "S01.PLC.01") == "O/0"


def test_identical_model_tokens_are_not_candidates():
    model = EngineeringModel()
    model.e3_model_tokens["SO1.PLC.01"] = {SourceRef(E3, 1)}
    model.pe3_model_tokens["SO1.PLC.01"] = {SourceRef(PE3, 1, 4)}
    assert [f for f in run_universal_verifiers(model) if f["code"] == "KD-EM-112-2"] == []


def test_no_confusable_check_without_both_sides():
    model = EngineeringModel()
    model.e3_model_tokens["SO1.PLC.01"] = {SourceRef(E3, 1)}
    assert run_universal_verifiers(model) == []


# ---------------------------------------------------------------------------
# Full adapter pipeline on a shifted-layout 4-PDF bundle
# ---------------------------------------------------------------------------

def test_build_model_shifted_columns_grouped_row_and_attributes():
    pe3 = pe3_pdf([
        ("KLD1...KLD3", 3, "Релейный модуль KPR-SCE-24VDC-1C"),
        ("KLD3", 1, "Релейный модуль KPR-SCE-230VAC-1C"),
        ("TA2\nTA3", 2, "Датчик тока ТТ-100"),
    ], x_offset=40.0)
    e3 = e3_pdf(["KLD1", "KLD2", "KLD3", "TA2", "TA3"])
    model = adapters.build_model([(E3, e3), (PE3, pe3)])
    # Grouped multi-designator row expands cleanly into the model.
    expanded = {d.normalized for r in model.spec_rows for d in r.designations}
    assert {"KLD1", "KLD2", "KLD3", "TA2", "TA3"} <= expanded
    # No extraction-gap diagnostics on a parseable document.
    assert not model.adapter_diagnostics
    # Attributes bridged from the name column.
    conflict = [f for f in run_universal_verifiers(model) if f["code"] == "KD-EM-112-1"]
    assert len(conflict) == 1


def test_build_model_emits_em000_for_unparseable_pdf():
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)  # blank page: no text layer
        blank = pdf.tobytes()
    model = adapters.build_model([(PE3, blank)])
    assert len(model.spec_rows) == 0
    assert any(d["code"] == "KD-EM-000" for d in model.adapter_diagnostics)


def test_engineering_findings_surfaces_adapter_failure(monkeypatch):
    def boom(pdf_sources, names=None):
        raise RuntimeError("adapter boom")
    monkeypatch.setattr(adapters, "build_model", boom)
    findings = adapters.engineering_findings([(PE3, "ПЭ3", "k", b"%PDF-1.4")])
    assert len(findings) == 1
    assert findings[0]["code"] == "KD-EM-000"
    assert findings[0]["severity"] == "error"
    assert "RuntimeError" in findings[0]["message"]


def test_engineering_findings_disabled_flag_returns_empty():
    assert adapters.engineering_findings(
        [(PE3, "ПЭ3", "k", b"x")], cfg={"engineering_model_checks": False}) == []


# ---------------------------------------------------------------------------
# End-to-end through the real crosscheck pipeline (regression for the
# original acceptance failure: real-PDF findings must not be zero)
# ---------------------------------------------------------------------------

def test_crosscheck_pipeline_grouped_row_no_false_missing():
    pe3 = pe3_pdf([("TA2\nTA3", 2, "Датчик тока ТТ-100")])
    from kd_crosscheck import run as crosscheck
    findings = crosscheck(E3, e3_pdf(["TA2", "TA3"]), PE3, pe3)
    assert "KD-PE-105" not in [f["code"] for f in findings]


def test_crosscheck_pipeline_voltage_conflict_visible():
    pe3 = pe3_pdf([
        ("KLD1...KLD3", 3, "Релейный модуль KPR-SCE-24VDC-1C"),
        ("KLD3", 1, "Релейный модуль KPR-SCE-230VAC-1C"),
    ])
    from kd_crosscheck import check_spec_duplicates
    findings = check_spec_duplicates(PE3, pe3)
    conflicts = [f for f in findings if f["code"] == "KD-PE-106"]
    assert conflicts and conflicts[0]["severity"] == "warning"
    assert "24VDC" in conflicts[0]["message"] and "230VAC" in conflicts[0]["message"]
