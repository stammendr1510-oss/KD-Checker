"""Generated PDF regressions; no uploaded document data is stored here."""
import fitz
import pytest

import kd_crosscheck as checker


@pytest.mark.parametrize('grid', [False, True])
def test_numeric_notes_do_not_remove_independent_rows(grid):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        if grid:
            for y in (80, 125, 175):
                page.draw_line((57, y), (480, y))
        for y, name, quantity, note in [(90, '1XT9', '1', '4'), (150, '1QF9', '2', '8')]:
            page.insert_text((60, y), name, fontsize=6)
            page.insert_text((438, y), quantity, fontsize=6)
            page.insert_text((456, y), note, fontsize=6)
        rows = checker.get_spec_rows(pdf.tobytes())
    assert [row['entries'] for row in rows] == [{'1XT9'}, {'1QF9'}]
    assert [row['quantity'] for row in rows] == [1, 2]


def test_incomplete_source_is_not_invented_to_match_quantity():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (80, 135):
            page.draw_line((57, y), (480, y))
        page.insert_text((60, 90), '1KM1...1KM3;', fontsize=6)
        page.insert_text((60, 100), '2KM1', fontsize=6)
        page.insert_text((438, 95), '6', fontsize=6)
        content = pdf.tobytes()
    rows = checker.get_spec_rows(content)
    assert rows[0]['entries'] == {'1KM1', '1KM2', '1KM3', '2KM1'}
    assert rows[0]['source'].replace('\n', '') == '1KM1...1KM3;2KM1'
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 90), '\n'.join(sorted(rows[0]['entries'])))
        scheme = pdf.tobytes()
    findings = checker.run('bundle.Э3.pdf', scheme, 'bundle.ПЭ3.pdf', content)
    assert [finding['code'] for finding in findings] == ['KD-PE-102', 'KD-PE-100']
    assert 'количество 6' in findings[0]['message']
    assert 'обозначений (4)' in findings[0]['message']


@pytest.mark.parametrize('lines, expected', [
    (['1KM4...', '1KM6;', '2KM10...', '2KM11'], {'1KM4', '1KM5', '1KM6', '2KM10', '2KM11'}),
    (['2QF2...', '2QF5', ';', '3QF1'], {'2QF2', '2QF3', '2QF4', '2QF5', '3QF1'}),
])
@pytest.mark.parametrize('quantity_y', [90, 105, 120])
def test_offset_table_grid_and_numeric_notes(lines, expected, quantity_y):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        # Real PE3's left border is to the right of the old .09W search limit.
        for y in (80, 135, 175):
            page.draw_line((57, y), (480, y))
        for index, text in enumerate(lines):
            # Separate PDF blocks: block membership alone cannot restore this cell.
            page.insert_text((60, 90+index*10), text, fontsize=6)
        page.insert_text((438, quantity_y), str(len(expected)), fontsize=6)
        page.insert_text((456, quantity_y), '17', fontsize=6)
        page.insert_text((60, 155), 'XT7', fontsize=6)
        page.insert_text((438, 155), '1', fontsize=6)
        page.insert_text((456, 155), '4', fontsize=6)
        rows = checker.get_spec_rows(pdf.tobytes())
    assert [row['entries'] for row in rows] == [expected, {'XT7'}]
    assert [row['quantity'] for row in rows] == [len(expected), 1]
    assert all(not row['unknown'] for row in rows)
