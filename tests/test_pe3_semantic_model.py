"""KD-PE-113: universal PE3 row semantic model (blind synthetic holdout).

Unseen reference classes (MQ/RT/ZP via configure_reference_classes), unseen
document names, in-memory models only. No TA/QF/KM/KLD/SO codes, no real CAD
files. Classification is multi-factor: grid geometry, notation structure,
adjacent-row parent context and description/units combined.
"""
import pytest

import naming
from adapters import _classify_spec_row, model_from_spec_rows
from engineering_model import (
    Designation, SpecRow, run_universal_verifiers, run_verifiers,
)

HOLDOUT = r'(?:MQ|RT|ZP)'
PE3 = 'ПЭ3-НЩ-8810.pdf'
E3 = 'Э3-НЩ-8810.pdf'


@pytest.fixture()
def holdout():
    naming.configure_reference_classes(HOLDOUT)
    yield
    naming.reset_reference_classes()


def raw_row(entries=(), unknown=(), qty=1, band=True, name='', indented=False):
    return {'entries': set(entries), 'unknown': list(unknown), 'quantity': qty,
            'row_band': (200.0, 230.0) if band else None,
            'name': name, 'source': ';'.join(entries), 'page': 1,
            'designator_indented': indented}


# --- Classifier unit boundaries ------------------------------------------------

def test_classifier_standalone_device_qty1(holdout):
    s = _classify_spec_row(raw_row(['MQ1'], qty=1), [], False)
    assert (s['row_role'], s['quantity_semantics'], s['classification_confidence']) == \
        ('standalone_device', 'device_count', 'confirmed')
    assert s['parent_ref'] is None


def test_classifier_standalone_device_qty_above_1_stays_device_count(holdout):
    # Genuine quantity mismatch (qty=3 vs 1 designator) must remain a
    # device-count row so KD-EM-108-1 still fires.
    s = _classify_spec_row(raw_row(['MQ1'], qty=3), [], False)
    assert s['row_role'] == 'standalone_device'
    assert s['quantity_semantics'] == 'device_count'
    assert s['classification_confidence'] == 'confirmed'


def test_classifier_grouped_position(holdout):
    s = _classify_spec_row(raw_row(['MQ1', 'MQ2', 'MQ3'], qty=3), [], False)
    assert (s['row_role'], s['quantity_semantics']) == ('grouped_position', 'grouped_instances')
    assert s['classification_confidence'] == 'confirmed'


def test_classifier_child_accessory_empty_designator_with_cues(holdout):
    # Empty designator cell + parent adjacency (confirmed band before) +
    # accessory description cues. Blank cell alone would NOT be enough.
    s = _classify_spec_row(raw_row((), qty=2, name='комплект крепежа'), ['MQ1'], True)
    assert (s['row_role'], s['quantity_semantics'], s['parent_ref']) == \
        ('accessory', 'accessory_qty', 'MQ1')
    assert s['classification_confidence'] == 'confirmed'


def test_classifier_blank_cell_without_cues_is_not_accessory(holdout):
    s = _classify_spec_row(raw_row((), qty=1, name=''), ['MQ1'], True)
    assert s['row_role'] != 'accessory'


def test_classifier_indented_child_row_assembly_element(holdout):
    s = _classify_spec_row(raw_row(['MQ2'], qty=4, name='', indented=True), ['MQ1'], True)
    assert s['row_role'] == 'assembly_element'
    assert s['parent_ref'] == 'MQ1'
    assert s['quantity_semantics'] == 'assembly_units'


def test_classifier_bulk_length_row(holdout):
    s = _classify_spec_row(raw_row(['RT4'], qty=100, band=False, name='Кабель 100 м'), [], False)
    assert (s['row_role'], s['quantity_semantics']) == ('assembly_element', 'length_or_bulk')


def test_classifier_ambiguous_without_grid(holdout):
    s = _classify_spec_row(raw_row(['MQ1'], unknown=['фрагмент??'], qty=1, band=False), [], False)
    assert s['row_role'] == 'ambiguous'
    assert s['classification_confidence'] == 'ambiguous'


# --- Verifier behaviour on classified rows --------------------------------------

def _model(rows):
    m = model_from_spec_rows(rows, PE3)
    return m


def test_em108_1_fires_on_standalone_qty_mismatch(holdout):
    model = _model([raw_row(['MQ1'], qty=3)])
    codes = [f['code'] for f in run_verifiers(model)]
    assert codes == ['KD-EM-108-1']


def test_em108_1_silent_on_accessory_row(holdout):
    rows = [raw_row(['MQ1'], qty=1),
            raw_row((), qty=7, name='комплект крепежа')]
    model = _model(rows)
    assert model.spec_rows[1].row_role == 'accessory'
    assert [f['code'] for f in run_verifiers(model)] == []


def test_em108_1_silent_on_bulk_length_row(holdout):
    model = _model([raw_row(['RT4'], qty=100, band=False, name='провод 100 м')])
    assert model.spec_rows[0].quantity_semantics == 'length_or_bulk'
    assert run_verifiers(model) == []


def test_em109_3_still_reports_unresolved_fragments_above_qty(holdout):
    model = _model([raw_row(['ZP1'], unknown=['ZP900..ZP999'], qty=5, band=False)])
    codes = [f['code'] for f in run_universal_verifiers(model)]
    assert codes == ['KD-EM-109-3']


def test_coverage_ignores_accessory_and_bulk_designations(holdout):
    from engineering_model import EngineeringModel, SchemeMention, SourceRef
    model = EngineeringModel()
    model.scheme_mentions.append(SchemeMention(
        designation=Designation.classify('MQ1'), provenance=SourceRef(document=E3, page=1)))
    rows = [raw_row(['MQ1'], qty=1),
            raw_row((), qty=2, name='комплект крепежа'),
            raw_row(['RT4'], qty=100, band=False, name='провод 100 м')]
    from adapters import model_from_spec_rows as mfs
    mfs(rows, PE3, model)
    findings = run_universal_verifiers(model)
    # Only MQ1 is a scheme device; accessory/bulk rows create no coverage
    # findings in either direction.
    assert [f['code'] for f in findings if f['code'] == 'KD-EM-109-2'] == []


# --- KD-EM-112 regression ------------------------------------------------------

def test_em112_1_voltage_conflict_unaffected_by_roles(holdout):
    from engineering_model import EngineeringModel, SourceRef
    model = EngineeringModel()
    rows = [raw_row(['MQ1'], qty=1, name='выключатель 24VDC'),
            raw_row(['MQ1'], qty=1, name='выключатель 230VAC')]
    from adapters import model_from_spec_rows as mfs
    mfs(rows, PE3, model)
    codes = [f['code'] for f in run_universal_verifiers(model)]
    assert 'KD-EM-112-1' in codes
    assert 'KD-EM-108-1' not in codes


def test_em112_2_confusable_models_unaffected_by_roles(holdout):
    from engineering_model import EngineeringModel, SourceRef
    model = EngineeringModel()
    ref = SourceRef(document=PE3, page=1)
    model.e3_model_tokens.setdefault('RT1.0A', set()).add(SourceRef(document=E3, page=1))
    model.pe3_model_tokens.setdefault('RT1.OA', set()).add(ref)
    from adapters import model_from_spec_rows as mfs
    mfs([raw_row(['RT1'], qty=1, name='изделие RT1.OA')], PE3, model)
    codes = [f['code'] for f in run_universal_verifiers(model)]
    assert 'KD-EM-112-2' in codes


def test_provenance_present_on_spec_rows(holdout):
    model = _model([raw_row(['MQ1'], qty=1)])
    row = model.spec_rows[0]
    assert row.provenance is not None and row.provenance.document == PE3
    assert row.provenance.page == 1
