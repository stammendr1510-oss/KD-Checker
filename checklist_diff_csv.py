"""CSV export of archived EPLAN Checklist requirement differences.

This report describes changes to source requirements, never KD compliance.
No live requests; all values originate from immutable saved snapshots.
"""
import csv
from io import StringIO

FIELD_NAMES = {
    "section_id": "ID раздела", "section_title": "Раздел",
    "title": "Формулировка", "comment": "Примечание",
    "url": "Источник", "tab": "Вкладка", "position": "Порядок",
}
HEADER = ["Тип изменения", "ID требования", "Поле", "Было", "Стало"]


def _safe_cell(value):
    """Protect CSV opened in Excel, including whitespace-prefixed formulas."""
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def build_checklist_diff_csv(comparison):
    if not comparison.get("available"):
        raise ValueError("Нельзя экспортировать неподтверждённые редакции чек-листа")
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(HEADER)
    for kind, rows in (("Добавлено", comparison["added"]), ("Удалено", comparison["removed"])):
        for row in rows:
            # Preserve all known fields from the historic requirement, including
            # blank optional fields. Each row names its field for reliable audits.
            for field, label in FIELD_NAMES.items():
                value = _safe_cell(row.get(field, ""))
                writer.writerow([
                    kind, row["id"], label,
                    value if kind == "Удалено" else "",
                    value if kind == "Добавлено" else "",
                ])
    for change in comparison["changed"]:
        for field in change["fields"]:
            writer.writerow([
                "Изменено", change["item_id"], FIELD_NAMES.get(field, field),
                _safe_cell(change["previous"].get(field, "")),
                _safe_cell(change["current"].get(field, "")),
            ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")
