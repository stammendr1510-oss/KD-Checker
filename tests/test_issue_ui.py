"""Real browser + local HTTP server, with synthetic CSV and temporary SQLite only."""
from contextlib import closing
from io import BytesIO
import os
import socket
import threading
import time

from openpyxl import load_workbook
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
        deadline = time.monotonic()+15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started, 'Local UI test server did not start'
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive()


def test_engineer_workflow_history_recheck_conflict_and_export(live_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('KD_TEST_CHROMIUM') or None)
        try:
            context = browser.new_context()
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(live_url)

            def check():
                page.locator('#files').set_input_files({
                    'name': 'demo.csv', 'mimeType': 'text/csv',
                    'buffer': 'Позиция;Обозначение;Наименование\n1;DEMO.002;Искусственная деталь\n'.encode('utf-8'),
                })
                with page.expect_response(lambda r: r.url.endswith('/api/check') and r.request.method == 'POST') as response:
                    page.get_by_role('button', name='Начать проверку', exact=True).click()
                record = response.value.json()
                expect(page.locator('#results .finding-editor').first).to_be_visible()
                return record['id']

            def save(editor, status, comment):
                editor.locator('select').select_option(status)
                editor.locator('textarea').fill(comment)
                with page.expect_response(lambda r: '/findings/' in r.url and r.request.method == 'PATCH') as changed:
                    editor.get_by_role('button', name='Сохранить решение', exact=True).click()
                assert changed.value.status == 200
                expect(editor.locator('.finding-message')).to_contain_text('Решение сохранено')

            first = check()
            editor = page.locator('#results .finding-editor').first
            expect(editor.locator('select')).to_have_value('new')
            finding_id = int(editor.get_attribute('data-finding-id'))
            csv_before = context.request.get(f'{live_url}/api/runs/{first}/csv').body()
            hostile = '<img src=x onerror="window.injected=true">'
            save(editor, 'in_progress', hostile)
            save(editor, 'resolved', '=1+1')
            page.reload()
            page.get_by_role('button', name='История', exact=True).click()
            page.locator('#historyList').get_by_role('button', name=str(first), exact=True).click()
            editor = page.locator('#historyList .finding-editor').first
            expect(editor.locator('select')).to_have_value('resolved')
            expect(editor.locator('textarea')).to_have_value('=1+1')
            editor.get_by_role('button', name='История изменений', exact=True).click()
            expect(editor.locator('.finding-audit')).to_contain_text('Новое → В работе')
            expect(editor.locator('.finding-audit')).to_contain_text('В работе → Устранено')
            expect(editor.locator('.finding-audit')).to_contain_text(hostile)
            assert page.locator('.finding-audit img').count() == 0
            assert page.evaluate('window.injected') is None
            assert context.request.get(f'{live_url}/api/runs/{first}/csv').body() == csv_before

            with page.expect_download() as download:
                page.locator('#historyList').get_by_role('link', name='Скачать отчёт Excel', exact=True).click()
            item = download.value
            assert item.suggested_filename == f'kd-report-{first}.xlsx'
            with closing(load_workbook(BytesIO(item.path().read_bytes()))) as book:
                assert book['Замечания']['H2'].value == 'Устранено'
                assert book['Замечания']['I2'].value == '=1+1'
                assert book['Замечания']['I2'].data_type == 's'
                assert book['Замечания']['J2'].value

            page.get_by_role('button', name='Проверка комплекта', exact=True).click()
            # After a reload, no stale results can satisfy the next visibility assertion.
            page.reload()
            second = check()
            assert second != first
            editor = page.locator('#results .finding-editor').first
            expect(editor.locator('select')).to_have_value('new')
            expect(editor.locator('textarea')).to_have_value('')
            expect(editor.locator('.finding-prior')).to_contain_text(f'проверкой №{first}')
            expect(editor.locator('.finding-prior')).to_contain_text('Устранено')
            save(editor, 'resolved', 'Инженер подтвердил для новой проверки')
            editor.locator('select').select_option('rejected')
            editor.locator('textarea').fill('')
            editor.get_by_role('button', name='Сохранить решение', exact=True).click()
            expect(editor.locator('.finding-message')).to_contain_text('укажите обоснование')
            save(editor, 'rejected', 'Обоснование решения инженера')
            assert context.request.get(f'{live_url}/api/runs/{first}').json()['finding_states'][0]['status'] == 'resolved'

            # Simulate a second engineer's tab holding the same version.
            other = context.new_page()
            other.goto(live_url)
            other.get_by_role('button', name='История', exact=True).click()
            other.locator('#historyList').get_by_role('button', name=str(second), exact=True).click()
            stale = other.locator('.finding-editor').first
            expect(stale.locator('select')).to_have_value('rejected')
            save(editor, 'in_progress', 'Актуальное решение')
            stale.locator('textarea').fill('Устаревшее решение')
            stale.get_by_role('button', name='Сохранить решение', exact=True).click()
            expect(stale.locator('.finding-message')).to_contain_text('уже изменено')
            assert context.request.get(f'{live_url}/api/runs/{second}').json()['finding_states'][0]['comment'] == 'Актуальное решение'
            assert len(context.request.get(f'{live_url}/api/runs/{first}/findings/{finding_id}/history').json()) == 2
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize('width,height', [(360, 800), (768, 1024), (1366, 768), (1920, 1080)])
def test_russian_report_cards_responsive_history_and_exports(live_url, monkeypatch, tmp_path, width, height):
    filename = 'demo_'+'LongFileDesignation'*10+'.csv'
    levels = ['error', 'warning', 'info']
    labels = ['Ошибка', 'Предупреждение', 'Информация']
    issues = [dict(severity=level, code=f'DEMO-{index}', file=filename, location='Лист 2',
                   message='Искусственное описание\n'+'ДлинныйТекстБезПробелов'*20,
                   recommendation='Сопоставить исходную запись и результат проверки. '*8)
              for index, level in enumerate(levels)]
    monkeypatch.setattr(web, 'analyze', lambda docs, config: issues)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('KD_TEST_CHROMIUM') or None)
        try:
            context = browser.new_context(viewport={'width': width, 'height': height})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(live_url)
            page.locator('#files').set_input_files({'name': filename, 'mimeType': 'text/csv', 'buffer': b'one;two\n1;2\n'})
            with page.expect_response(lambda r: r.url.endswith('/api/check') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='Начать проверку', exact=True).click()
            result = response.value.json()
            assert result['summary'] == dict(error=1, warning=1, info=1)
            assert [item['severity'] for item in result['issues']] == levels
            expect(page.locator('#results .issue-card')).to_have_count(3)
            assert page.locator('#results .issue-level').all_text_contents() == labels
            assert [page.locator('#results .issue-level').nth(i).get_attribute('data-severity') for i in range(3)] == levels
            assert page.locator('#results .issue-file').all_text_contents() == [filename]*3
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            assert page.locator('#results .issue-card').evaluate_all('(cards)=>cards.every(c=>c.scrollWidth<=c.clientWidth+1)')
            if width >= 1200:
                assert page.locator('#results .issue-content').first.bounding_box()['width'] >= 500
            assert page.locator('#results .issue-file').first.bounding_box()['height'] > 24
            run_id = result['id']
            original = context.request.get(f'{live_url}/api/runs/{run_id}').json()
            csv_before = context.request.get(f'{live_url}/api/runs/{run_id}/csv').body()
            editor = page.locator('#results .finding-editor').first
            editor.locator('select').select_option('in_progress')
            comment = '=1+1\nКомментарий инженера: '+'ПримерБезПробелов'*30
            editor.locator('textarea').fill(comment)
            with page.expect_response(lambda r: '/findings/' in r.url and r.request.method == 'PATCH') as saved:
                editor.get_by_role('button', name='Сохранить решение', exact=True).click()
            assert saved.value.status == 200
            expect(editor.locator('.finding-message')).to_contain_text('Решение сохранено')
            page.reload()
            page.get_by_role('button', name='История', exact=True).click()
            page.locator('#historyList').get_by_role('button', name=str(run_id), exact=True).click()
            expect(page.locator('#historyList .issue-card')).to_have_count(3)
            assert page.locator('#historyList .issue-level').all_text_contents() == labels
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            editor = page.locator('#historyList .finding-editor').first
            expect(editor.locator('select')).to_have_value('in_progress')
            expect(editor.locator('textarea')).to_have_value(comment)
            editor.get_by_role('button', name='История изменений', exact=True).click()
            expect(editor.locator('.finding-audit')).to_contain_text('Новое → В работе')
            expect(editor.locator('.finding-audit')).to_contain_text(comment)
            assert page.locator('#historyList .issue-card').evaluate_all('(cards)=>cards.every(c=>c.scrollWidth<=c.clientWidth+1)')
            page.screenshot(path=str(tmp_path / f'report-{width}.png'), full_page=True)
            with page.expect_download() as download:
                page.locator('#historyList').get_by_role('link', name='Скачать отчёт Excel', exact=True).click()
            with closing(load_workbook(BytesIO(download.value.path().read_bytes()))) as book:
                sheet = book['Замечания']
                assert [sheet.cell(row, 2).value for row in (2, 3, 4)] == labels
                assert sheet['D2'].value == filename
                assert sheet['H2'].value == 'В работе'
                assert sheet['I2'].value == comment and sheet['I2'].data_type == 's'
            with page.expect_download() as download:
                page.locator('#historyList').get_by_role('link', name='Скачать отчёт CSV', exact=True).click()
            assert download.value.path().read_bytes() == csv_before
            final = context.request.get(f'{live_url}/api/runs/{run_id}').json()
            assert final['issues'] == original['issues'] == issues
            assert final['finding_states'][0]['fingerprint'] == original['finding_states'][0]['fingerprint']
            assert errors == []
        finally:
            browser.close()
