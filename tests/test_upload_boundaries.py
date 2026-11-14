"""Upload rejection/acceptance contracts with tiny synthetic limits, no network."""
import pytest
from fastapi.testclient import TestClient

import app as web
import storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB', tmp_path / 'history.sqlite3')
    monkeypatch.setattr(web, 'MAX_FILE', 128)
    monkeypatch.setattr(web, 'MAX_TOTAL', 200)
    monkeypatch.setattr(web, 'MAX_FILES', 2)
    monkeypatch.setattr(web, 'current_checklist_state', lambda: {
        'status': 'not_configured', 'reason': 'Synthetic offline mode',
        'revision': None, 'retrieved_at': None, 'items': [], 'reviews': [],
    })
    with TestClient(web.app) as value:
        yield value


def upload(name, size):
    return ('files', (name, b'a' * size, 'text/csv'))


@pytest.mark.parametrize('files', [
    [upload('synthetic.csv', 129)],
    [upload('first.csv', 101), upload('second.csv', 100)],
    [upload('first.csv', 1), upload('second.csv', 1), upload('third.csv', 1)],
])
def test_exceeded_limit_rejects_entire_request_without_saving_partial_history(client, files):
    response = client.post('/api/check', files=files)
    assert response.status_code == 413
    # No save_run call, including when the first file was successfully parsed.
    assert not storage.DB.exists()


@pytest.mark.parametrize('files', [
    [upload('synthetic.csv', 128)],
    [upload('first.csv', 100), upload('second.csv', 100)],
])
def test_exact_file_total_and_count_boundaries_remain_accepted(client, files):
    response = client.post('/api/check', files=files)
    assert response.status_code == 200
    result = response.json()
    assert len(result['files']) == len(files)
    assert 'KD-000' not in [issue['code'] for issue in result['issues']]
    assert storage.get_run(result['id'])['files'] == [item[1][0] for item in files]


def test_parser_exception_is_redacted_in_response_history_and_reports(client, monkeypatch):
    sentinel = 'SYNTHETIC-PRIVATE-EXCEPTION-DETAIL'
    def fail(*args):
        raise RuntimeError(sentinel)
    monkeypatch.setattr(web, 'extract', fail)
    response = client.post('/api/check', files=[upload('synthetic.csv', 10)])
    assert response.status_code == 200
    result = response.json()
    assert result['files'] == []
    assert [issue['code'] for issue in result['issues']] == ['KD-000']
    assert sentinel not in response.text
    for suffix in ('', '/csv'):
        saved = client.get(f"/api/runs/{result['id']}{suffix}")
        assert saved.status_code == 200
        assert sentinel not in saved.text
    from contextlib import closing
    from io import BytesIO
    from openpyxl import load_workbook
    report = client.get(f"/api/runs/{result['id']}/xlsx")
    assert report.status_code == 200
    with closing(load_workbook(BytesIO(report.content))) as book:
        assert all(sentinel not in str(cell.value) for sheet in book for row in sheet for cell in row)
