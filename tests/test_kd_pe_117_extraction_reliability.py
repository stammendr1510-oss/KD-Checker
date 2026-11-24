"""Synthetic offline regression evidence for KD-PE-117, including real HTTP/export."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
import subprocess

import fitz
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

import app as web
import storage
import naming
from adapters import (build_model, deduplicate_findings, engineering_analysis,
                      model_from_xlsx)
from build_metadata import build_metadata
from connection_import import classify_connection_row, route_xlsx
from engineering_graph import build_graph
from engineering_model import verify_coverage
from kd_crosscheck import expand_notation, scheme_designations
from e3_pe3_inventory import base_of

HEADERS = ['Лист №', 'Откуда', 'Номер контакта', 'Куда', 'Номер контакта',
           'Цвет', 'Поперечное сечение', 'Наименование кабеля', 'Тип провода', 'Соединение']


def workbook(rows, headers=HEADERS):
    with closing(Workbook()) as book:
        sheet = book.active
        sheet.title = 'Произвольный лист'
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        output = BytesIO()
        book.save(output)
    return output.getvalue()


def wire(source='ZR7', contact='11', target='LM8', terminal='12', mark='W-51'):
    return [1, source, contact, target, terminal, 'синий', '1.5', 'CB-8', 'медь', mark]


def test_positional_duplicate_headers_and_graph():
    content = workbook([wire(), wire('BUS', '0', 'ZR8', '2', 'W-52')])
    routed = route_xlsx(content)
    sheet = routed.sheets[0]
    assert sheet.role == 'connections'
    assert sheet.records[0]['source_terminal'] == '11'
    assert sheet.records[0]['target_terminal'] == '12'
    model = model_from_xlsx(content, 'случайное-имя.xlsx', routed=routed)
    assert len(model.connections) == 2
    assert ('BUS', '0') in build_graph(model.connections)


@pytest.mark.parametrize('row,status', [
    (wire(), 'extracted'), (wire(contact=None), 'incomplete'),
    (wire(mark=''), 'incomplete'), (wire(source='ZR7;ZR8'), 'ambiguous'),
    (wire(contact='x'*501), 'rejected'), (wire(contact='\x7f'), 'rejected'),
])
def test_connection_classification_has_reasons(row, status):
    keys = ['sheet_number', 'source_device', 'source_terminal', 'target_device',
            'target_terminal', 'color', 'cross_section', 'cable', 'wire_type', 'wire']
    actual, reason = classify_connection_row(dict(zip(keys, row)))
    assert actual == status and reason


@pytest.mark.parametrize('raw,expected,unknown', [
    ('BUS', {'BUS'}, []), ('R', {'R'}, []), ('Ш', {'Ш'}, []),
    ('ZR1\nZR2,LM3...LM5;ZR4', {'ZR1', 'ZR2', 'LM3', 'LM4', 'LM5', 'ZR4'}, []),
    ('ZR\n12', {'ZR12'}, []), ('ZR1\n2', set(), ['ZR1\n2']),
    ('ZR1;UNKNOWN', {'ZR1'}, ['UNKNOWN']),
])
def test_all_consumers_share_notation(raw, expected, unknown):
    assert naming.expand_notation is expand_notation
    assert expand_notation(raw) == (expected, unknown)


def test_subdesignation_parent_independent_of_configured_classes():
    assert base_of('ZR7.1') == naming.base_designator('ZR7.1') == 'ZR7'


def pdf(text):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 80), text)
        return doc.tobytes()


def test_letter_label_corroboration_is_upload_order_independent():
    pe = workbook([['BUS', 1, 'Шина']], ['Обозначение', 'Количество', 'Наименование'])
    sources = [('СИНТ.123456.001.Э3.pdf', pdf('BUS HEADER')), ('таблица.xlsx', pe)]
    for batch in (sources, list(reversed(sources))):
        model = build_model(batch)
        assert {m.designation.raw for m in model.scheme_mentions} == {'BUS'}
        assert verify_coverage(model) == []
    with fitz.open(stream=pdf('HEADER'), filetype='pdf') as doc:
        assert not list(scheme_designations(doc[0]))


def finding(code, severity='warning', page=1, row=2, counts=(3, 2)):
    return dict(code=code, severity=severity, file='СИНТ.ПЭ3.pdf',
                location=f'лист {page}, строка {row}',
                message=f'количество {counts[0]} отличается от числа распознанных обозначений ({counts[1]}) в записи «ZR1;ZR2»',
                recommendation='Проверьте строку')


def test_semantic_dedup_retains_rules_refs_and_distinct_defects():
    a = finding('KD-PE-102', 'info')
    b = finding('KD-EM-108-1')
    b['location'] = 'СИНТ.ПЭ3.pdf, лист 1, строка 2'
    b['source_refs'] = [{'document': 'СИНТ.ПЭ3.pdf', 'page': 1, 'row': 2}]
    result = deduplicate_findings([a, b, finding('KD-EM-108-1', row=3),
                                   finding('KD-EM-108-1', counts=(4, 2)),
                                   finding('KD-EM-108-1', page=2)])
    assert len(result) == 1
    assert result[0]['severity'] == 'warning'
    assert result[0]['related_codes'] == ['KD-EM-108-1', 'KD-PE-102']
    assert len(result[0]['source_refs']) == 5
    assert deduplicate_findings(result) == result
    assert 'related_codes' not in a


def test_build_metadata_live_and_safe_fallback(monkeypatch):
    root = Path(web.__file__).parent
    actual = build_metadata('test')
    for key, args in [('branch', ['--abbrev-ref', 'HEAD']), ('build', ['--short', 'HEAD'])]:
        assert actual[key] == subprocess.check_output(['git', 'rev-parse', *args], cwd=root, text=True).strip()
    def unavailable(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, 'check_output', unavailable)
    monkeypatch.delenv('KD_BUILD_BRANCH', raising=False)
    monkeypatch.delenv('KD_BUILD_REF', raising=False)
    assert build_metadata('test') == dict(version='test', branch='не определена', build='локальная сборка')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    monkeypatch.setattr(web, 'current_checklist_state', lambda: {'status': 'not_configured'})
    with TestClient(web.app) as client:
        yield client


@pytest.mark.parametrize('rows,counts', [
    ([wire()], (1, 0, 0, 0)),
    ([wire(), wire(contact=None), wire(source='ZR7;ZR8'), wire(contact='x'*501)], (1, 1, 1, 1)),
])
def test_http_completeness_history_and_xlsx(client, rows, counts):
    response = client.post('/api/check', files={'files': ('новая-таблица.xlsx', workbook(rows))})
    assert response.status_code == 200
    result = response.json()
    summary = result['extracted_entities']
    document = summary['documents'][0]
    assert document['raw_rows'] == len(rows)
    assert tuple(document[k] for k in ('extracted', 'incomplete', 'ambiguous', 'rejected')) == counts
    assert len(document['reasons']) == len(rows) - counts[0]
    assert summary['connections'] == counts[0]
    if len(rows) == 1:
        assert result['issues'] == []  # clean package: zero false positives
    saved = client.get(f"/api/runs/{result['id']}").json()
    assert saved['extracted_entities'] == summary
    assert saved['app_metadata'] == result['application']
    exported = client.get(f"/api/runs/{result['id']}/xlsx")
    assert exported.status_code == 200
    with closing(load_workbook(BytesIO(exported.content))) as book:
        assert 'Полнота извлечения' in book.sheetnames
        row = list(book['Полнота извлечения'].values)[1]
        assert row[0] == 'новая-таблица.xlsx'
        assert tuple(map(int, row[4:8])) == counts
        assert result['application']['branch'] in str(list(book['Извлечение'].values))


def test_unknown_headers_are_accounted_and_pdf_raw_count_is_unknown():
    unknown = workbook([['данные', 8]], ['Произвольный', 'Столбец'])
    _, summary = engineering_analysis([('неизвестная.xlsx', unknown), ('лист.pdf', pdf('some text'))])
    xlsx, pdf_doc = summary['documents']
    assert xlsx['raw_rows'] == xlsx['rejected'] == 2
    assert xlsx['confidence_limits']
    assert pdf_doc['raw_rows'] is None
    assert pdf_doc['confidence_limits']


@pytest.mark.parametrize('rows', [
    [['ZR1–ZR3', 3, 'Модули']],
    [['BUS', 1, 'Шина']],
    [['ZR7.1', 1, 'Модуль'], ['ZR7.2', 4, 'Комплект крепления']],
])
def test_clean_element_packages_have_zero_false_positives(client, rows):
    content = workbook(rows, ['Обозначение', 'Количество', 'Наименование'])
    response = client.post('/api/check', files={'files': ('СИНТ.123456.001.ПЭ3.xlsx', content)})
    assert response.status_code == 200
    result = response.json()
    assert result['issues'] == []
    assert result['extracted_entities']['spec_rows'] == len(rows)
    assert client.get(f"/api/runs/{result['id']}/xlsx").status_code == 200


def test_dedup_keeps_distinct_conflicts_without_shared_evidence():
    first = finding('KD-EM-109-1')
    second = dict(first, message='ZR1: конфликт другой клеммы')
    assert len(deduplicate_findings([first, second])) == 2
    second = dict(first, message=first['message'] + ' Дополнительный независимый дефект')
    assert len(deduplicate_findings([first, second])) == 2


def test_graph_coverage_matches_parent_without_collapsing_electrical_nodes():
    from engineering_graph import verify_dangling_devices
    from engineering_model import Designation, SourceRef
    model = model_from_xlsx(workbook([wire('ZR7', '11', 'LM8', '12')]), 'провода.xlsx')
    model.add_device(Designation.classify('ZR7.1'), SourceRef('перечень.xlsx', row=2))
    assert verify_dangling_devices(model) == []
    assert ('ZR7', '11') in build_graph(model.connections)
    assert ('ZR7.1', '11') not in build_graph(model.connections)
