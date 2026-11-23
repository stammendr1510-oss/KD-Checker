"""Designation-agnostic synthetic tests for KD-PE-110 (electrical graph).

All designator classes (ZZ, MQ, PT) are invented for fixtures; no rule
special-cases any real class. Positive, negative, and ambiguous scenarios per
mechanism, including the length-unit skip (EM-110-3), transitive ring
(EM-110-2), and zero-edge devices (EM-110-1).
"""
import pytest

import naming
from connection_checks import Connection, Endpoint
from engineering_graph import (
    CODE_COUNT, CODE_DANGLING, CODE_LOOP, build_graph, run_graph_verifiers,
    verify_dangling_devices, verify_wire_counts, verify_wire_loops,
)
from engineering_model import (
    Designation, EngineeringModel, SchemeMention, SourceRef, SpecRow,
)


@pytest.fixture()
def synthetic_classes():
    naming.configure_reference_classes(r'(?:ZZ|MQ|PT)')
    yield
    naming.reset_reference_classes()


def _ref(doc='ПРИМЕР.ПЭ3.pdf', page=2, row=5, bounds=(1.0, 2.0, 3.0, 4.0)):
    return SourceRef(document=doc, page=page, row=row, bounds=bounds)


def _row(raw, qty, entries, ref=None):
    return SpecRow(raw_text=raw, quantity=qty,
                   designations=[Designation.classify(d) for d in entries],
                   provenance=ref or _ref())


def _connection(wire, device='ZZ1', terminal='1', target='MQ2', target_terminal='2',
                cable='', document='vo.pdf', location='sheet 1'):
    return Connection(project='P', revision='R', wire=wire, cable=cable,
                      source=Endpoint(device=device, terminal=terminal),
                      target=Endpoint(device=target, terminal=target_terminal),
                      document=document, location=location)


def _model_with_devices(names, connections=()):
    model = EngineeringModel()
    for name in names:
        model.add_device(Designation.classify(name), _ref())
    model.connections.extend(connections)
    return model


# --- Graph construction ------------------------------------------------------

def test_build_graph_deterministic_and_normalized(synthetic_classes):
    records = [
        _connection('W1', device='ZZ1', terminal='1', target='МQ2', target_terminal='2'),
        _connection('W2', device='zz1', terminal='1', target='PT3', target_terminal='A'),
    ]
    graph = build_graph(records)
    assert graph == build_graph(list(reversed(records)))
    assert graph[('ZZ1', '1')] == frozenset({('MQ2', '2'), ('PT3', 'A')})
    assert list(graph) == sorted(graph)


# --- KD-EM-110-1: dangling devices -------------------------------------------

def test_em1101_positive_flags_zero_edge_device(synthetic_classes):
    model = _model_with_devices(['ZZ1', 'MQ2', 'PT9'],
                                [_connection('W1', device='ZZ1', terminal='1',
                                             target='MQ2', target_terminal='2')])
    findings = verify_dangling_devices(model)
    assert len(findings) == 1
    assert findings[0]['code'] == CODE_DANGLING
    assert findings[0]['severity'] == 'info'
    assert 'PT9' in findings[0]['message']
    assert 'ZZ1' not in findings[0]['message']
    assert findings[0]['file'] == 'ПРИМЕР.ПЭ3.pdf'


def test_em1101_negative_all_devices_wired(synthetic_classes):
    model = _model_with_devices(['ZZ1', 'MQ2'],
                                [_connection('W1', device='ZZ1', terminal='1',
                                             target='MQ2', target_terminal='2')])
    assert verify_dangling_devices(model) == []


def test_em1101_negative_no_connections_no_check(synthetic_classes):
    model = _model_with_devices(['ZZ1', 'PT9'])
    assert verify_dangling_devices(model) == []


# --- KD-EM-110-2: wire rings -------------------------------------------------

def _triangle(wire='W1'):
    return [
        _connection(wire, device='ZZ1', terminal='1', target='MQ2', target_terminal='2'),
        _connection(wire, device='MQ2', terminal='2', target='PT3', target_terminal='A'),
        _connection(wire, device='PT3', terminal='A', target='ZZ1', target_terminal='1'),
    ]


def test_em1102_positive_transitive_ring(synthetic_classes):
    model = EngineeringModel(connections=_triangle())
    findings = verify_wire_loops(model)
    assert len(findings) == 1
    assert findings[0]['code'] == CODE_LOOP
    assert findings[0]['severity'] == 'warning'
    assert 'W1' in findings[0]['message']
    assert findings[0]['file'] == 'vo.pdf'
    assert findings[0]['location'] == 'sheet 1'


def test_em1102_negative_open_chain(synthetic_classes):
    model = EngineeringModel(connections=_triangle()[:2])
    assert verify_wire_loops(model) == []


def test_em1102_negative_reverse_reexport_is_not_ring(synthetic_classes):
    records = [
        _connection('W1', device='ZZ1', terminal='1', target='MQ2', target_terminal='2'),
        _connection('W1', device='MQ2', terminal='2', target='ZZ1', target_terminal='1'),
    ]
    assert verify_wire_loops(EngineeringModel(connections=records)) == []


def test_em1102_negative_self_endpoint_out_of_scope(synthetic_classes):
    records = [_connection('W1', device='ZZ1', terminal='1',
                           target='ZZ1', target_terminal='1')]
    assert verify_wire_loops(EngineeringModel(connections=records)) == []


def test_em1102_ambiguous_mixed_alphabet_still_ring(synthetic_classes):
    records = [
        _connection('W1', device='ZZ1', terminal='1', target='МQ2', target_terminal='2'),
        _connection('W1', device='MQ2', terminal='2', target='PT3', target_terminal='A'),
        _connection('W1', device='PT3', terminal='A', target='ZZ1', target_terminal='1'),
    ]
    findings = verify_wire_loops(EngineeringModel(connections=records))
    assert len(findings) == 1


# --- KD-EM-110-3: wire count vs PE3 quantity ---------------------------------

def _cable_model(qty, wires=('W1', 'W2'), raw='MQ1; 2 шт', cable='MQ1'):
    model = EngineeringModel()
    model.spec_rows.append(_row(raw, qty, [cable]))
    model.connections.extend(
        _connection(w, device='ZZ1', terminal=str(i), target='MQ2',
                    target_terminal=str(i), cable=cable)
        for i, w in enumerate(wires))
    return model


def test_em1103_positive_count_mismatch(synthetic_classes):
    findings = verify_wire_counts(_cable_model(3))
    assert len(findings) == 1
    assert findings[0]['code'] == CODE_COUNT
    assert findings[0]['severity'] == 'info'
    assert 'MQ1' in findings[0]['message']
    assert '3' in findings[0]['message']
    assert findings[0]['file'] == 'ПРИМЕР.ПЭ3.pdf'


def test_em1103_negative_counts_match(synthetic_classes):
    assert verify_wire_counts(_cable_model(2)) == []


def test_em1103_negative_length_unit_row_skipped(synthetic_classes):
    for raw in ('Кабель MQ1, 5 м', 'Кабель MQ1, 500 мм', 'Кабель MQ1, 2 км', 'Cable MQ1, 5 m'):
        model = _cable_model(5, raw=raw)
        assert verify_wire_counts(model) == [], raw


def test_em1103_negative_no_cable_records(synthetic_classes):
    model = EngineeringModel()
    model.spec_rows.append(_row('MQ1; 2 шт', 2, ['MQ1']))
    model.connections.append(_connection('W1'))
    assert verify_wire_counts(model) == []


def test_em1103_ambiguous_cyrillic_cable_matches_latin(synthetic_classes):
    findings = verify_wire_counts(_cable_model(3, cable='МQ1'))  # Cyrillic М
    assert len(findings) == 1
    assert findings[0]['code'] == CODE_COUNT


# --- Aggregate seam -----------------------------------------------------------

def test_run_graph_verifiers_empty_model_never_raises(synthetic_classes):
    assert run_graph_verifiers(EngineeringModel()) == []
