"""Standalone XLSX audit export of two archived EPLAN Checklist revisions.

No network requests and no checks of engineering conformity.
All historic source strings are written as literal Excel strings.
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from checklist_diff_csv import FIELD_NAMES
from excel_reports import _format_sheet, _text

HEADERS = ("Изменение", "ID требования", "Поле", "Было", "Стало")
COLORS = {"Добавлено": "E8F6EC", "Удалено": "FCEBEC", "Изменено": "FFF5DC"}


def build_checklist_diff_xlsx(diff):
    if not diff.get("available"):
        raise ValueError("Для экспорта нужны две подтверждённые редакции EPLAN Checklist")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Изменения Checklist"
    sheet.append(list(HEADERS))
    for kind, items in (("Добавлено", diff["added"]), ("Удалено", diff["removed"])):
        for item in items:
            for field, label in FIELD_NAMES.items():
                value = item.get(field, "")
                before, after = ((value, "") if kind == "Удалено" else ("", value))
                _append_row(sheet, kind, item["id"], label, before, after)
    for change in diff["changed"]:
        for field in change["fields"]:
            _append_row(
                sheet, "Изменено", change["item_id"],
                FIELD_NAMES.get(field, field),
                change["previous"].get(field, ""),
                change["current"].get(field, ""),
            )

    sheet.auto_filter.ref = f"A1:E{max(1, sheet.max_row)}"
    _format_sheet(sheet, [20, 20, 25, 85, 85])
    sheet.print_title_rows = "1:1"

    # Only metadata from the already verified, saved comparison is exported.
    # This second sheet makes an archived workbook traceable without network
    # access or implying that either revision is currently up to date.
    audit = workbook.create_sheet("Редакции")
    audit.append(["Параметр", "Значение"])
    for label, value in (
        ("Предыдущая редакция SHA-256", diff.get("previous_revision") or ""),
        ("Текущая редакция SHA-256", diff.get("current_revision") or ""),
        ("Добавлено требований", diff["summary"]["added"]),
        ("Удалено требований", diff["summary"]["removed"]),
        ("Изменено требований", diff["summary"]["changed"]),
        ("Ограничение проверки", diff.get("reason") or
         "Сопоставление формулировок требований не подтверждает соответствие КД"),
    ):
        audit.append([label, None])
        _text(audit.cell(audit.max_row, 2), value)
    _format_sheet(audit, [45, 105])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _append_row(sheet, kind, item_id, label, before, after):
    sheet.append([None, item_id, None, None, None])
    row = sheet.max_row
    for col, value in ((1, kind), (3, label), (4, before), (5, after)):
        _text(sheet.cell(row, col), value)
    color = COLORS[kind]
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor=color)
