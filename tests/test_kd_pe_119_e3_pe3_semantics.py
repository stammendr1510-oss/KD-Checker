from adapters import model_from_spec_rows
from e3_pe3_inventory import Mention, compare_inventories
from engineering_model import (Designation, SchemeMention, SourceRef,
                               verify_coverage, verify_endpoint_conflicts)
from engineering_graph import connections_from_rows
from naming import normalize_pe3_row, expand_notation


def test_compound_and_ascii_dotted_range():
    assert expand_notation('R1...R3;S') == ({'R1', 'R2', 'R3', 'S'}, [])
    assert expand_notation('B0.1-B5.1') == ({f'B{i}.1' for i in range(6)}, [])


def test_grouped_multiline_and_ranges():
    raw = dict(source='VR1...VR3;MK1\nMK2', entries={'VR1', 'MK1'}, quantity=5)
    expected = {'VR1', 'VR2', 'VR3', 'MK1', 'MK2'}
    assert normalize_pe3_row(raw)['entries'] == expected
    model = model_from_spec_rows([raw], 'fixture')
    assert {d.normalized for d in model.spec_rows[0].designations} == expected
    model.scheme_mentions = [SchemeMention(Designation.classify(v), SourceRef('scheme'))
                             for v in sorted(expected)]
    assert verify_coverage(model) == []
    mentions = [Mention(v, v, 1, 'label', v, 'high') for v in sorted(expected)]
    assert compare_inventories('scheme', 'fixture', mentions, [raw], e3_word_count=5,
                               e3_pages_processed=1, e3_pages_with_text=1) == []


def test_terminal_range_parent_coverage():
    raw = dict(source='BL1.1...BL1.3', entries=set(), quantity=3, name='terminal block')
    model = model_from_spec_rows([raw], 'fixture')
    model.scheme_mentions = [SchemeMention(Designation.classify('BL1'), SourceRef('scheme'))]
    assert model.object_kinds()['BL1'] == 'terminal_assembly'
    assert verify_coverage(model) == []


def test_bridging_vs_apparatus_conflict():
    model = model_from_spec_rows([
        dict(source='BL1.1...BL1.3', entries=set(), name='distribution block'),
        dict(source='GX1', entries={'GX1'}, name='potential bus'),
        dict(source='VR1', entries={'VR1'}, name='relay')], 'fixture')
    rows = [dict(source_device=device, source_terminal='1', target_device=f'MK{i}',
                 target_terminal='2', wire=wire)
            for device in ('BL1', 'GX1', 'VR1')
            for i, wire in enumerate(('10', '11'), 1)]
    model.connections.extend(connections_from_rows(rows, document='fixture'))
    findings = verify_endpoint_conflicts(model)
    assert len(findings) == 1
    assert 'VR1:1' in str(findings[0])
