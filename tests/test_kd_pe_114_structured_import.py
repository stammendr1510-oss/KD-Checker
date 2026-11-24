"""KD-PE-114 focused synthetic acceptance tests."""
from io import BytesIO

import fitz
import pytest
from openpyxl import Workbook

import naming
from adapters import (
    apply_engineering_findings,
    build_model,
    deduplicate_findings,
    engineering_analysis,
)
from e3_pe3_inventory import _not_evaluated, extract_mentions, matching_keys
from kd_crosscheck import get_spec_rows


CLASSES = r'(?:ZR|BC|WY)'


@pytest.fixture(autouse=True)
def reference_classes():
    naming.configure_reference_classes(CLASSES)
    yield
    naming.reset_reference_classes()


def test_compound_semicolon_list_expands_dash_range():
    values, unknown = naming.expand_notation('ZR1–ZR3;BC7')
    assert values == {'ZR1', 'ZR2', 'ZR3', 'BC7'}
    assert unknown == []


def test_multiline_table_cell_source_preserves_whitespace():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 130):
            page.draw_line((50, y), (480, y))
        for x in (50, 115, 420, 455, 480):
            page.draw_line((x, 70), (x, 130))
        page.insert_text((60, 60), 'Poz.', fontsize=6)
        page.insert_text((458, 60), 'Kol.', fontsize=6)
        page.insert_text((60, 88), 'ZR1;\nZR2', fontsize=6)
        page.insert_text((460, 88), '2', fontsize=6)
        rows = get_spec_rows(pdf.tobytes())
    assert rows[0]['source'] == 'ZR1; ZR2'
    assert rows[0]['entries'] == {'ZR1', 'ZR2'}


def test_e3_range_adds_interior_mention_and_parent_key_matches():
    words = [(10, 10, 70, 20, 'WY1–WY3', 0, 0, 0)]
    mentions = extract_mentions(words, 1)
    assert {mention.designation for mention in mentions} == {'WY1', 'WY2', 'WY3'}
    assert matching_keys('BC8.2') & matching_keys('BC8')


def test_text_layer_failure_recommends_table_structure_not_ocr():
    finding = _not_evaluated('scheme.pdf', 'elements.pdf', [1], 'pe3_not_parsed')
    assert 'структуру таблицы' in finding['recommendation']
    assert 'OCR' not in finding['recommendation']
    no_text = _not_evaluated('scheme.pdf', 'elements.pdf', [1], 'no_text_layer')
    assert 'OCR' in no_text['recommendation']


def _workbook_bytes():
    workbook = Workbook()
    wires = workbook.active
    wires.title = 'Wire data'
    wires.append(['Source device', 'Source terminal', 'Target device',
                  'Target terminal', 'Wire mark', 'Cable'])
    wires.append(['ZR1', '11', 'ZR2', '12', 'W-17', 'BC4'])
    elements = workbook.create_sheet('Element data')
    elements.append(['Designation', 'Quantity', 'Description'])
    elements.append(['ZR1–ZR2;BC4', 3, 'Module 48 V DC'])
    documents = workbook.create_sheet('Document data')
    documents.append(['Document'])
    documents.append(['QWER.123456.001.Э3'])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_xlsx_populates_connections_rows_refs_and_summary():
    source = ('unseen_structured_bundle.xlsx', _workbook_bytes())
    model = build_model([source])
    assert len(model.connections) == 1
    assert len(model.spec_rows) == 1
    assert len(model.document_refs) == 1
    _findings, summary = engineering_analysis([source])
    assert summary['connections'] == 1
    assert summary['spec_rows'] == 1
    assert summary['document_refs'] == 1
    assert summary['metadata'] == {'xlsx_uploaded': True, 'scope_note': None}


def test_pdf_only_scope_note_stays_out_of_issues():
    issues, summary = apply_engineering_findings([], [], {}, return_summary=True)
    assert issues == []
    assert summary['metadata']['xlsx_uploaded'] is False
    assert 'XLSX' in summary['metadata']['scope_note']


def test_overlap_dedup_uses_group_designator_source_and_severity():
    base = dict(file='QWER.123456.001.ПЭ3.pdf', location='лист 1',
                recommendation='Проверьте строку')
    findings = [
        dict(base, severity='info', code='KD-PE-102', message='Количество для ZR6 отличается'),
        dict(base, severity='warning', code='KD-EM-108-1', message='Количество для ZR6 отличается'),
    ]
    result = deduplicate_findings(findings)
    assert len(result) == 1
    assert result[0]['severity'] == 'warning'
