import pytest

from connection_checks import Endpoint, check_connections
from connection_import import classify_connection_row
from engineering_graph import build_graph, connections_from_rows
from engineering_model import EngineeringModel, verify_endpoint_conflicts


def row(source, st, target, tt, wire='W1'):
    return dict(source_device=source, source_terminal=st,
                target_device=target, target_terminal=tt, wire=wire)


def model(rows):
    return EngineeringModel(connections=connections_from_rows(rows, document='synthetic.xlsx'))


def test_potential_rows():
    rows = [row('L1', None, 'VR1', '1'), row('BL2', 'A2', 'N', ''),
            row('PE', None, 'PE', None)]
    assert all(classify_connection_row(r)[0] == 'extracted' for r in rows)
    m = model(rows)
    assert len(m.connections) == 3
    assert m.connections[0].source.kind == 'potential'
    assert m.connections[1].target.kind == 'potential'
    assert m.connections[2].source.kind == m.connections[2].target.kind == 'potential'
    assert ('VR1', '1') in build_graph(m.connections)[('L1', '')]
    assert check_connections(m.connections) == []
    assert verify_endpoint_conflicts(m) == []


@pytest.mark.parametrize('label', ['PE', 'N', 'PEN', 'GND', 'L1', 'L2', 'L3', '+24V', '0V', '24VDC'])
def test_bus_names(label):
    assert Endpoint(label, '').kind == 'potential'
    assert Endpoint(label, '1').kind == 'terminal'
    assert classify_connection_row(row(label, None, 'VR1', '1'))[0] == 'extracted'


@pytest.mark.parametrize('source,terminal', [('VR1', None), ('BL2', ''), ('GX1', None), ('', '1'), (None, None)])
def test_incomplete_device_or_endpoint(source, terminal):
    assert classify_connection_row(row(source, terminal, 'VR1', '1'))[0] == 'incomplete'
    assert model([row(source, terminal, 'VR1', '1')]).connections == []


def test_daisy_chain_normalized_wire():
    m = model([row('VR1', '1', 'GX1', '3', 'W 7'),
               row('GX1', '3', 'BL2', 'A2', 'w7')])
    assert verify_endpoint_conflicts(m) == []


def test_distinct_wire_conflict_and_bus_branch():
    m = model([row('L1', '', 'GX1', '4', 'W7'),
               row('GX1', '4', 'BL2', 'A2', 'W8'),
               row('L1', None, 'VR1', '1', 'W9')])
    warnings = verify_endpoint_conflicts(m)
    assert len(warnings) == 1
    assert warnings[0]['code'] == 'KD-EM-109-1'
    assert 'GX1:4' in warnings[0]['message']
