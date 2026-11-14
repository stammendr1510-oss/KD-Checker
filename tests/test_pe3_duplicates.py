"""Document-scoped duplicate checks with generated PDFs only."""
from contextlib import closing
import csv
from io import BytesIO, StringIO

from fastapi.testclient import TestClient
import fitz
from openpyxl import load_workbook
import pytest

import app as web
from kd_crosscheck import check_spec_duplicates, get_spec_rows
import storage


def pe3(pages, grid=True, frame_only=False, extra_mentions=False):
    """Each page contains (notation, qty) rows; all contents are artificial."""
    with fitz.open() as pdf:
        for rows in pages:
            page = pdf.new_page(width=600, height=800)
            page.insert_text((180, 40), 'Artificial test drawing; no enterprise data. KM1 KM1')
            if extra_mentions:
                page.insert_text((60, 30), '1KM1', fontsize=6)
                page.insert_text((438, 30), '1', fontsize=6)
            if grid:
                ys = (20, 50, 70, 70+len(rows)*50) if frame_only else (20, 50, *range(70, 71+len(rows)*50, 50))
                for y in ys:
                    page.draw_line((50, y), (480, y))
                for x in (50, 115, 420, 455, 480):
                    page.draw_line((x, 20), (x, 70+len(rows)*50))
            for index, (notation, qty) in enumerate(rows):
                top = 70+index*50
                page.insert_text((60, top+18), notation, fontsize=6)
                page.insert_text((438, top+18), str(qty), fontsize=6)
                if extra_mentions:
                    page.insert_text((180, top+18), 'Description mentions 1KM1 1KM1', fontsize=6)
                    page.insert_text((490, top+18), '1KM1;1KM1', fontsize=6)
        return pdf.tobytes()


def scheme_mentions():
    with fitz.open() as pdf:
        for _ in range(2):
            page = pdf.new_page()
            page.insert_text((80, 90), '1KM1\n1KM1\n1KM1')
        return pdf.tobytes()


def duplicate_codes(content):
    return check_spec_duplicates('demo.ПЭ3.pdf', content)


@pytest.mark.parametrize('rows, names', [
    ([('1KM1', 1), ('1KM1', 1)], ['1KM1']),
    ([('1km1', 1), ('1KM1', 1)], ['1KM1']),
    ([('1KM01', 1), ('1KM01', 1)], ['1KM01']),
    ([('1KM1;XT1', 2), ('XT1;1KM2', 2)], ['XT1']),
    ([('1KM1...1KM3', 3), ('1KM3...1KM5', 3)], ['1KM3']),
    ([('1KM1...1KM3', 3), ('1KM2', 1)], ['1KM2']),
    ([('1K\nM1', 1), ('1KM1', 1)], ['1KM1']),
])
def test_confirmed_repeated_cells_and_range_intersections(rows, names):
    findings = duplicate_codes(pe3([rows]))
    assert len(findings) == len(names)
    for finding, name in zip(findings, names):
        assert finding['code'] == 'KD-PE-104'
        assert finding['severity'] == 'warning'
        assert finding['file'] == 'demo.ПЭ3.pdf'
        assert f'«{name}»' in finding['message']
        assert '2 разных строках одного ПЭ3' in finding['message']
        assert finding['location'] == 'Лист 1, строка таблицы 3; Лист 1, строка таблицы 4'
        assert 'не подтверждённая ошибка КД' in finding['recommendation']


@pytest.mark.parametrize('rows', [
    [('1KM1', 1), ('2KM1', 1)],
    [('XT1', 1), ('XT1.1', 1)],
    [('1KM1...1KM3', 3), ('1KM4...1KM6', 3)],
    [('1KM1;1KM1', 1)],
    [('1KM1...1KM3;1KM2...1KM4', 4)],
])
def test_distinct_designators_and_mentions_inside_one_cell_are_not_duplicates(rows):
    assert duplicate_codes(pe3([rows])) == []


def test_repeated_headers_descriptions_and_notes_are_not_position_cells():
    assert duplicate_codes(pe3([[('1KM1', 1), ('XT1', 1)]], extra_mentions=True)) == []


def test_description_inside_legacy_search_window_is_not_a_position_duplicate():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 120, 170):
            page.draw_line((50, y), (480, y))
        for x in (50, 100, 420, 455, 480):
            page.draw_line((x, 70), (x, 170))
        for y in (90, 140):
            # The actual designation cells are empty; this text is in description.
            page.insert_text((108, y), '1KM1', fontsize=6)
            page.insert_text((438, y), '1', fontsize=6)
        content = pdf.tobytes()
    assert duplicate_codes(content) == []


def test_horizontal_rules_without_designator_column_borders_are_not_enough():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 120, 170):
            page.draw_line((50, y), (480, y))
        for y in (90, 140):
            page.insert_text((60, y), '1KM1', fontsize=6)
            page.insert_text((438, y), '1', fontsize=6)
        content = pdf.tobytes()
    assert duplicate_codes(content) == []


def test_rectangular_cell_paths_are_supported_as_confirmed_borders():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for top in (70, 120):
            page.draw_rect(fitz.Rect(50, top, 115, top+50))
            page.insert_text((60, top+18), '1KM1', fontsize=6)
            page.insert_text((438, top+18), '1', fontsize=6)
        content = pdf.tobytes()
    assert len(duplicate_codes(content)) == 1


def test_filled_text_backgrounds_are_not_confirmed_table_borders():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for top in (70, 120):
            page.draw_rect(fitz.Rect(50, top, 115, top+50), color=None, fill=(.9,.9,.9))
            page.insert_text((60, top+18), '1KM1', fontsize=6)
            page.insert_text((438, top+18), '1', fontsize=6)
        content = pdf.tobytes()
    assert duplicate_codes(content) == []


def test_unread_fragment_outside_legacy_window_makes_duplicate_evidence_incomplete():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 120, 170):
            page.draw_line((50, y), (480, y))
        for x in (50, 115, 420, 455, 480):
            page.draw_line((x, 70), (x, 170))
        for y in (90, 140):
            page.insert_text((60, y), '1KM1', fontsize=6)
            page.insert_text((114, y), '2', fontsize=6)
            page.insert_text((438, y), '1', fontsize=6)
        content = pdf.tobytes()
    assert duplicate_codes(content) == []


@pytest.mark.parametrize('uncertain', [
    '1KM1;XT1\n2', '1KM1;???', '1KM1...1KM', '1KM1...2KM3',
])
def test_ambiguous_rows_are_excluded_even_with_a_known_subset(uncertain):
    content = pe3([[(uncertain, 3), ('1KM1', 1)]])
    assert duplicate_codes(content) == []
    assert all(not row['duplicate_eligible'] for row in get_spec_rows(content) if row['unknown'])


@pytest.mark.parametrize('grid,frame_only', [(False, False), (True, True)])
def test_no_confident_row_boundaries_means_no_duplicate_warning(grid, frame_only):
    content = pe3([[('1KM1', 1), ('1KM1', 1)]], grid=grid, frame_only=frame_only)
    assert duplicate_codes(content) == []
    assert all(not row['duplicate_eligible'] for row in get_spec_rows(content))


def test_padded_range_does_not_invent_an_unpadded_duplicate():
    content = pe3([[('1KM01...1KM03', 3), ('1KM1', 1)]])
    assert duplicate_codes(content) == []
    assert get_spec_rows(content)[0]['duplicate_eligible'] is False


def test_same_cell_is_counted_once_even_if_extraction_record_is_repeated():
    from kd_crosscheck import duplicate_findings
    row = get_spec_rows(pe3([[('1KM1', 1)]]))[0]
    assert duplicate_findings([row, row], 'demo.ПЭ3.pdf') == []


def test_cross_page_repeats_are_one_warning_with_every_location():
    content = pe3([[('1KM1', 1)], [('XT1', 1), ('1KM1', 1)], [('1KM1', 1)]])
    findings = duplicate_codes(content)
    assert len(findings) == 1
    assert findings[0]['location'] == 'Лист 1, строка таблицы 3; Лист 2, строка таблицы 4; Лист 3, строка таблицы 3'
    assert '3 разных строках' in findings[0]['message']


def test_api_does_not_mix_documents_or_scheme_mentions(monkeypatch):
    monkeypatch.setattr(web, 'analyze', lambda docs, config: [])
    monkeypatch.setattr(web, 'save_run', lambda names, issues: 42)
    single = pe3([[('1KM1', 1)]])
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=[
            ('files', ('demo.Э3.pdf', scheme_mentions(), 'application/pdf')),
            ('files', ('demo.ПЭ3.pdf', single, 'application/pdf')),
            ('files', ('other.ПЭ3.pdf', single, 'application/pdf')),
        ])
    assert response.status_code == 200
    result = response.json()
    assert result['summary'] == dict(error=0, warning=0, info=2)
    assert [item['code'] for item in result['issues']] == ['KD-SET-101', 'KD-PE-100']
    assert result['issues'][0]['file'] == 'other.ПЭ3.pdf'
    assert 'совпадений — 1' in result['issues'][1]['message']


@pytest.mark.parametrize('wrapped', [False, True])
def test_previous_bundle_scenario_keeps_64_matches_and_only_quantity_warning(monkeypatch, wrapped):
    from test_pdf_normalization import generated_bundle
    monkeypatch.setattr(web, 'analyze', lambda docs, config: [])
    monkeypatch.setattr(web, 'save_run', lambda names, issues: 42)
    scheme, spec, _ = generated_bundle(wrapped)
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=[
            ('files', ('demo.Э3.pdf', scheme, 'application/pdf')),
            ('files', ('demo.ПЭ3.pdf', spec, 'application/pdf')),
        ])
    assert response.status_code == 200
    result = response.json()
    assert result['summary'] == dict(error=0, warning=1, info=1)
    assert [finding['code'] for finding in result['issues']] == ['KD-PE-102', 'KD-PE-100']
    assert 'количество 9' in result['issues'][0]['message']
    assert 'обозначений (7)' in result['issues'][0]['message']
    assert 'уникальных обозначений — 64' in result['issues'][1]['message']
    assert 'совпадений — 64' in result['issues'][1]['message']


def test_multiple_scheme_pairs_do_not_duplicate_pe3_warnings(monkeypatch):
    monkeypatch.setattr(web, 'analyze', lambda docs, config: [])
    monkeypatch.setattr(web, 'save_run', lambda names, issues: 42)
    repeated = pe3([[('1KM1', 1), ('1KM1', 1)]])
    identical_scheme = scheme_mentions()
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=[
            ('files', ('demo.Э3.pdf', identical_scheme, 'application/pdf')),
            ('files', ('demo.Э3.pdf', identical_scheme, 'application/pdf')),
            ('files', ('demo.ПЭ3.pdf', repeated, 'application/pdf')),
        ])
    assert response.status_code == 200
    codes = [issue['code'] for issue in response.json()['issues']]
    assert codes.count('KD-PE-104') == 1
    assert codes.count('KD-PE-100') == 1  # Identical E3 uploaded twice is analyzed once.
    assert codes.count('KD-SET-102') == 1  # Repeated upload is still reported.


def test_standalone_pe3_decisions_history_recheck_and_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    monkeypatch.setattr(web, 'analyze', lambda docs, config: [])
    content = pe3([[('1KM1', 1), ('1KM1', 1)]])
    with TestClient(web.app) as client:
        def check():
            response = client.post('/api/check', files=[('files', ('demo.ПЭ3.pdf', content, 'application/pdf'))])
            assert response.status_code == 200
            return response.json()
        first = check()
        assert first['summary'] == dict(error=0, warning=1, info=0)
        assert [finding['code'] for finding in first['issues']] == ['KD-PE-104']
        run_id = first['id']
        original = client.get(f'/api/runs/{run_id}').json()
        state = original['finding_states'][0]
        csv_before = client.get(f'/api/runs/{run_id}/csv').content
        url = f'/api/runs/{run_id}/findings/{state["id"]}'
        response = client.patch(url, json=dict(status='resolved', comment='=1+1', expected_version=0))
        assert response.status_code == 200
        assert len(client.get(url+'/history').json()) == 1
        assert client.get(f'/api/runs/{run_id}').json()['issues'] == original['issues']
        assert client.get(f'/api/runs/{run_id}/csv').content == csv_before
        records = list(csv.reader(StringIO(csv_before.decode('utf-8-sig')), delimiter=';'))
        assert records[1][0:2] == ['warning', 'KD-PE-104']
        with closing(load_workbook(BytesIO(client.get(f'/api/runs/{run_id}/xlsx').content))) as book:
            sheet = book['Замечания']
            assert sheet['B2'].value == 'Предупреждение'
            assert sheet['C2'].value == 'KD-PE-104'
            assert sheet['H2'].value == 'Устранено'
            assert sheet['I2'].value == '=1+1' and sheet['I2'].data_type == 's'
        second = check()
        current = client.get(f'/api/runs/{second["id"]}').json()['finding_states'][0]
        assert current['status'] == 'new' and current['comment'] == ''
        assert current['previous']['id'] == state['id']
        assert current['previous']['status'] == 'resolved'
