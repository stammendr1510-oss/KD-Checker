"""Pipeline seam tests (KD-PE-109): check_documents must emit KD-EM-* findings
additively, honor the config flag, and never suppress legacy codes by default.
Model construction is stubbed with synthetic data (invented classes only).
"""
import fitz
import pytest

import adapters
import naming
from engineering_model import Designation, SourceRef, SpecRow
import secure_backend


@pytest.fixture()
def synthetic_classes():
    naming.configure_reference_classes(r'(?:ZZ|MQ)')
    yield
    naming.reset_reference_classes()


def _pdf(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()


def _sources():
    return [('СИНТ.Э3.pdf', _pdf('ZZ1')), ('СИНТ.ПЭ3.pdf', _pdf('ZZ1'))]


CFG = {'require_designation_in_filename': False, 'designation_pattern': 'x',
       'detect_duplicate_designations': False, 'require_pdf_text': False,
       'min_pdf_text_chars': 0, 'require_excel_headers': False,
       'specification_headers': ['позиция'], 'check_specification_references': False,
       'check_duplicate_positions': False, 'warn_empty_dxf': False}


def _patched_model(monkeypatch):
    def fake_build_model(pdf_sources, names=None):
        model = adapters.EngineeringModel()
        ref = SourceRef(document='СИНТ.ПЭ3.pdf', page=1, row=1)
        model.spec_rows.append(SpecRow(raw_text='ZZ1;MQ1', quantity=3,
                                       designations=[Designation.classify('ZZ1'),
                                                     Designation.classify('MQ1')],
                                       provenance=ref))
        model.uploaded_identities.add(('Э3', 'синт'))
        return model
    monkeypatch.setattr(adapters, 'build_model', fake_build_model)


def test_check_documents_emits_em_findings(monkeypatch, synthetic_classes):
    _patched_model(monkeypatch)
    issues = secure_backend.check_documents(_sources(), dict(CFG))
    codes = {i['code'] for i in issues}
    assert 'KD-EM-108-1' in codes  # quantity 3 vs 2 confirmed


def test_check_documents_flag_off(monkeypatch, synthetic_classes):
    _patched_model(monkeypatch)
    cfg = dict(CFG, engineering_model_checks=False)
    issues = secure_backend.check_documents(_sources(), cfg)
    assert not any(i['code'].startswith('KD-EM-') for i in issues)


def test_check_documents_additive_by_default(monkeypatch, synthetic_classes):
    _patched_model(monkeypatch)
    issues = secure_backend.check_documents(_sources(), dict(CFG))
    assert any(i['code'].startswith('KD-EM-') for i in issues)
    assert any(i['code'].startswith('KD-PE-') for i in issues)  # legacy untouched


def test_check_documents_failure_never_breaks_pipeline(monkeypatch, synthetic_classes):
    def boom(pdf_sources, names=None):
        raise RuntimeError('extraction failure')
    monkeypatch.setattr(adapters, 'build_model', boom)
    issues = secure_backend.check_documents(_sources(), dict(CFG))
    # The pipeline must survive (legacy findings still produced) and the
    # failure must be surfaced as a structured KD-EM-000 diagnostic, not
    # silently swallowed.
    assert issues and any(i['code'] == 'KD-PE-000' for i in issues)
    diagnostics = [i for i in issues if i['code'] == 'KD-EM-000']
    assert len(diagnostics) == 1 and diagnostics[0]['severity'] == 'error'
    assert 'построение инженерной модели' in diagnostics[0]['location']
    assert 'RuntimeError' in diagnostics[0]['message']
