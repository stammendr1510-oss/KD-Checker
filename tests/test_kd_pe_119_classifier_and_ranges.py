from naming import expand_notation
from engineering_classifier import classify_object
from engineering_model import EngineeringModel, Designation, SpecRow, SourceRef
from engineering_graph import connections_from_rows


def test_compound_and_dotted_ranges():
    assert expand_notation('VR1...VR3;S') == ({'VR1', 'VR2', 'VR3', 'S'}, [])
    assert expand_notation('BL0.1–BL5.1') == ({f'BL{i}.1' for i in range(6)}, [])
    assert expand_notation('GX1.1...GX1.4') == ({f'GX1.{i}' for i in range(1, 5)}, [])
    assert expand_notation('GX1.1...GX2.4')[0] == set()


def test_classification():
    assert classify_object('VR9') == 'device'
    assert classify_object('BL7', description='distribution block') == 'terminal_assembly'
    assert classify_object('GX8', description='protective potential') == 'potential_bus'
    assert classify_object('MK9', description='multi-core cable') == 'cable'
    assert classify_object('MK9.2', parent_ref='MK9', role='accessory') == 'accessory'


def test_connection_group_reconciliation():
    model = EngineeringModel()
    for tag, name in [('BL7', 'distribution block'), ('GX8', 'potential bus'), ('MK9', 'multi-core cable')]:
        des = Designation.classify(tag)
        model.add_device(des, SourceRef('fixture'))
        model.spec_rows.append(SpecRow(tag, 1, [des], name=name))
    row = dict(source_device='VR1', source_terminal='1', target_device='VR2',
               target_terminal='2', wire='10', color='blue', cross_section=2.5, wire_type='H07V')
    model.connections.extend(connections_from_rows(
        [dict(row, cable=tag) for tag in ('BL7', 'GX8', 'MK9')], document='fixture'))
    model.reconcile_connection_groups()
    assert [(c.cable, c.bus_or_block_ref) for c in model.connections] == [
        ('', 'BL7'), ('', 'GX8'), ('MK9', '')]
    assert all((c.color, c.cross_section, c.wire_type) == ('blue', '2.5', 'H07V')
               for c in model.connections)
