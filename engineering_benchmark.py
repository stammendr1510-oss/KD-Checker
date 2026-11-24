from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


SEVERITIES = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}


@dataclass
class GroundTruthManifest:
    expected_devices: list[str] = field(default_factory=list)
    expected_pe3_rows: list[dict] = field(default_factory=list)
    expected_connections: list[dict] = field(default_factory=list)
    expected_defects: list[dict] = field(default_factory=list)
    forbidden_false_positives: list[dict] = field(default_factory=list)


@dataclass
class BenchmarkReport:
    extraction_precision: float
    extraction_recall: float
    diagnostic_precision: float
    diagnostic_recall: float
    false_positive_count: int
    missed_defects: list[dict] = field(default_factory=list)
    unexpected_false_positives: list[dict] = field(default_factory=list)
    extraction_details: dict = field(default_factory=dict)
    extraction_true_positives: int = 0
    extraction_false_positives: int = 0
    extraction_false_negatives: int = 0
    diagnostic_true_positives: int = 0
    diagnostic_false_negatives: int = 0

    def render_russian_summary(self) -> str:
        lines = [
            "ЭТАЛОННАЯ ПРОВЕРКА КД",
            f"Извлечение — точность: {self.extraction_precision:.4f}; полнота: {self.extraction_recall:.4f}",
            f"Диагностика — точность: {self.diagnostic_precision:.4f}; полнота: {self.diagnostic_recall:.4f}",
            f"Ложных срабатываний: {self.false_positive_count}",
            f"Пропущено дефектов: {len(self.missed_defects)}",
            f"Извлечение — верно: {self.extraction_true_positives}; лишних: {self.extraction_false_positives}; пропущено: {self.extraction_false_negatives}",
        ]
        for category, metrics in self.extraction_details.items():
            labels = {"devices": "Устройства", "pe3_rows": "Строки ПЭ3", "connections": "Соединения"}
            lines.append(
                f"{labels.get(category, category)} — точность: {metrics['precision']:.4f}; "
                f"полнота: {metrics['recall']:.4f}; TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}"
            )
        for heading, items in (
            ("Пропущенные дефекты", self.missed_defects),
            ("Неожиданные ложные срабатывания", self.unexpected_false_positives),
        ):
            lines.append(heading + ":")
            lines.extend("  " + json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items)
            if not items:
                lines.append("  Нет")
        return "\n".join(lines)


def _identity(item: dict) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise ValueError("Диагностическая запись должна быть объектом.")
    values = (item.get("canonical_type"), item.get("target_entity"))
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Требуются непустые canonical_type и target_entity.")
    return values


def _validate_manifest(manifest: GroundTruthManifest) -> None:
    for name in ("expected_devices", "expected_pe3_rows", "expected_connections",
                 "expected_defects", "forbidden_false_positives"):
        values = getattr(manifest, name)
        if not isinstance(values, list):
            raise ValueError(f"Поле {name} должно быть списком.")
        value_type = str if name == "expected_devices" else dict
        if any(not isinstance(value, value_type) for value in values):
            raise ValueError(f"Некорректный тип элемента в {name}.")
    for defect in manifest.expected_defects:
        _identity(defect)
        if defect.get("min_severity") not in SEVERITIES:
            raise ValueError("Некорректная минимальная серьёзность дефекта.")
    for item in manifest.forbidden_false_positives:
        _identity(item)


def load_manifest(path: str | Path) -> GroundTruthManifest:
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError("Манифест должен быть объектом JSON.")
    names = ("expected_devices", "expected_pe3_rows", "expected_connections",
             "expected_defects", "forbidden_false_positives")
    manifest = GroundTruthManifest(**{name: payload.get(name, []) for name in names})
    _validate_manifest(manifest)
    return manifest


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _counts(expected: list, actual: list) -> tuple[int, int, int]:
    counts = {}
    for item in expected:
        key = _canonical(item)
        counts[key] = counts.get(key, 0) + 1
    tp = 0
    for item in actual:
        key = _canonical(item)
        if counts.get(key, 0):
            counts[key] -= 1
            tp += 1
    return tp, len(actual) - tp, len(expected) - tp


def _precision(tp: int, fp: int) -> float:
    return tp / (tp + fp) if tp + fp else 1.0


def _recall(tp: int, fn: int) -> float:
    return tp / (tp + fn) if tp + fn else 1.0


def evaluate_run(
    manifest: GroundTruthManifest,
    extracted_summary: dict,
    issues: list[dict],
) -> BenchmarkReport:
    if not isinstance(manifest, GroundTruthManifest):
        raise TypeError("Требуется GroundTruthManifest.")
    _validate_manifest(manifest)
    if not isinstance(extracted_summary, dict) or not isinstance(issues, list):
        raise TypeError("Некорректная структура результатов проверки.")

    categories = (
        ("devices", manifest.expected_devices, str),
        ("pe3_rows", manifest.expected_pe3_rows, dict),
        ("connections", manifest.expected_connections, dict),
    )
    details = {}
    extraction_tp = extraction_fp = extraction_fn = 0
    for name, expected, value_type in categories:
        actual = extracted_summary.get(name, [])
        if not isinstance(actual, list) or any(not isinstance(item, value_type) for item in actual):
            raise ValueError(f"Некорректная категория извлечения: {name}.")
        tp, fp, fn = _counts(expected, actual)
        extraction_tp += tp
        extraction_fp += fp
        extraction_fn += fn
        details[name] = {"tp": tp, "fp": fp, "fn": fn, "precision": _precision(tp, fp), "recall": _recall(tp, fn)}

    forbidden = {_identity(item) for item in manifest.forbidden_false_positives}
    for issue in issues:
        _identity(issue)

    # Сортировка требований по порогу гарантирует, что более слабое
    # совпадение не займёт замечание, нужное более строгому эталону.
    unmatched = set(range(len(issues)))
    matched_defects = set()
    order = sorted(range(len(manifest.expected_defects)),
                   key=lambda i: SEVERITIES[manifest.expected_defects[i]["min_severity"]],
                   reverse=True)
    for index in order:
        defect = manifest.expected_defects[index]
        identity = _identity(defect)
        threshold = SEVERITIES[defect["min_severity"]]
        candidates = [
            i for i in unmatched
            if _identity(issues[i]) == identity
            and identity not in forbidden
            and SEVERITIES.get(issues[i].get("severity"), -1) >= threshold
        ]
        if candidates:
            selected = max(candidates, key=lambda i: SEVERITIES[issues[i]["severity"]])
            unmatched.remove(selected)
            matched_defects.add(index)

    missed = [
        defect for index, defect in enumerate(manifest.expected_defects)
        if index not in matched_defects
    ]
    unexpected = [issue for index, issue in enumerate(issues) if index in unmatched]
    diagnostic_tp = len(matched_defects)
    diagnostic_fp = len(unexpected)
    diagnostic_fn = len(missed)

    return BenchmarkReport(
        extraction_precision=_precision(extraction_tp, extraction_fp),
        extraction_recall=_recall(extraction_tp, extraction_fn),
        diagnostic_precision=_precision(diagnostic_tp, diagnostic_fp),
        diagnostic_recall=_recall(diagnostic_tp, diagnostic_fn),
        false_positive_count=diagnostic_fp,
        missed_defects=missed,
        unexpected_false_positives=unexpected,
        extraction_details=details,
        extraction_true_positives=extraction_tp,
        extraction_false_positives=extraction_fp,
        extraction_false_negatives=extraction_fn,
        diagnostic_true_positives=diagnostic_tp,
        diagnostic_false_negatives=diagnostic_fn,
    )
