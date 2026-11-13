"""Severity regressions using generated PDFs only."""
import fitz
import pytest
from fastapi.testclient import TestClient

import app as web


def generated_pdf(quantity):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((200, 250), 'Synthetic engineering test document only')
        for y in (70, 130):
            page.draw_line((50, y), (480, y))
        page.insert_text((60, 90), '1KM1;1KM2', fontsize=6)
        page.insert_text((438, 90), str(quantity), fontsize=6)
        return pdf.tobytes()


@pytest.mark.parametrize('quantity, expected_warning', [(2, 0), (3, 1)])
def test_api_severity_counts_and_recommendations(monkeypatch, quantity, expected_warning):
    monkeypatch.setattr(web, 'save_run', lambda names, issues: 42)
    content = generated_pdf(quantity)
    with TestClient(web.app) as client:
        response = client.post('/api/check', files=[
            ('files', ('demo.Э3.pdf', content, 'application/pdf')),
            ('files', ('demo.ПЭ3.pdf', content, 'application/pdf')),
            ('files', ('unsupported.txt', b'synthetic', 'text/plain')),
        ])
    assert response.status_code == 200
    result = response.json()
    assert result['summary'] == {'error': 1, 'warning': expected_warning, 'info': 1}
    assert sum(result['summary'].values()) == len(result['issues'])
    findings = {finding['code']: finding for finding in result['issues']}
    assert findings['KD-000']['severity'] == 'error'
    assert findings['KD-PE-100']['severity'] == 'info'
    assert 'исходные Э3 и ПЭ3' in findings['KD-PE-100']['recommendation']
    if expected_warning:
        finding = findings['KD-PE-102']
        assert finding['severity'] == 'warning'
        assert 'проверка инженером' in finding['recommendation']
        assert 'не подтверждённая ошибка КД' in finding['recommendation']
    else:
        assert 'KD-PE-102' not in findings
