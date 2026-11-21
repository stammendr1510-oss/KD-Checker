"""XLSX and CSV regressions using artificial saved checks only."""
from contextlib import closing
from copy import deepcopy
import csv
import json
from io import BytesIO, StringIO
from zipfile import ZipFile
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import app as web
from excel_reports import HEADERS, build_excel_report
import storage


@pytest.fixture
def saved_run():
    return {
        'id': 7, 'created_at': '2026-01-02T03:04:05+00:00',
        'files': ['demo.pdf', 'sample.csv'],
        'issues': [
            {'severity': level, 'code': f'TEST-{index}', 'file': 'demo.pdf',
             'location': f'Лист {index}', 'message': f'Искусственное замечание {index}\nВторая строка',
             'recommendation': 'Проверить тестовые данные'}
            for index, level in enumerate(['error', 'warning', 'info', 'warning'], 1)
        ],
    }


def test_workbook_contents_and_format(saved_run):
    original = deepcopy(saved_run)
    with closing(load_workbook(BytesIO(build_excel_report(saved_run)))) as book:
        assert book.sheetnames == ['Общие сведения', 'Замечания']
        general, findings = book.worksheets
        assert list(general.values)[:7] == [
            ('Параметр', 'Значение'), ('№ проверки', 7),
            ('Дата проверки (UTC)', saved_run['created_at']), ('Количество файлов', 2),
            ('Ошибки', 1), ('Предупреждения', 2), ('Информационные сообщения', 1),
        ]
        assert list(general.values)[9:] == [(1, 'demo.pdf'), (2, 'sample.csv')]
        assert list(findings.values)[0] == ('№', 'Уровень', 'Код', 'Документ', 'Лист/место', 'Описание', 'Рекомендация', 'Статус', 'Комментарий инженера', 'Дата изменения')
        assert list(findings.values)[1] == (
            1, 'Ошибка', 'TEST-1', 'demo.pdf', 'Лист 1',
            'Искусственное замечание 1\nВторая строка', 'Проверить тестовые данные', 'Новое', None, None,
        )
        assert [row[1] for row in list(findings.values)[1:]] == [
            'Ошибка', 'Предупреждение', 'Информация', 'Предупреждение',
        ]
        assert [row[0] for row in list(findings.values)[1:]] == [1, 2, 3, 4]
        assert findings.auto_filter.ref == 'A1:J5'
        assert general.auto_filter.ref == 'A9:B11'
        assert general.freeze_panes == findings.freeze_panes == 'A2'
        assert findings['A1'].font.bold
        assert findings.column_dimensions['F'].width == 80
        assert findings.column_dimensions['G'].width == 80
        assert findings['F2'].alignment.wrap_text
        assert general['B10'].alignment.wrap_text
        fills = [findings.cell(row, 2).fill.fgColor.rgb for row in (2, 3, 4)]
        assert len(set(fills)) == 3
        for row in (2, 3, 4):
            assert len({cell.fill.fgColor.rgb for cell in findings[row]}) == 1
    assert saved_run == original


@pytest.mark.parametrize('payload', [
    '=1+1', '+SUM(A1:A2)', '-1+2', '@SUM(A1:A2)',
    ' \t=HYPERLINK("https://example.invalid","test")',
    '\r\n=1+1', '#REF!', '00123', 'Кириллица; "кавычки"\nновая строка',
])
def test_all_text_fields_are_literal_strings(saved_run, payload):
    saved_run['created_at'] = payload
    saved_run['files'] = [payload]
    saved_run['issues'] = [{key: payload for key in (
        'severity', 'code', 'file', 'location', 'message', 'recommendation',
    )}]
    saved_run['finding_states'] = [{'issue_index': 0, 'status': 'new', 'comment': payload, 'updated_at': payload}]
    content = build_excel_report(saved_run)
    with closing(load_workbook(BytesIO(content), data_only=False)) as book:
        cells = [book['Общие сведения']['B3'], book['Общие сведения']['B10']]
        cells += list(book['Замечания'][2])[1:7] + list(book['Замечания'][2])[8:]
        expected = payload.replace('\r\n', '\n').replace('\r', '\n')
        assert all(cell.value == expected and cell.data_type == 's' for cell in cells)
        assert all(cell.hyperlink is None for cell in cells)
    with ZipFile(BytesIO(content)) as archive:
        assert not any('externalLinks' in name for name in archive.namelist())
        for name in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml'):
            xml = ElementTree.fromstring(archive.read(name))
            assert not xml.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f')


def test_invalid_xml_controls_do_not_break_download(saved_run):
    saved_run['files'] = ['demo\x00\x01.pdf']
    saved_run['issues'][0]['message'] = 'Текст\x0b\x0c\x1f\nПродолжение'
    with closing(load_workbook(BytesIO(build_excel_report(saved_run)))) as book:
        assert book['Общие сведения']['B10'].value == 'demo.pdf'
        assert book['Замечания']['F2'].value == 'Текст\nПродолжение'


def test_empty_check_still_has_usable_headers(saved_run):
    saved_run.update(files=[], issues=[])
    with closing(load_workbook(BytesIO(build_excel_report(saved_run)))) as book:
        general, findings = book.worksheets
        assert [general.cell(row, 2).value for row in range(4, 8)] == [0, 0, 0, 0]
        assert findings.max_row == 1
        assert findings.auto_filter.ref == 'A1:J1'
        assert general.auto_filter.ref == 'A9:B9'


def test_download_from_persisted_history_and_csv_compatibility(tmp_path, monkeypatch, saved_run):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    saved_run['issues'][0]['message'] = '=1+1'
    run_id = storage.save_run(saved_run['files'], saved_run['issues'])
    with TestClient(web.app) as client:
        history = client.get('/api/runs').json()
        assert history[0]['id'] == run_id
        record = client.get(f'/api/runs/{run_id}').json()
        response = client.get(f'/api/runs/{run_id}/xlsx')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        assert response.headers['content-disposition'] == f'attachment; filename="kd-report-{run_id}.xlsx"'
        with closing(load_workbook(BytesIO(response.content))) as book:
            assert book['Общие сведения']['B3'].value == record['created_at']
            assert book['Замечания']['F2'].value == '=1+1'
            assert book['Замечания']['F2'].data_type == 's'
        csv_response = client.get(f'/api/runs/{run_id}/csv')
        assert csv_response.status_code == 200
        assert csv_response.content.startswith(b'\xef\xbb\xbf')
        rows = list(csv.reader(StringIO(csv_response.content.decode('utf-8-sig')), delimiter=';'))
        assert rows[0] == ['Важность', 'Код', 'Файл', 'Место', 'Замечание', 'Рекомендация']
        assert rows[1][4] == "'=1+1"
        assert len(rows) == len(saved_run['issues']) + 1
        assert client.get('/api/runs/999/xlsx').status_code == 404
        assert client.get('/api/runs/999/csv').status_code == 404


def test_details_add_only_two_columns_to_existing_findings_sheet(saved_run):
    saved_run['issues'] = [
        {
            'severity': 'warning', 'code': 'KD-PE-105', 'file': 'demo.ПЭ3.pdf',
            'location': 'Э3: листы PDF 1, 2; ПЭ3: лист PDF 2, строка таблицы 5',
            'message': 'На схеме обнаружены обозначения, отсутствующие в перечне элементов.',
            'recommendation': 'Проверьте перечень и подтвердите решение инженером.',
            'details': {
                'schema_version': 1,
                'kind': 'e3_pe3_missing_designations',
                'status': 'evaluated',
                'confidence': 'high',
                'e3_document': 'demo.Э3.pdf',
                'pe3_document': 'demo.ПЭ3.pdf',
                'e3_pages': [1, 2],
                'pe3_page': 2,
                'pe3_table_row': 5,
                'pe3_quantity': 9,
                'pe3_entries_count': 7,
                'pe3_name': 'Трансформатор тока измерительный',
                'missing': [
                    {
                        'designation': '3TA2', 'raw': '3TA2', 'pages': [1, 2],
                        'kinds': ['label', 'reference'],
                        'evidence': [{'page': 1, 'kind': 'label', 'context': '3TA2'}],
                    },
                    {
                        'designation': '3TA3', 'raw': '3TA3', 'pages': [1, 2],
                        'kinds': ['label'],
                        'evidence': [{'page': 1, 'kind': 'label', 'context': '3TA3'}],
                    },
                ],
                'ambiguous': [],
                'reason': 'quantity_mismatch',
            },
        },
        {
            'severity': 'info', 'code': 'TEST-2', 'file': 'demo.pdf',
            'location': 'Лист 1', 'message': 'Искусственное замечание',
            'recommendation': 'Проверить тестовые данные',
        },
    ]
    with closing(load_workbook(BytesIO(build_excel_report(saved_run)))) as book:
        assert book.sheetnames == ['Общие сведения', 'Замечания']
        sheet = book['Замечания']
        header = list(sheet.values)[0]
        assert header[:10] == tuple(HEADERS)
        assert header[10:] == ('Доказательства KD-PE-105', 'Детали KD-PE-105 (JSON)')
        assert '3TA2' in sheet['K2'].value and '3TA3' in sheet['K2'].value
        assert json.loads(sheet['L2'].value)['missing'][0]['designation'] == '3TA2'
        assert sheet['K2'].data_type == 's' and sheet['L2'].data_type == 's'
        assert sheet['K3'].value in (None, '') and sheet['L3'].value in (None, '')
        assert sheet.auto_filter.ref == 'A1:L3'

