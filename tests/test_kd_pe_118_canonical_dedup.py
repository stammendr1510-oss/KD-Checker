"""Public synthetic input only; no customer documents."""
from pathlib import Path

from adapters import deduplicate_findings, model_from_spec_rows
from designation_conflicts import conflict_findings
from engineering_benchmark import evaluate_run, load_manifest
from engineering_graph import connections_from_rows
from engineering_model import verify_designation_attribute_conflicts, verify_endpoint_conflicts


def card(code, message, location, severity='warning', file='synthetic.pdf'):
    return dict(code=code, message=message, location=location, severity=severity,
                file=file, recommendation='Проверить')


def test_overlapping_rules_and_independent_terminal():
    findings = [
        card('KD-PE-106', 'обозначение «vr1» с несовместимыми данными', 'лист 1'),
        card('KD-EM-112-1', 'обозначение «VR1»; напряжение', 'лист 2', 'error'),
        card('KD-PE-106', 'обозначение «VR1»; тип изделия', 'лист 3'),
        card('KD-PE-107', 'в Э3 «BL-01», в ПЭ3 «BL-O1»', 'лист 4', 'info'),
        card('KD-EM-112-2', 'в Э3 «BL-01», в ПЭ3 «BL-O1»', 'лист 5', 'info'),
        card('KD-SET-203', '«SYNTH-77Э3» явно перечислен', 'строка 1'),
        card('KD-EM-108-2', '«SYNTH-77Э3» явно перечислен', 'строка 2'),
        card('KD-SET-203', '«SYNTH-77Э3» явно перечислен', 'строка 3'),
        card('KD-EM-109-1', 'Контакт «VR1:1» использован в разных проводах', 'лист 6'),
    ]
    result = deduplicate_findings(findings)
    assert len(result) == 4
    by_type = {f['canonical_type']: f for f in result}
    assert by_type['parameter_conflict']['severity'] == 'error'
    assert by_type['parameter_conflict']['related_codes'] == ['KD-EM-112-1', 'KD-PE-106']
    assert len(by_type['parameter_conflict']['source_refs']) == 3
    assert len(by_type['confusable_model']['source_refs']) == 2
    assert len(by_type['missing_set_document']['source_refs']) == 3
    assert by_type['terminal_conflict']['target_entity'] == 'VR1:1'
    assert deduplicate_findings(result) == result
    assert 'canonical_type' not in findings[0]


def test_public_benchmark():
    manifest = load_manifest(Path(__file__).parent / 'fixtures' / 'benchmark_synthetic_cabinet.json')
    raw_rows = [
        ('VR1', 'Relay 24VDC'), ('VR1', 'Relay 230VAC'),
        ('BL2', 'Module'), ('GX3', 'Switch'),
    ]
    rows = [dict(source=d, entries={d}, quantity=1, name=n, page=1,
                 table_row=i, row_band=(60+i*20, 80+i*20))
            for i, (d, n) in enumerate(raw_rows, 1)]
    model = model_from_spec_rows(rows, 'synthetic.pdf')
    model.connections = connections_from_rows([
        dict(source_device='VR1', source_terminal='1', target_device='BL2',
             target_terminal='2', wire='W1'),
        dict(source_device='VR1', source_terminal='1', target_device='GX3',
             target_terminal='3', wire='W2'),
    ], document='synthetic.xlsx')
    findings = conflict_findings(rows, 'synthetic.pdf') + verify_designation_attribute_conflicts(model)
    findings += verify_endpoint_conflicts(model)
    findings += [
        card('KD-PE-107', 'в Э3 «BL-01», в ПЭ3 «BL-O1»', 'лист 4', 'info'),
        card('KD-EM-112-2', 'в Э3 «BL-01», в ПЭ3 «BL-O1»', 'лист 5', 'info'),
        card('KD-SET-203', '«SYNTH-77Э3» явно перечислен', 'строка 1'),
        card('KD-EM-108-2', '«SYNTH-77Э3» явно перечислен', 'строка 2'),
    ]
    extracted = dict(
        devices=sorted(model.devices),
        pe3_rows=[dict(designation=r.raw_text, quantity=r.quantity, name=r.name)
                  for r in model.spec_rows],
        connections=[dict(source=f'{r.source.device}:{r.source.terminal}',
                          target=f'{r.target.device}:{r.target.terminal}', wire=r.wire)
                     for r in model.connections],
    )
    issues = [dict(f, severity=f['severity'].upper()) for f in deduplicate_findings(findings)]
    report = evaluate_run(manifest, extracted, issues)
    assert report.extraction_precision == report.extraction_recall == 1.0
    assert report.diagnostic_recall == 1.0
    assert report.false_positive_count == 0
