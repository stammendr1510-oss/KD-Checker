"""Synthetic checks for storage compatibility and failed-upload provenance."""
import pytest
from fastapi.testclient import TestClient

import app as web
import secure_backend
from test_full_set_completeness import pdf


@pytest.fixture
def checklist(monkeypatch):
    snapshot = {'status': 'available', 'items': [], 'reviews': []}
    monkeypatch.setattr(web, 'current_checklist_state', lambda: snapshot)
    return snapshot


def upload_csv(client):
    return client.post('/api/check', files=[
        ('files', ('synthetic.csv', b'Designation;Quantity\nZR8;1\n', 'text/csv')),
    ])


def test_storage_callback_accepting_keywords_receives_all_metadata(monkeypatch, checklist):
    calls = []

    def save(names, issues, **metadata):
        calls.append((names, issues, metadata))
        return 23

    monkeypatch.setattr(web, 'save_run', save)
    with TestClient(web.app) as client:
        result = upload_csv(client).json()
    assert result['id'] == 23
    assert len(calls) == 1
    assert calls[0][2] == dict(checklist=checklist,
                             extracted_entities=result['extracted_entities'],
                             app_metadata=result['application'])


def test_storage_callback_with_legacy_checklist_parameter(monkeypatch, checklist):
    calls = []

    def save(names, issues, checklist=None):
        calls.append(checklist)
        return 24

    monkeypatch.setattr(web, 'save_run', save)
    with TestClient(web.app) as client:
        assert upload_csv(client).json()['id'] == 24
    assert calls == [checklist]


def test_storage_internal_typeerror_is_not_retried(monkeypatch, checklist):
    calls = []

    def save(names, issues, **metadata):
        calls.append(names)
        raise TypeError('storage implementation failure')

    monkeypatch.setattr(web, 'save_run', save)
    with TestClient(web.app) as client:
        with pytest.raises(TypeError, match='storage implementation failure'):
            upload_csv(client)
    assert len(calls) == 1


@pytest.mark.parametrize('entrypoint', ['local', 'scoped'])
def test_unreadable_upload_is_present_but_never_reparsed(entrypoint, monkeypatch, checklist):
    import json

    reference = 'QWER.987654.321.Э3'
    files = [('ASDF.123456.789.СП.pdf', pdf([reference])),
             (reference + '.pdf', b'%PDF-1.7\ntruncated')]
    if entrypoint == 'local':
        monkeypatch.setattr(web, 'save_run', lambda names, issues: 25)
        with TestClient(web.app) as client:
            findings = client.post('/api/check', files=[
                ('files', (name, content, 'application/pdf')) for name, content in files
            ]).json()['issues']
    else:
        cfg = json.loads(web.CONFIG.read_text(encoding='utf-8'))
        findings = secure_backend.check_documents(files, cfg)
    assert sum(f['code'] == 'KD-000' for f in findings) == 1
    assert not [f for f in findings if f['code'] in
                {'KD-SET-203', 'KD-EM-108-2', 'KD-EM-000'}]
