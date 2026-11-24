"""Synthetic PDF scheme and XLSX PE3/wiring integration benchmark."""
import json
from io import BytesIO
from pathlib import Path

import fitz
from openpyxl import Workbook

import naming
from adapters import model_from_scheme, model_from_xlsx, deduplicate_findings
from engineering_model import verify_coverage, verify_endpoint_conflicts
from engineering_benchmark import load_manifest, evaluate_run

FIXTURE = Path(__file__).parent / 'fixtures' / 'benchmark_e3_pe3_xlsx.json'


def test_benchmark():
    data = json.loads(FIXTURE.read_text(encoding='utf-8'))['input']
    wb = Workbook()
    pe = wb.active
    pe.append(['Designation', 'Name', 'Quantity'])
    for row in data['spec']:
        pe.append(row)
    ws = wb.create_sheet()
    ws.append(['Source device', 'Source terminal', 'Target device', 'Target terminal',
               'Wire', 'Cable', 'Color', 'Cross-section', 'Wire type'])
    for row in data['wires']:
        ws.append(row)
    stream = BytesIO()
    wb.save(stream)
    wb.close()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 80), data['scheme'])
        scheme = pdf.tobytes()
    naming.configure_reference_classes(r'(?:VR|BL|GX|MK|P)')
    try:
        model = model_from_scheme(scheme, 'synthetic-scheme.pdf')
        model = model_from_xlsx(stream.getvalue(), 'synthetic-tables.xlsx', model)
    finally:
        naming.reset_reference_classes()
    extracted = dict(devices=sorted(model.devices),
                     pe3_rows=[dict(designation=r.raw_text, name=r.name, quantity=r.quantity)
                               for r in model.spec_rows],
                     connections=[dict(source=f'{r.source.device}:{r.source.terminal}',
                                       target=f'{r.target.device}:{r.target.terminal}',
                                       wire=r.wire, cable=r.cable, bus_or_block_ref=r.bus_or_block_ref,
                                       color=r.color, cross_section=r.cross_section, wire_type=r.wire_type)
                                  for r in model.connections])
    findings = verify_coverage(model) + verify_endpoint_conflicts(model)
    issues = [dict(f, severity=f['severity'].upper()) for f in deduplicate_findings(findings)]
    report = evaluate_run(load_manifest(FIXTURE), extracted, issues)
    assert report.extraction_precision == report.extraction_recall == 1.0
    assert report.diagnostic_recall == 1.0
    assert report.false_positive_count == 0


def test_repeated_document_references_across_sources():
    cards = [dict(code='KD-SET-203', file=f'synthetic-{i}.pdf',
                  location=f'строка {i}', message='«SYNTH-77Э3» явно перечислен',
                  severity=severity)
             for i, severity in enumerate(('info', 'warning'), 1)]
    merged = deduplicate_findings(cards)
    assert len(merged) == 1
    assert merged[0]['severity'] == 'warning'
    assert len(merged[0]['source_refs']) == 2
    assert deduplicate_findings(merged) == merged
