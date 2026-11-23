"""Designation-agnostic synthetic tests for KD-PE-108/109 (engineering model).

All designator classes (ZZ, MQ, PT, ...) are invented for fixtures; no rule
special-cases any real class. Positive, negative, and ambiguous scenarios per
mechanism, plus SourceRef provenance checks.
"""
import pytest

import naming
from connection_checks import Connection, Endpoint
from engineering_model import (
    Designation, DocumentRef, EngineeringModel, SchemeMention, SourceRef,
    SpecRow, run_universal_verifiers, run_verifiers, verify_ambiguous_identities,
    verify_document_refs, verify_row_quantities,
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


def _connection(wire, device='ZZ1', terminal='1', target='MQ2', target_terminal='2',
                document='vo.pdf', location='sheet 1'):
    return Connection(project='P', revision='R', wire=wire, cable='',
                      source=Endpoint(device=device, terminal=terminal),
                      target=Endpoint(device=target, terminal=target_terminal),
                      document=document, location=location)


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


# --- EM-109-1: endpoint conflicts -------------------------------------------

def test_em1091_positive_distinct_wires(synthetic_classes):
    model = adapters.attach_connections(
        [_connection('W1', terminal='1'), _connection('W2', terminal='9', target='PT9')],
        EngineeringModel())
    assert [x for x in run_universal_verifiers(model) if x['code'] == 'KD-EM-109-1'] == []


def test_em1091_negative_same_terminal_two_wires(synthetic_classes):
    model = adapters.attach_connections(
        [_connection('W1', terminal='3', document='a.pdf', location='лист 1'),
         _connection('W2', terminal='3', document='b.pdf', location='лист 2')],
        EngineeringModel())
    out = [x for x in run_universal_verifiers(model) if x['code'] == 'KD-EM-109-1']
    assert len(out) == 2  # both endpoints are shared across the two records
    f = next(x for x in out if 'ZZ1:3' in x['message'])
    assert f['severity'] == 'warning'
    assert 'W1' in f['message'] and 'W2' in f['message']
    assert 'a.pdf' in f['message'] and 'b.pdf' in f['message']


def test_em1091_ambiguous_same_wire_repeated(synthetic_classes):
    # Same wire re-declared: not a conflict (deduped by key in connection_checks).
    model = adapters.attach_connections(
        [_connection('W1', terminal='3'), _connection('W1', terminal='3')], EngineeringModel())
    assert [x for x in run_universal_verifiers(model) if x['code'] == 'KD-EM-109-1'] == []


# --- EM-109-2: coverage ------------------------------------------------------

def test_em1092_positive_full_overlap(synthetic_classes):
    model = EngineeringModel()
    model.scheme_mentions.append(SchemeMention(Designation.classify('ZZ1'), _ref()))
    model.spec_rows.append(_row('ZZ1', 1, ['ZZ1']))
    assert run_universal_verifiers(model) == []


def test_em1092_negative_scheme_only_and_spec_only(synthetic_classes):
    model = EngineeringModel()
    model.scheme_mentions.append(SchemeMention(Designation.classify('MQ1'), _ref(doc='s.Э3.pdf', page=3)))
    model.scheme_mentions.append(SchemeMention(Designation.classify('MQ2'), _ref(doc='s.Э3.pdf', page=4)))
    model.spec_rows.append(_row('PT1', 1, ['PT1'], ref=_ref(doc='p.ПЭ3.pdf', page=1, row=2)))
    out = run_universal_verifiers(model)
    assert len(out) == 2
    assert all(f['code'] == 'KD-EM-109-2' for f in out)
    assert 'MQ1' in out[0]['message'] and 's.Э3.pdf, лист 3' in out[0]['message']
    assert 'PT1' in out[1]['message'] and 'p.ПЭ3.pdf, лист 1, строка 2' in out[1]['message']


def test_em1092_ambiguous_single_document_only(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('ZZ1', 1, ['ZZ1']))
    assert run_universal_verifiers(model) == []  # no Э3 loaded: no coverage claim


# --- EM-109-3: contact consistency -------------------------------------------

def test_em1093_positive_resolved_row(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('ZZ1...ZZ3', 3, ['ZZ1', 'ZZ2', 'ZZ3']))
    assert run_universal_verifiers(model) == []


def test_em1093_negative_unresolved_with_excess_quantity(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('MQ1; MQ2?; MQ3?', 3, ['MQ1'], unknown=['MQ2?', 'MQ3?'],
                                ref=_ref(doc='q.ПЭ3.pdf', page=6, row=8)))
    (f,) = [x for x in run_universal_verifiers(model) if x['code'] == 'KD-EM-109-3']
    assert f['severity'] == 'info'
    assert 'MQ2?' in f['message'] and 'MQ3?' in f['message']
    assert 'лист 6, строка 8' in f['location']


# --- Seam: additive by default, opt-in legacy suppression -------------------

def test_seam_disabled_by_flag():
    cfg = {'engineering_model_checks': False}
    assert adapters.engineering_findings([], cfg) == []


def test_seam_never_raises_on_garbage():
    cfg = {'engineering_model_checks': True}
    assert adapters.engineering_findings([('x.ПЭ3.pdf', 'ПЭ3', 'x', b'not a pdf')], cfg) == []


def test_seam_additive_keeps_legacy_codes():
    existing = [{'code': 'KD-PE-102'}, {'code': 'KD-SET-203'}]
    out = adapters.apply_engineering_findings(existing, [], {'engineering_model_checks': True})
    assert {'KD-PE-102', 'KD-SET-203'} <= {f['code'] for f in out}


def test_seam_opt_in_suppresses_legacy_only():
    existing = [{'code': 'KD-PE-102'}, {'code': 'KD-SET-203'}, {'code': 'KD-PE-100'}]
    cfg = {'engineering_model_checks': True, 'engineering_model_replace_legacy': True}
    out = adapters.apply_engineering_findings(existing, [], cfg)
    codes = [f['code'] for f in out]
    assert 'KD-PE-102' not in codes and 'KD-SET-203' not in codes and 'KD-PE-100' in codes


# --- Adapter units (from KD-PE-108) ------------------------------------------

def test_attach_connections_bridge(synthetic_classes):
    model = adapters.attach_connections([_connection('W1')], EngineeringModel())
    assert model.connections[0].wire == 'W1'


def test_adapter_spec_rows_provenance(synthetic_classes):
    rows = [{'page': 3, 'source': 'ZZ1;ZZ2', 'quantity': 2,
             'entries': {'ZZ1', 'ZZ2'}, 'unknown': [], 'row_band': (0, 1, 2, 3),
             'table_row': 9, 'duplicate_eligible': False}]
    model = adapters.model_from_spec_rows(rows, 'T.ПЭ3.pdf')
    assert model.spec_rows[0].provenance.page == 3
    assert model.spec_rows[0].provenance.row == 9
    assert set(model.devices) == {'ZZ1', 'ZZ2'}
    assert verify_row_quantities(model) == []
