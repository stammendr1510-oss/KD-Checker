import json

import pytest

from engineering_benchmark import GroundTruthManifest, evaluate_run, load_manifest


def sample():
    manifest = GroundTruthManifest(
        expected_devices=["VR1", "BL2", "GX3"],
        expected_pe3_rows=[{"designation": "VR1", "quantity": 1}],
        expected_connections=[{"source": "VR1", "target": "GX3"}],
        expected_defects=[
            {"canonical_type": "missing_connection", "target_entity": "VR1", "min_severity": "ERROR"},
            {"canonical_type": "quantity_mismatch", "target_entity": "BL2", "min_severity": "WARNING"},
        ],
        forbidden_false_positives=[
            {"canonical_type": "missing_device", "target_entity": "GX3"},
        ],
    )
    extracted = {
        "devices": ["VR1", "BL2", "GX3"],
        "pe3_rows": [{"quantity": 1, "designation": "VR1"}],
        "connections": [{"target": "GX3", "source": "VR1"}],
    }
    issues = [
        {"canonical_type": "missing_connection", "target_entity": "VR1", "severity": "ERROR"},
        {"canonical_type": "quantity_mismatch", "target_entity": "BL2", "severity": "WARNING"},
    ]
    return manifest, extracted, issues


def test_exact_match():
    manifest, extracted, issues = sample()
    result = evaluate_run(manifest, extracted, issues)
    assert (result.extraction_precision, result.extraction_recall) == (1.0, 1.0)
    assert (result.diagnostic_precision, result.diagnostic_recall) == (1.0, 1.0)
    assert result.false_positive_count == 0
    assert result.missed_defects == []
    assert result.extraction_true_positives == 5


def test_missed_defect():
    manifest, extracted, issues = sample()
    result = evaluate_run(manifest, extracted, issues[:1])
    assert result.diagnostic_recall == 0.5
    assert result.false_positive_count == 0
    assert result.missed_defects[0]["target_entity"] == "BL2"


def test_forbidden_false_positive():
    manifest, extracted, issues = sample()
    issues.append({"canonical_type": "missing_device", "target_entity": "GX3", "severity": "ERROR"})
    result = evaluate_run(manifest, extracted, issues)
    assert result.false_positive_count == 1
    assert result.diagnostic_precision == pytest.approx(2 / 3)
    assert result.diagnostic_recall == 1.0
    assert result.unexpected_false_positives[0]["target_entity"] == "GX3"


def test_russian_summary_output():
    manifest, extracted, issues = sample()
    summary = evaluate_run(manifest, extracted, issues).render_russian_summary()
    for text in ("ЭТАЛОННАЯ", "точность", "полнота", "Ложных срабатываний", "Пропущенные дефекты"):
        assert text in summary


def test_duplicate_extraction_is_fp():
    manifest, extracted, issues = sample()
    extracted["devices"].append("VR1")
    result = evaluate_run(manifest, extracted, issues)
    assert result.extraction_false_positives == 1
    assert result.extraction_recall == 1.0


def test_insufficient_severity_counts_as_fp_and_miss():
    manifest, extracted, issues = sample()
    issues[0]["severity"] = "WARNING"
    result = evaluate_run(manifest, extracted, issues)
    assert result.diagnostic_recall == 0.5
    assert result.false_positive_count == 1


def test_duplicate_diagnostic_is_fp():
    manifest, extracted, issues = sample()
    issues.append(dict(issues[0]))
    result = evaluate_run(manifest, extracted, issues)
    assert result.false_positive_count == 1


def test_load_manifest(tmp_path):
    manifest, _, _ = sample()
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(manifest.__dict__), encoding="utf-8")
    loaded = load_manifest(path)
    assert loaded.expected_devices == manifest.expected_devices


def test_missing_extracted_item():
    manifest, extracted, issues = sample()
    extracted["devices"].pop()
    result = evaluate_run(manifest, extracted, issues)
    assert result.extraction_recall < 1.0
    assert result.extraction_false_negatives == 1


def test_empty_ground_truth():
    result = evaluate_run(GroundTruthManifest(), {}, [])
    assert (result.extraction_precision, result.extraction_recall) == (1.0, 1.0)
    assert (result.diagnostic_precision, result.diagnostic_recall) == (1.0, 1.0)


def test_reject_invalid_manifest(tmp_path):
    path = tmp_path / "truth.json"
    path.write_text(json.dumps({"expected_defects": [{"canonical_type": "a", "target_entity": "VR1", "min_severity": "OTHER"}]}))
    with pytest.raises(ValueError):
        load_manifest(path)
