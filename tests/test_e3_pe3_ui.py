"""Browser regression: KD-PE-105 structured details are visible to the engineer."""
from contextlib import closing
import os
import socket
import threading
import time

from playwright.sync_api import expect, sync_playwright
import pytest
import uvicorn

import app as web
import storage


@pytest.fixture
def live_url(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'browser-history.sqlite3')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(web.app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started, 'Local UI test server did not start'
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.ui
def test_kd_105_details_are_visible_in_history(live_url):
    issue = {
        'severity': 'warning',
        'code': 'KD-PE-105',
        'file': 'demo.ПЭ3.pdf',
        'location': 'Э3: листы PDF 1, 2; ПЭ3: лист PDF 2, строка таблицы 5',
        'message': 'На схеме обнаружены обозначения, отсутствующие в перечне элементов.',
        'recommendation': 'Проверьте перечень и подтвердите решение инженером.',
        'details': {
            'schema_version': 1,
            'kind': 'e3_pe3_missing_designations',
            'status': 'evaluated',
            'confidence': 'high',
            'e3_document': 'demo.Э3.pdf',
            'e3_pages': [1, 2],
            'pe3_document': 'demo.ПЭ3.pdf',
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
    }
    run_id = storage.save_run(['demo.Э3.pdf', 'demo.ПЭ3.pdf'], [issue])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('KD_TEST_CHROMIUM') or None)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(live_url)
            page.get_by_role('button', name='История', exact=True).click()
            page.locator('#historyList').get_by_role('button', name=str(run_id), exact=True).click()
            expect(page.locator('.issue-details')).to_contain_text('Структурированные сведения')
            expect(page.locator('.issue-details')).to_contain_text('3TA2')
            expect(page.locator('.issue-details')).to_contain_text('3TA3')
            expect(page.locator('.issue-details')).to_contain_text('лист PDF 2')
            expect(page.locator('.issue-details')).to_contain_text('Трансформатор тока измерительный')
            expect(page.locator('.issue-details')).to_contain_text('Характер расхождения')
            expect(page.locator('.issue-details')).to_contain_text('Почему такая достоверность')
            expect(page.locator('.issue-details')).to_contain_text('Рекомендации инженеру')
            assert page.locator('.issue-details img').count() == 0
            assert errors == []
        finally:
            browser.close()
