"""Independent engineering acceptance (KD-PE-111).

Strict holdout / blind principle: all fixtures use unseen synthetic reference
classes (VR/BL/GX/NK via configure_reference_classes), unseen document names
(Э3-ГХ-7701 / ПЭ3-ГХ-7701 / СП-ГХ-7701 / ВО-ГХ-7701) and cabinets ШЗА-77.
Never TA/QF/KM/KLD/SO/ZZ/MQ/PT, never seen filenames. No external APIs, no
real CAD files; multi-document scenarios are built with in-memory PDFs only.
"""
import fitz
import pytest

import naming
from adapters import engineering_findings
from connection_checks import Connection, Endpoint
from engineering_graph import build_graph, run_graph_verifiers
from engineering_model import (
    Designation, DocumentRef, EngineeringModel, SchemeMention, SourceRef,
    SpecRow, run_universal_verifiers, run_verifiers,
)

HOLDOUT_CLASSES = r'(?:VR|BL|GX|NK)'
E3_DOC = 'Э3-ГХ-7701.pdf'
PE3_DOC = 'ПЭ3-ГХ-7701.pdf'
SP_DOC = 'СП-ГХ-7701.pdf'
VO_DOC = 'ВО-ГХ-7701.pdf'


@pytest.fixture()
def holdout_classes():
    naming.configure_reference_classes(HOLDOUT_CLASSES)
    yield
    naming.reset_reference_classes()


def des(raw):
    return Designation.classify(raw)


def sref(document=PE3_DOC, page=1, row=None):
    return SourceRef(document=document, page=page, row=row)


def spec_row(text, qty, entries, unknown=(), document=PE3_DOC, page=1, rownum=1):
    return SpecRow(raw_text=text, quantity=qty,
                   designations=[des(d) for d in entries], unknown=list(unknown),
                   provenance=sref(document, page, rownum))


def mention(raw, document=E3_DOC, page=1):
    return SchemeMention(designation=des(raw), provenance=sref(document, page))


def conn(wire, sdev, sterm, tdev, tterm, cable='КВ-77', document=VO_DOC, location='ШЗА-77.1'):
    return Connection(project='ГХ.7710.7701', revision='А', wire=wire, cable=cable,
                      source=Endpoint(sdev, sterm), target=Endpoint(tdev, tterm),
                      document=document, location=location)


def make_model(devices=(), rows=(), mentions=(), conns=(), refs=(), uploaded=()):
    model = EngineeringModel()
    for d in devices:
        model.add_device(des(d), sref(E3_DOC, page=1))
    model.spec_rows.extend(rows)
    model.scheme_mentions.extend(mentions)
    model.connections.extend(conns)
    model.document_refs.extend(refs)
    model.uploaded_identities.update(uploaded)
    return model


def codes(findings):
    return [f['code'] for f in findings]


# --- Clean holdout package ----------------------------------------------------

CLEAN_DEVICES = ['VR1', 'VR2', 'BL1', 'GX1', 'NK1']
CLEAN_ROWS = [
    spec_row('VR1', 1, ['VR1'], rownum=1),
    spec_row('VR2', 1, ['VR2'], rownum=2),
    spec_row('BL1', 1, ['BL1'], rownum=3),
    spec_row('GX1', 1, ['GX1'], rownum=4),
    spec_row('NK1', 1, ['NK1'], rownum=5),
]
CLEAN_MENTIONS = [mention(d) for d in CLEAN_DEVICES]
CLEAN_CONNS = [
    conn('1', 'VR1', '1', 'BL1', '1'),
    conn('2', 'BL1', '2', 'GX1', '1'),
    conn('3', 'GX1', '2', 'NK1', '1'),
    conn('4', 'GX1', '3', 'NK1', '2'),
    conn('5', 'NK1', '3', 'VR2', '1'),
]


def clean_model():
    return make_model(devices=CLEAN_DEVICES, rows=list(CLEAN_ROWS),
                      mentions=CLEAN_MENTIONS, conns=CLEAN_CONNS,
                      uploaded={('Э3', 'гх.7710.7701'), ('ПЭ3', 'гх.7710.7701')})


def test_clean_holdout_package_zero_false_positives(holdout_classes):
    model = clean_model()
    assert run_verifiers(model) == []
    assert run_universal_verifiers(model) == []
    assert run_graph_verifiers(model) == []


def test_clean_holdout_graph_is_deterministic(holdout_classes):
    adjacency = build_graph(clean_model().connections)
    assert list(adjacency) == sorted(adjacency)
    assert adjacency[('VR1', '1')] == frozenset({('BL1', '1')})
    assert adjacency[('GX1', '2')] == frozenset({('NK1', '1')})
    assert adjacency[('GX1', '3')] == frozenset({('NK1', '2')})
    assert build_graph(clean_model().connections) == adjacency


# --- Defective holdout packages: exact codes + SourceRef provenance -------------

def test_quantity_mismatch_emits_exact_code_and_provenance(holdout_classes):
    model = make_model(rows=[spec_row('BL1', 3, ['BL1'], rownum=7)])
    findings = run_verifiers(model)
    assert codes(findings) == ['KD-EM-108-1']
    f = findings[0]
    assert f['severity'] == 'warning' and f['file'] == PE3_DOC
    assert 'лист 1, строка 7' in f['location']


def test_missing_sp_reference_emits_exact_code_and_provenance(holdout_classes):
    ref = DocumentRef(kind='ВО', key='гх.7710.9999', raw='ГХ.7710.9999.ВО',
                      provenance=sref(SP_DOC, page=2, row=3))
    model = make_model(refs=[ref], uploaded={('Э3', 'гх.7710.7701')})
    findings = run_verifiers(model)
    assert codes(findings) == ['KD-EM-108-2']
    f = findings[0]
    assert f['severity'] == 'warning' and f['file'] == SP_DOC
    assert 'лист 2, строка 3' in f['location'] and 'ГХ.7710.9999.ВО' in f['message']


def test_lookalike_cyrillic_latin_emits_exact_code_with_both_forms(holdout_classes):
    model = EngineeringModel()
    latin_ref = sref(E3_DOC, page=1)
    cyr_ref = sref(PE3_DOC, page=4, row=2)
    model.add_device(des('BR1'), latin_ref)
    model.add_device(des('ВR1'), cyr_ref)  # Cyrillic В vs Latin B
    findings = run_verifiers(model)
    assert codes(findings) == ['KD-EM-108-3']
    f = findings[0]
    assert f['severity'] == 'info' and f['file'] == E3_DOC
    assert 'BR1' in f['message'] and 'ВR1' in f['message']
    assert 'лист 1' in f['message'] and 'лист 4, строка 2' in f['message']


def test_terminal_wire_conflict_emits_exact_code(holdout_classes):
    conns = [conn('1', 'VR1', '1', 'BL1', '1'), conn('2', 'VR1', '1', 'GX1', '1')]
    model = make_model(devices=['VR1', 'BL1', 'GX1'], conns=conns)
    findings = run_universal_verifiers(model)
    assert codes(findings) == ['KD-EM-109-1']
    f = findings[0]
    assert f['severity'] == 'warning' and f['file'] == VO_DOC
    assert 'VR1:1' in f['message'] and 'ШЗА-77.1' in f['message']


def test_terminal_reuse_within_one_wire_is_not_a_conflict(holdout_classes):
    conns = [conn('1', 'VR1', '1', 'BL1', '1'), conn('1', 'VR1', '1', 'GX1', '1')]
    model = make_model(devices=['VR1', 'BL1', 'GX1'], conns=conns)
    assert run_universal_verifiers(model) == []


def test_coverage_both_directions_emits_info_findings(holdout_classes):
    rows = [spec_row('NK2', 1, ['NK2'], rownum=1)]
    model = make_model(rows=rows, mentions=[mention('GX1', page=3)])
    findings = run_universal_verifiers(model)
    assert codes(findings) == ['KD-EM-109-2', 'KD-EM-109-2']
    joined = ' '.join(f['message'] for f in findings)
    assert 'GX1' in joined and 'NK2' in joined
    assert all(f['severity'] == 'info' for f in findings)


def test_unresolved_fragments_surface_via_109_3(holdout_classes):
    rows = [spec_row('GX1;GX900..GX999', 5, ['GX1'], unknown=['GX900..GX999'], rownum=9)]
    model = make_model(rows=rows)
    findings = run_universal_verifiers(model)
    assert codes(findings) == ['KD-EM-109-3']
    f = findings[0]
    assert f['severity'] == 'info' and f['file'] == PE3_DOC
    assert 'GX900..GX999' in f['message'] and 'строка 9' in f['location']


def test_unknown_fragments_below_quantity_stay_silent(holdout_classes):
    rows = [spec_row('GX1;мусор', 1, ['GX1'], unknown=['мусор'], rownum=1)]
    assert run_universal_verifiers(make_model(rows=rows)) == []


def test_dangling_device_emits_exact_code_with_provenance(holdout_classes):
    conns = [conn('1', 'VR1', '1', 'BL1', '1')]
    model = make_model(devices=['VR1', 'BL1', 'GX1'], conns=conns)
    findings = run_graph_verifiers(model)
    assert codes(findings) == ['KD-EM-110-1']
    f = findings[0]
    assert f['severity'] == 'info'
    assert 'GX1' in f['message'] and 'Э3-ГХ-7701.pdf, лист 1' in f['message']


def test_no_connections_means_no_dangling_verdict(holdout_classes):
    model = make_model(devices=['GX1'])
    assert run_graph_verifiers(model) == []


def test_three_node_wire_ring_emits_exact_code(holdout_classes):
    conns = [conn('W1', 'VR1', '1', 'BL1', '1'), conn('W1', 'BL1', '1', 'GX1', '1'),
             conn('W1', 'GX1', '1', 'VR1', '1')]
    model = make_model(devices=['VR1', 'BL1', 'GX1'], conns=conns)
    findings = run_graph_verifiers(model)
    assert codes(findings) == ['KD-EM-110-2']
    f = findings[0]
    assert f['severity'] == 'warning' and f['file'] == VO_DOC
    assert 'W1' in f['message'] and '→' in f['message']


def test_two_node_reexport_is_not_a_ring(holdout_classes):
    conns = [conn('W1', 'VR1', '1', 'BL1', '1'),
             conn('W1', 'BL1', '1', 'VR1', '1', document='ВО-ГХ-7702.pdf')]
    model = make_model(devices=['VR1', 'BL1'], conns=conns)
    assert run_graph_verifiers(model) == []


def test_cable_wire_count_mismatch_emits_exact_code(holdout_classes):
    conns = [conn('1', 'VR1', '1', 'BL1', '1'), conn('2', 'VR1', '2', 'BL1', '2'),
             conn('3', 'VR1', '3', 'BL1', '3')]
    rows = [spec_row('КВ-77', 2, ['КВ-77'], rownum=4)]
    model = make_model(conns=conns, rows=rows)
    findings = run_graph_verifiers(model)
    assert codes(findings) == ['KD-EM-110-3']
    f = findings[0]
    assert f['severity'] == 'info' and f['file'] == PE3_DOC
    assert 'КВ-77' in f['message'] and 'строка 4' in f['location']


def test_cable_count_match_and_length_rows_stay_silent(holdout_classes):
    conns = [conn('1', 'VR1', '1', 'BL1', '1'), conn('2', 'VR1', '2', 'BL1', '2')]
    rows = [spec_row('КВ-77', 2, ['КВ-77'], rownum=1),
            spec_row('КВ-78 100 м', 100, ['КВ-78'], rownum=2)]
    model = make_model(devices=['VR1', 'BL1'], conns=conns, rows=rows)
    assert run_graph_verifiers(model) == []


# --- Extraction vs. engineering boundary --------------------------------------

def test_extraction_noise_surfaces_cleanly_without_corrupting_graph(holdout_classes):
    rows = [
        spec_row('GX1;фрагмент??', 4, ['GX1'], unknown=['фрагмент??'], rownum=1),
        spec_row('???', None, [], unknown=['???'], rownum=2),
        spec_row('', 0, [], rownum=3),
        spec_row('VR1', 1, ['VR1'], rownum=4),
    ]
    model = make_model(devices=['VR1', 'GX1'], rows=rows,
                       mentions=[mention('VR1'), mention('GX1')])
    findings = (run_verifiers(model) + run_universal_verifiers(model)
                + run_graph_verifiers(model))
    assert sorted(codes(findings)) == ['KD-EM-108-1', 'KD-EM-109-3']
    assert build_graph(model.connections) == {}
    assert model.devices['VR1'].designation.normalized == 'VR1'


def test_fully_empty_model_is_silent(holdout_classes):
    model = make_model()
    assert run_verifiers(model) == []
    assert run_universal_verifiers(model) == []
    assert run_graph_verifiers(model) == []


# --- End-to-end seam: adapters.engineering_findings on real in-memory PDFs ------

PROJECT = 'ГХ.7710.7701'
SPACES = ' ' * 62  # mono 10pt: digit lands at x≈433, inside the qty column
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'  # Cyrillic-capable


def _pdf(build):
    doc = fitz.open()
    page = doc.new_page()
    build(page)
    return doc.tobytes()


def _e3_pdf(text):
    return _pdf(lambda page: page.insert_text((72, 100), text))


def _pe3_pdf(row_specs):
    """Minimal grid-confirmed ПЭ3: designator column [0.09W,0.188W), 'кол'
    header near 0.75W, horizontal rules crossing the designator column."""
    def build(page):
        page.insert_text((435, 70), 'кол', fontname='DVSM', fontfile=FONT, fontsize=10)
        y = 200
        for designation, qty in row_specs:
            page.draw_line((50, y), (120, y))
            page.draw_line((50, y + 30), (120, y + 30))
            page.insert_text((60, y + 13), f'{designation}\n{SPACES}{qty}',
                             fontname='DVSM', fontfile=FONT, fontsize=10)
            y += 60
    return _pdf(build)


def _sp_pdf(reference_text):
    def build(page):
        page.insert_text((200, 100), 'обозначение', fontname='DVSM', fontfile=FONT, fontsize=10)
        page.draw_line((150, 80), (150, 200))
        page.draw_line((450, 80), (450, 200))
        for y in (110, 150, 190):
            page.draw_line((100, y), (500, y))
        page.insert_text((200, 135), reference_text, fontname='DVSM', fontfile=FONT, fontsize=10)
    return _pdf(build)


def _sources(*named_pdfs):
    return [(name, 'ignored-kind', 'ignored-key', content) for name, content in named_pdfs]


def test_seam_clean_holdout_package_returns_no_findings(holdout_classes):
    sources = _sources(
        (f'{PROJECT}.Э3.pdf', _e3_pdf('VR1 VR2 BL1 GX1 NK1')),
        (f'{PROJECT}.ПЭ3.pdf', _pe3_pdf([('VR1', 1), ('VR2', 1), ('BL1', 1),
                                         ('GX1', 1), ('NK1', 1)])),
        (f'{PROJECT}.СП.pdf', _sp_pdf(f'{PROJECT}.Э3')),
    )
    assert engineering_findings(sources) == []


def test_seam_multi_defect_holdout_aggregates_em_codes(holdout_classes):
    sources = _sources(
        (f'{PROJECT}.Э3.pdf', _e3_pdf('VR1 BL1 GX1 NK1')),
        (f'{PROJECT}.ПЭ3.pdf', _pe3_pdf([('VR1', 1), ('BL1', 2), ('NK1', 1),
                                         ('NK2', 1), ('NK9;XX', 5)])),
        (f'{PROJECT}.СП.pdf', _sp_pdf('ГХ.7710.9999.ВО')),
    )
    findings = engineering_findings(sources)
    found = codes(findings)
    assert sorted(found) == ['KD-EM-108-1', 'KD-EM-108-1', 'KD-EM-108-2',
                             'KD-EM-109-2', 'KD-EM-109-2', 'KD-EM-109-3']
    by_code = {}
    for f in findings:
        by_code.setdefault(f['code'], []).append(f)
    assert by_code['KD-EM-108-1'][0]['file'] == f'{PROJECT}.ПЭ3.pdf'
    assert by_code['KD-EM-108-2'][0]['file'] == f'{PROJECT}.СП.pdf'
    assert {f['severity'] for f in findings} == {'info', 'warning'}
    # No structured connections were uploaded: graph rules stay silent.
    assert not any(c.startswith('KD-EM-110') for c in found)
