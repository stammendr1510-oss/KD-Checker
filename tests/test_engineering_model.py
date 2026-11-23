"""Designation-agnostic synthetic tests for KD-PE-108 (engineering model).

All designator classes (ZZ, MQ, PT, ...) are invented for fixtures; no rule
special-cases any real class. Positive, negative, and ambiguous scenarios per
mechanism, plus SourceRef provenance checks.
"""
import pytest

import naming
from connection_checks import Connection, Endpoint
from engineering_model import (
    Designation, DocumentRef, EngineeringModel, SchemeMention, SourceRef,
    SpecRow, run_verifiers, verify_ambiguous_identities, verify_document_refs,
    verify_row_quantities,
)
import adapters


@pytest.fixture()
def synthetic_classes():
    naming.configure_reference_classes(r'(?:ZZ|MQ|PT)')
    yield
    naming.reset_reference_classes()


def _ref(doc='ПРИМЕР.ПЭ3.pdf', page=2, row=5, bounds=(1.0, 2.0, 3.0, 4.0)):
    return SourceRef(document=doc, page=page, row=row, bounds=bounds)


def _row(raw, qty, entries, unknown=(), ref=None):
    return SpecRow(raw_text=raw, quantity=qty,
                   designations=[Designation.classify(d) for d in entries],
                   unknown=list(unknown), provenance=ref or _ref())


# --- Designation normalization ---------------------------------------------

def test_designation_alphabet_classification():
    assert Designation.classify('ZZ1').alphabet == 'lat'
    mixed = Designation.classify('МQ1')  # Cyrillic М inside
    assert mixed.alphabet == 'mixed'
    assert mixed.normalized == 'MQ1'


# --- EM-108-1: quantity vs expanded designations ---------------------------

def test_em1081_positive_consistent_rows(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('ZZ1;ZZ2;ZZ3', 3, ['ZZ1', 'ZZ2', 'ZZ3']))
    assert verify_row_quantities(model) == []


def test_em1081_negative_quantity_mismatch(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('ZZ1...ZZ4', 3, ['ZZ1', 'ZZ2', 'ZZ3', 'ZZ4'],
                                ref=_ref(page=7, row=11)))
    out = verify_row_quantities(model)
    assert len(out) == 1
    f = out[0]
    assert f['severity'] == 'warning' and f['code'] == 'KD-EM-108-1'
    assert 'лист 7' in f['location'] and 'строка 11' in f['location']
    assert '4' in f['message'] and '3' in f['message']


def test_em1081_ambiguous_partial_expansion(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('ZZ1...ZZ2; ?', 2, ['ZZ1', 'ZZ2'], unknown=['?']))
    assert verify_row_quantities(model) == []  # unknown fragments don't force findings


def test_em1081_provenance_carried_to_finding(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('MQ1;MQ2', 5, ['MQ1', 'MQ2'], ref=_ref()))
    (f,) = verify_row_quantities(model)
    assert f['location'] == 'ПРИМЕР.ПЭ3.pdf, лист 2, строка 5'
    assert f['file'] == 'ПРИМЕР.ПЭ3.pdf'


# --- EM-108-2: document refs presence ---------------------------------------

def test_em1082_positive_ref_present(synthetic_classes):
    model = EngineeringModel()
    model.uploaded_identities.add(('Э3', 'гост.1234'.casefold()))
    model.document_refs.append(DocumentRef(kind='Э3', key='гост.1234'.casefold(),
                                           raw='ГОСТ.1234.001 Э3', provenance=_ref()))
    assert verify_document_refs(model) == []


def test_em1082_negative_ref_missing(synthetic_classes):
    model = EngineeringModel()
    model.document_refs.append(DocumentRef(kind='ВО', key='гост.9999', raw='ГОСТ.9999 ВО',
                                           provenance=_ref(page=1, row=3)))
    (f,) = verify_document_refs(model)
    assert f['code'] == 'KD-EM-108-2'
    assert 'ГОСТ.9999 ВО' in f['message']
    assert 'лист 1, строка 3' in f['location']


# --- EM-108-3: ambiguous lookalike identities -------------------------------

def test_em1083_ambiguous_mixed_alphabet(synthetic_classes):
    model = EngineeringModel()
    model.add_device(Designation.classify('MQ1'), _ref(doc='a.Э3.pdf', page=1))
    model.add_device(Designation.classify('МQ1'), _ref(doc='b.ПЭ3.pdf', page=4))  # Cyrillic М
    out = verify_ambiguous_identities(model)
    assert len(out) == 1
    assert out[0]['code'] == 'KD-EM-108-3'
    forms = out[0]['message']
    assert 'MQ1' in forms and 'a.Э3.pdf' in forms and 'b.ПЭ3.pdf' in forms


def test_em1083_positive_uniform_alphabet(synthetic_classes):
    model = EngineeringModel()
    model.add_device(Designation.classify('ZZ1'), _ref())
    model.add_device(Designation.classify('ZZ1'), _ref(doc='other.Э3.pdf'))
    assert verify_ambiguous_identities(model) == []


# --- Model completeness pipeline -------------------------------------------

def test_run_verifiers_combines_all(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('PT1', 2, ['PT1']))
    model.document_refs.append(DocumentRef(kind='СП', key='x', raw='X СП', provenance=_ref()))
    model.add_device(Designation.classify('PT1'), _ref())
    model.add_device(Designation.classify('РT1'), _ref(doc='c.ПЭ3.pdf'))  # lookalike
    codes = {f['code'] for f in run_verifiers(model)}
    assert codes == {'KD-EM-108-1', 'KD-EM-108-2', 'KD-EM-108-3'}


# --- Connection bridge ------------------------------------------------------

def _connection():
    return Connection(project='P', revision='R', wire='W1', cable='',
                      source=Endpoint(device='ZZ1', terminal='1'),
                      target=Endpoint(device='MQ2', terminal='2'),
                      document='vo.pdf', location='sheet 1')


def test_attach_connections_bridge(synthetic_classes):
    model = adapters.attach_connections([_connection()], EngineeringModel())
    assert model.connections[0].wire == 'W1'


# --- Adapter: spec rows -> model --------------------------------------------

def test_adapter_spec_rows_provenance(synthetic_classes):
    rows = [{'page': 3, 'source': 'ZZ1;ZZ2', 'quantity': 2,
             'entries': {'ZZ1', 'ZZ2'}, 'unknown': [], 'row_band': (0, 1, 2, 3),
             'table_row': 9, 'duplicate_eligible': False}]
    model = adapters.model_from_spec_rows(rows, 'T.ПЭ3.pdf')
    assert len(model.spec_rows) == 1
    assert model.spec_rows[0].provenance.page == 3
    assert model.spec_rows[0].provenance.row == 9
    assert set(model.devices) == {'ZZ1', 'ZZ2'}
    assert verify_row_quantities(model) == []
