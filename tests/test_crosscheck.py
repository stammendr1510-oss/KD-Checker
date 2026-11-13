from kd_crosscheck import expand_notation, designation_type, pair_key
import fitz
import pytest
from kd_crosscheck import get_spec_rows, run


TRANSFORMERS = {f'{group}TA{number}' for group in (1, 2, 3) for number in (1, 2, 3)}


def spec_pdf(lines, quantity_line=0, grid=True, second_row=True, rectangles=False):
    """A two-page PE3 with a wrapped cell on sheet 2; no external fixtures."""
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        page = pdf.new_page(width=600, height=800)
        if grid:
            if rectangles:
                page.draw_rect(fitz.Rect(50, 70, 480, 155))
                page.draw_rect(fitz.Rect(50, 155, 480, 205))
            else:
                for y in (70, 155, 205):
                    page.draw_line((50, y), (480, y))
        for index, text in enumerate(lines):
            page.insert_text((60, 90+index*14), text, fontsize=6)
        page.insert_text((438, 90+quantity_line*14), '9', fontsize=6)
        if second_row:
            page.insert_text((60, 180), '1KM1...1KM2', fontsize=6)
            page.insert_text((438, 180), '2', fontsize=6)
        return pdf.tobytes()


@pytest.mark.parametrize('notation', [
    '1TA1...1TA3;2TA1...2TA3;3TA1...3TA3',
    '1TA1...1TA3;\n2TA1...2TA3;\n3TA1\n...3TA3',
    '1TA1.\n..1TA3\n;2TA1...\n2TA3;3TA1...3TA3',
    '1TA1…1TA3;\r\n2TA1…2TA3;\t3TA1…3TA3',
    '1TA1..3;2TA1..3;3TA1..3',
    '1TA\n1...1TA\n3;2TA1...2TA3;3TA1...3TA3;',
])
def test_multiline_transformer_notation(notation):
    assert expand_notation(notation) == (TRANSFORMERS, [])


@pytest.mark.parametrize('quantity_line', [0, 1, 2, 3])
@pytest.mark.parametrize('rectangles', [False, True])
def test_wrapped_cell_preserves_all_nine_and_separates_rows(quantity_line, rectangles):
    content = spec_pdf(['1TA1...1TA3;', '2TA1...2TA3;', '3TA1', '...3TA3'],
                       quantity_line=quantity_line, rectangles=rectangles)
    rows = get_spec_rows(content)
    assert len(rows) == 2
    assert rows[0]['page'] == 2
    assert rows[0]['quantity'] == 9
    assert rows[0]['entries'] == TRANSFORMERS
    assert not rows[0]['unknown']
    assert rows[1]['entries'] == {'1KM1', '1KM2'}
    assert rows[1]['quantity'] == 2


def test_wrapped_block_without_grid():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 90), '1TA1...1TA3;\n2TA1...2TA3;\n3TA1\n...3TA3', fontsize=6)
        page.insert_text((438, 90), '9', fontsize=6)
        page.insert_text((60, 150), '1KM1...1KM2', fontsize=6)
        page.insert_text((438, 150), '2', fontsize=6)
        rows = get_spec_rows(pdf.tobytes())
    assert [r['entries'] for r in rows] == [TRANSFORMERS, {'1KM1', '1KM2'}]


def test_crosscheck_no_false_quantity_mismatch():
    pe = spec_pdf(['1TA1...1TA3;', '2TA1...2TA3;', '3TA1', '...3TA3'], quantity_line=2)
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((60, 90), '\n'.join(sorted(TRANSFORMERS | {'1KM1', '1KM2'})))
        e3 = pdf.tobytes()
    findings = run('ТНБВ.656451.162.Э3.pdf', e3, 'ТНБВ.656451.162.ПЭ3.pdf', pe)
    assert [f['code'] for f in findings] == ['KD-PE-100']
    assert '— 11' in findings[0]['message']


def test_real_quantity_mismatch_still_reported():
    pe = spec_pdf(['1TA1...1TA3;2TA1...2TA3;3TA1'], second_row=False)
    findings = run('a.Э3.pdf', pe, 'a.ПЭ3.pdf', pe)
    assert 'KD-PE-102' in [f['code'] for f in findings]


@pytest.mark.parametrize('grid', [True, False])
def test_adjacent_rows_do_not_complete_each_others_range(grid):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        if grid:
            for y in (70, 110, 150):
                page.draw_line((50, y), (480, y))
        page.insert_text((60, 90), '1TA1...', fontsize=6)
        page.insert_text((438, 90), '3', fontsize=6)
        second_y = 130 if grid else 97
        page.insert_text((60, second_y), '1TA3', fontsize=6)
        page.insert_text((438, second_y), '1', fontsize=6)
        rows = get_spec_rows(pdf.tobytes())
    assert len(rows) == 1
    assert rows[0]['entries'] == {'1TA3'}
    assert rows[0]['quantity'] == 1


def test_web_check_accepts_multiline_pe3(monkeypatch):
    from fastapi.testclient import TestClient
    import app as web

    monkeypatch.setattr(web, 'save_run', lambda names, issues: 42)
    pe = spec_pdf(['1TA1...1TA3;', '2TA1...2TA3;', '3TA1', '...3TA3'], quantity_line=2)
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=[
            ('files', ('ТНБВ.656451.162.Э3.pdf', pe, 'application/pdf')),
            ('files', ('ТНБВ.656451.162.ПЭ3.pdf', pe, 'application/pdf')),
        ])
    assert response.status_code == 200
    result = response.json()
    assert result['id'] == 42
    assert len(result['files']) == 2
    assert set(result['summary']) == {'error', 'warning', 'info'}
    assert 'KD-PE-100' in [f['code'] for f in result['issues']]
    assert 'KD-PE-102' not in [f['code'] for f in result['issues']]


@pytest.mark.parametrize('notation', ['1TA3...1TA1', '1TA1...2TA3', '1TA1...1TA103', '1TA1...'])
def test_ambiguous_ranges_stay_unknown(notation):
    entries, unknown = expand_notation(notation)
    assert not entries
    assert unknown == [notation]

def test_range():
    out, unknown = expand_notation('1KM1...1KM8;2KM1...2KM8')
    assert len(out) == 16 and not unknown

def test_matching_name():
    a='ТНБВ.656451.162.Э3 Щит 6.5-NDB-09.pdf'
    b='ТНБВ.656451.162.ПЭ3 Щит 6.5-NDB-09.pdf'
    assert pair_key(a) == pair_key(b)
    assert designation_type(a) == 'Э3' and designation_type(b) == 'ПЭ3'

def test_lookalike_letters():
    from kd_crosscheck import normalized
    assert normalized('KМ0') == normalized('KM0')

