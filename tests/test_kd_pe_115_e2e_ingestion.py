"""KD-PE-115 real HTTP ingestion regressions with generated multipart files."""
from contextlib import closing
from io import BytesIO

import fitz
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
import pytest

import app as web
from adapters import deduplicate_findings
import naming
import storage


@pytest.fixture(autouse=True)
def unseen_reference_classes():
    naming.configure_reference_classes(r'(?:UV|RC|LM)')
    yield
    naming.reset_reference_classes()


def _wire_workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Neutral export'
    sheet.append([
        'Source device', 'Source terminal', 'Target device', 'Target terminal',
        'Wire mark', 'Cable', 'Wire color', 'Cross-section',
    ])
    sheet.append(['UV1.1', '11', 'LM7', '12', 'W-901', 'C-44', 'Blue', '1.5'])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _pe3_pdf():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 130, 190):
            page.draw_line((50, y), (480, y))
        for x in (50, 115, 420, 455, 480):
            page.draw_line((x, 70), (x, 190))
        page.insert_text((60, 60), 'Poz.', fontsize=6)
        page.insert_text((458, 60), 'Kol.', fontsize=6)
        page.insert_text((60, 88), 'UV1.1\nUV2.1', fontsize=6)
        page.insert_text((460, 96), '2', fontsize=6)
        page.insert_text((60, 148), 'RC4;RC5', fontsize=6)
        page.insert_text((460, 156), '2', fontsize=6)
        return pdf.tobytes()


def _e3_pdf():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((72, 100), 'UV1.1 UV2.1 RC4 RC5 LM7')
        return pdf.tobytes()


def test_multipart_router_pdf_groups_persistence_and_excel(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    monkeypatch.setattr(web, 'current_checklist_state', lambda: {
        'status': 'not_configured', 'reason': 'Тестовый источник не настроен',
        'revision': None, 'retrieved_at': None, 'items': [], 'reviews': [],
    })
    files = [
        ('files', ('unseen-wire-table.xlsx', _wire_workbook(),
                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
        ('files', ('ГХ.9917.2846.Э3.pdf', _e3_pdf(), 'application/pdf')),
        ('files', ('ГХ.9917.2846.ПЭ3.pdf', _pe3_pdf(), 'application/pdf')),
    ]
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=files)
        assert response.status_code == 200
        result = response.json()
        assert not [finding for finding in result['issues']
                    if finding['code'] == 'KD-010'
                    and finding['file'] == 'unseen-wire-table.xlsx']
        assert result['issues'] == deduplicate_findings(result['issues'])

        extracted = result['extracted_entities']
        assert extracted['connections'] == 1
        assert extracted['spec_rows'] == 2
        assert extracted['devices'] >= 4
        rows = extracted['preview']['pe3_rows']
        assert {'UV1.1', 'UV2.1'} <= set(rows[0]['designation'].split())
        assert rows[1]['designation'] == 'RC4;RC5'
        connection = extracted['preview']['connections'][0]
        assert (connection['color'], connection['cross_section']) == ('Blue', '1.5')

        saved = client.get(f"/api/runs/{result['id']}").json()
        assert saved['extracted_entities'] == extracted
        assert saved['app_metadata'] == result['application']

        report = client.get(f"/api/runs/{result['id']}/xlsx")
        assert report.status_code == 200
        with closing(load_workbook(BytesIO(report.content))) as workbook:
            assert 'Извлечение' in workbook.sheetnames
            values = list(workbook['Извлечение'].values)
            assert any(row[:2] == ('Соединения', '1') for row in values)
            assert any(row[0] == 'Версия приложения' for row in values)
            assert any('Blue' in row for row in values if row)
