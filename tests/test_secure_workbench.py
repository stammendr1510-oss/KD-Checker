"""Browser -> real ASGI transport -> scoped SQLite; no mocked API responses.

HTTPS origin is routed in-process, so no TLS certificate exceptions or listener.
This tests browser/HTTP semantics, not a production TLS handshake.
"""
import os
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
import pytest

from secure_api import create_secure_app, COOKIE
from secure_backend import ScopedBackend
from scoped_history import Repository, Scope
from secure_sessions import Sessions, Identity

ORIGIN='https://kd.test'
CSV='позиция;обозначение;наименование\n1;SYNTH.001;A\n1;SYNTH.002;B\n'.encode('utf-8')

@pytest.fixture
def system(tmp_path):
    repo=Repository.create(tmp_path/'repo.sqlite3')
    for user,role,project in [('engineer','engineer','a'),('viewer','viewer','a'),('foreign','engineer','b')]:
        repo.provision(user,Scope('org',project),role)
    class Verifier:
        def verify(self,ticket):
            return Identity(ticket,'org','b' if ticket=='foreign' else 'a') if ticket in ('engineer','viewer','foreign') else None
    app=create_secure_app(verifier=Verifier(),backend=ScopedBackend(repo),sessions=Sessions(),origin=ORIGIN)
    with TestClient(app,base_url=ORIGIN) as client:
        yield repo,client


def test_public_shell_contains_no_data_and_context_requires_current_membership(system):
    repo,client=system
    response=client.get('/workbench')
    assert response.status_code==200
    assert 'frame-ancestors' in response.headers['content-security-policy']
    assert response.headers['cache-control']=='no-store'
    from html.parser import HTMLParser
    forms=[]
    class Forms(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag=='form': forms.append(dict(attrs))
    Forms().feed(response.text)
    assert forms and all(form.get('method','get').lower()=='post' for form in forms)
    assert client.get('/auth/context').status_code==401
    assert client.post('/workbench').status_code==401
    client.post('/auth/login',json={'credential':'engineer'},headers={'origin':ORIGIN})
    assert client.get('/auth/context').json()['project']=='a'
    repo.revoke('engineer',Scope('org','a'))
    assert client.get('/auth/context').status_code==403
    assert client.get('/workbench/../config.json').status_code in (403,404,401)


def test_browser_engineer_workflow_roles_and_no_dom_injection(system):
    repo,client=system
    errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.environ.get('KD_TEST_CHROMIUM') or None)
        context=browser.new_context(accept_downloads=True,viewport={'width':1280,'height':900})
        def bridge(route):
            request=route.request
            assert request.url.startswith(ORIGIN+'/'), 'Unexpected external browser request'
            client.cookies.clear() # Cookies belong to the browser, never to the bridge.
            headers=request.all_headers()
            response=client.request(request.method,request.url,content=request.post_data_buffer,headers=headers)
            route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
        context.route('**/*',bridge)
        page=context.new_page()
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(ORIGIN+'/workbench')
        expect(page.get_by_role('button',name='Войти',exact=True)).to_be_enabled()
        def login(user):
            page.locator('#credential').fill(user)
            page.get_by_role('button',name='Войти',exact=True).click()
            expect(page.locator('#context')).to_contain_text(user)
            expect(page.locator('#workspace')).to_be_visible()
        login('engineer')
        page.locator('input[name=revision]').fill('synthetic-rev')
        page.locator('input[name=files]').set_input_files({'name':'synthetic.csv','mimeType':'text/csv','buffer':CSV})
        page.get_by_role('button',name='Проверить документы',exact=True).click()
        expect(page.locator('#run-title')).to_have_text('Проверка №1')
        assert page.locator('#severity option').evaluate_all('(items)=>items.map(x=>[x.value,x.textContent])') == [['','Все уровни'],['error','Ошибка'],['warning','Предупреждение'],['info','Информация']]
        page.locator('#severity').select_option('error')
        expect(page.locator('.finding')).to_have_count(1)
        expect(page.locator('.finding h3')).to_contain_text('KD-011')
        card=page.locator('.finding')
        card.get_by_label('Статус замечания').select_option('resolved')
        payload='<img src=x onerror="window.syntheticLeak=1">'
        card.get_by_label('Комментарий инженера').fill(payload)
        card.get_by_role('button',name='Сохранить решение',exact=True).click()
        expect(page.locator('#message')).to_have_text('Решение сохранено.')
        card.get_by_role('button',name='История решения',exact=True).click()
        expect(card.locator('pre')).to_contain_text(payload)
        assert page.evaluate('window.syntheticLeak') is None
        assert page.locator('.finding img').count()==0
        page.locator('#status').select_option('resolved')
        expect(page.locator('.finding')).to_have_count(1)
        page.locator('#search').fill('not-a-rule')
        expect(page.locator('.finding')).to_have_count(0)
        page.locator('#search').fill('')
        page.locator('#status').select_option('')
        page.get_by_role('button',name='Проверить документы',exact=True).click()
        expect(page.locator('#run-title')).to_have_text('Проверка №2')
        expect(page.locator('.finding select')).to_have_value('new')
        page.locator('#previous').fill('1')
        page.get_by_role('button',name='Сравнить до / после',exact=True).click()
        expect(page.locator('#comparison')).to_contain_text('сохраняющиеся')
        with page.expect_download() as saved:
            page.get_by_role('link',name='Скачать CSV',exact=True).click()
        assert saved.value.suggested_filename=='kd-run-2.csv'
        page.set_viewport_size({'width':360,'height':800})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_role('button',name='Выйти',exact=True).click()
        expect(page.locator('#workspace')).to_be_hidden()
        expect(page.locator('#runs')).to_be_empty()
        login('viewer')
        page.locator('#runs button').filter(has_text='№2').click()
        expect(page.locator('#run-title')).to_have_text('Проверка №2')
        page.locator('.finding').first.get_by_role('button',name='Сохранить решение',exact=True).click()
        expect(page.locator('#message')).to_contain_text('Доступ запрещён')
        expect(page.locator('#workspace')).to_be_hidden()
        login('foreign')
        expect(page.locator('#runs')).to_contain_text('пока нет')
        assert page.evaluate("fetch('/api/runs/1/xlsx').then(r=>r.status)")==404
        assert errors==[]
        context.close()
        browser.close()
