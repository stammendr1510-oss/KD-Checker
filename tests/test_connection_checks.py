from dataclasses import replace
import pytest

from connection_checks import Connection, Endpoint, check_connections


def record(**changes):
    values = dict(project='synthetic-project', revision='rev-1', wire='W1', cable='',
                  source=Endpoint('X1', '1'), target=Endpoint('X2', '2'),
                  document='synthetic-export.json', location='record 1')
    return Connection(**(values | changes))


def test_duplicate_has_exact_provenance_and_standard_finding_fields():
    first = record()
    second = replace(first, document='synthetic-other.json', location='record 8')
    result = check_connections([first, second])
    assert len(result) == 1
    finding = result[0]
    assert set(finding) == {'code', 'severity', 'file', 'location', 'message', 'recommendation'}
    assert finding['code'] == 'KD-CON-101' and finding['severity'] == 'info'
    assert (finding['file'], finding['location']) == (second.document, second.location)
    assert first.document in finding['message'] and first.location in finding['message']


@pytest.mark.parametrize('changes', [
    {'project': 'other'}, {'revision': 'rev-2'}, {'wire': 'W2'}, {'cable': 'C1'},
    {'source': Endpoint('X3', '1')}, {'target': Endpoint('X2', '3')},
    {'source': Endpoint('X2', '2'), 'target': Endpoint('X1', '1')},
    {'source': Endpoint('Х1', '1')}, {'source': Endpoint('x1', '1')},
])
def test_no_inference_across_scopes_branches_direction_or_spelling(changes):
    assert check_connections([record(), record(location='record 2', **changes)]) == []


def test_same_source_occurrence_is_not_a_second_export_record():
    assert check_connections([record(), record()]) == []


def test_same_endpoint_is_only_informational():
    result = check_connections([record(target=Endpoint('X1', '1'))])
    assert [(item['code'], item['severity']) for item in result] == [('KD-CON-102', 'info')]


@pytest.mark.parametrize('value', ['', '  ', None, 12, 'a\nb', 'x'*501])
def test_invalid_endpoint_rejected(value):
    with pytest.raises(ValueError):
        Endpoint(value, '1')


@pytest.mark.parametrize('field', ['project', 'revision', 'wire', 'document', 'location'])
def test_required_provenance_and_scope(field):
    with pytest.raises(ValueError):
        record(**{field: ''})


def test_unstructured_data_is_rejected():
    with pytest.raises(ValueError):
        check_connections([{'text': 'PDF text is not topology'}])
    with pytest.raises(ValueError):
        record(source='X1:1')
    assert check_connections([]) == []


def test_normalization_is_only_nfc_and_outer_whitespace():
    assert Endpoint(' A\u0301 ', ' 1 ') == Endpoint('Á', '1')
    assert Endpoint('X1', '1') != Endpoint('Х1', '1')
