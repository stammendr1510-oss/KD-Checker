"""Universal conflict detection for repeated PE3 designations (KD-PE-106).

The module is pure: it receives already extracted PE3 rows and never opens a
PDF, never uses the network and never modifies source documents.

Engineering rule
----------------
A repeated designation is *not* an error by itself.  It becomes a conflict only
when the two rows describe **incompatible product attributes** that can be read
from the PE3 name: a different nominal voltage signature (``24VDC`` vs
``230VAC``) or a different product type code.  Repeated rows with the same
product, continuation rows, multi-sheet references and terminal-block assemblies
stay compatible or are reported as an ambiguity for an engineer, never as a
conflict.

No rule is tied to a concrete device code, document name or sheet number.
"""
from __future__ import annotations

import re
from collections import defaultdict


CODE = "KD-PE-106"
MAX_ROWS = 5000
MAX_FINDINGS = 20
MAX_EVIDENCE_ROWS = 4
MAX_NAME = 300

TERMINAL_FAMILIES = frozenset({"X", "XT", "XN", "XL", "XG"})

# Voltage signatures such as 24VDC, 230 VAC, 24 V DC, 110 В.
VOLTAGE_RE = re.compile(
    r"(?<![\w.])(\d{1,4})\s*(V\s*DC|V\s*AC|VDC|VAC|V|В)\b",
    re.I,
)
# Multi-part product type codes such as KPR-SCE-24VDC-1C or ABC/123-4.
TYPE_CODE_RE = re.compile(
    r"(?<![\w])([A-ZА-Я]{2,}[A-ZА-Я0-9]*(?:[-/][A-ZА-Я0-9]+)+)(?![\w])",
    re.I,
)

COMPATIBLE = "compatible"
UNKNOWN = "unknown"
AMBIGUOUS = "ambiguous"
VOLTAGE_CONFLICT = "voltage_conflict"
TYPE_CONFLICT = "type_conflict"


def _clean(value, limit=MAX_NAME):
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def family_of(designation):
    match = re.match(r"^\d*([A-ZА-Я]+)", str(designation or "").upper())
    return match.group(1) if match else ""


def name_signature(name):
    """Return a punctuation- and case-insensitive product-name signature."""
    return re.sub(r"[^0-9a-zа-я]+", "", _clean(name).casefold())


def voltage_signature(name):
    """Return the set of explicit nominal-voltage signatures in a name."""
    found = set()
    for number, suffix in VOLTAGE_RE.findall(_clean(name)):
        normalized_suffix = re.sub(r"\s+", "", suffix).upper().replace("В", "V")
        if normalized_suffix in ("V", "VDC", "VAC"):
            found.add(f"{int(number)}{normalized_suffix}")
    return found


def type_signature(name):
    """Return product type-code tokens, ignoring pure voltage fragments."""
    found = set()
    for token in TYPE_CODE_RE.findall(_clean(name)):
        value = token.upper()
        if not any(ch.isdigit() for ch in value):
            continue
        found.add(value)
    return found


def compare_names(left, right):
    """Classify a pair of PE3 product names.

    ``compatible`` - the same product description;
    ``voltage_conflict`` / ``type_conflict`` - incompatible attributes;
    ``ambiguous`` - names differ but no explicit incompatible attribute;
    ``unknown`` - at least one name is unavailable.
    """
    left, right = _clean(left), _clean(right)
    if not left or not right:
        return UNKNOWN
    if name_signature(left) == name_signature(right):
        return COMPATIBLE
    left_voltage, right_voltage = voltage_signature(left), voltage_signature(right)
    if left_voltage and right_voltage and not (left_voltage & right_voltage):
        return VOLTAGE_CONFLICT
    left_type, right_type = type_signature(left), type_signature(right)
    if left_type and right_type and not (left_type & right_type):
        return TYPE_CONFLICT
    return AMBIGUOUS


MIN_TABLE_ROW_TOP = 50


def _cell_key(row):
    band = row.get("row_band")
    if not band:
        return None
    # Header and title areas above the table are not product rows.  The same
    # lower bound is already used by the duplicate-evidence rule.
    if float(band[0]) < MIN_TABLE_ROW_TOP:
        return None
    return (row.get("page"), round(float(band[0]), 1), round(float(band[1]), 1))


def _row_label(row):
    label = f"лист PDF {row.get('page')}"
    if row.get("table_row") is not None:
        label += f", строка таблицы {row.get('table_row')}"
    return label


def _row_description(row):
    name = _clean(row.get("name"))
    quantity = row.get("quantity")
    parts = [f"{_row_label(row)}"]
    if isinstance(quantity, int):
        parts.append(f"количество {quantity}")
    parts.append(f"изделие «{name}»" if name else "наименование не извлечено")
    return "; ".join(parts)


def _issue(severity, filename, location, message, recommendation):
    return {
        "severity": severity,
        "code": CODE,
        "file": filename,
        "location": location,
        "message": message,
        "recommendation": recommendation,
    }


def _describe_conflict(reason, left, right):
    if reason == VOLTAGE_CONFLICT:
        values = sorted(voltage_signature(left.get("name")) | voltage_signature(right.get("name")))
        return "различаются номинальные напряжения: " + ", ".join(values)
    if reason == TYPE_CONFLICT:
        values = sorted(type_signature(left.get("name")) | type_signature(right.get("name")))
        return "различаются типы изделий: " + ", ".join(values[:6])
    return "наименования различаются, но явные несовместимые атрибуты не установлены"


def conflict_findings(rows, filename):
    """Return KD-PE-106 findings for repeated PE3 designations.

    Only rows with grid-confirmed bands are used.  The function never chooses a
    correct variant, never removes a row and never rewrites the document.
    """
    if not rows:
        return []
    occurrences = defaultdict(dict)
    for row in rows[:MAX_ROWS]:
        if row.get("unknown"):
            continue
        cell = _cell_key(row)
        if cell is None:
            continue
        for designation in sorted(row.get("entries") or set()):
            occurrences[designation][cell] = row

    findings = []
    for designation in sorted(occurrences):
        if len(findings) >= MAX_FINDINGS:
            break
        cells = occurrences[designation]
        if len(cells) < 2:
            continue
        ordered = [row for _, row in sorted(cells.items(), key=lambda item: item[0])]
        pairs = []
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                pairs.append((left, right, compare_names(left.get("name"), right.get("name"))))

        conflicts = [(left, right, reason) for left, right, reason in pairs
                     if reason in (VOLTAGE_CONFLICT, TYPE_CONFLICT)]
        ambiguous = [(left, right, reason) for left, right, reason in pairs
                     if reason == AMBIGUOUS]
        terminal = family_of(designation) in TERMINAL_FAMILIES

        if conflicts and not terminal:
            left, right, reason = conflicts[0]
            shown = ordered[:MAX_EVIDENCE_ROWS]
            lines = [
                f"Для проверки инженером: обозначение «{designation}» встречается в "
                f"{len(ordered)} строках ПЭ3 с несовместимыми данными изделия.",
                "Строки: " + " | ".join(_row_description(row) for row in shown) + ".",
                f"Причина: {_describe_conflict(reason, left, right)}.",
                "Уровень достоверности: высокий, если наименования извлечены из "
                "подтверждённых ячеек; иначе — средний.",
                "Автоматический выбор правильного варианта не выполняется; "
                "строки исходного документа не изменяются.",
            ]
            findings.append(_issue(
                "warning", filename,
                "; ".join(f"ПЭ3: {_row_label(row)}" for row in shown),
                " ".join(lines),
                "Сопоставьте обе строки с Э3 и спецификацией изделия. "
                "Определите, является ли повтор продолжением записи, комплектованием "
                "или ошибкой обозначения. Решение принимает инженер; "
                "автоматическое исправление не выполняется.",
            ))
            continue

        if ambiguous:
            left, right, _ = ambiguous[0]
            shown = ordered[:MAX_EVIDENCE_ROWS]
            findings.append(_issue(
                "info", filename,
                "; ".join(f"ПЭ3: {_row_label(row)}" for row in shown),
                f"Для проверки инженером: обозначение «{designation}» повторяется в "
                f"{len(ordered)} строках ПЭ3, но наименования различаются без явного "
                f"несовместимого атрибута. Строки: "
                + " | ".join(_row_description(row) for row in shown)
                + ". Автоматический вывод о конфликте не делается.",
                "Проверьте, относятся ли строки к одному изделию, его составным частям "
                "или к разным исполнениям. Не удаляйте строки автоматически.",
            ))
    return findings
