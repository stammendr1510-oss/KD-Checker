"""Local XLSX export of saved checks. No document processing or network calls."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


LEVELS = {
    'error': ('Ошибка', 'FDE9E7'),
    'warning': ('Предупреждение', 'FFF4CC'),
    'info': ('Информация', 'E8F1FB'),
}
HEADERS = ['№', 'Уровень', 'Код', 'Документ', 'Лист/место', 'Описание', 'Рекомендация']


def _text(cell, value):
    # XLSX has an explicit string type: retain even =,+,-,@ prefixes literally.
    # Never let openpyxl infer formulas or Excel error values from saved text.
    text = str(value if value is not None else '').replace('\r\n', '\n').replace('\r', '\n')
    cell.value = ILLEGAL_CHARACTERS_RE.sub('', text)
    cell.data_type = 's'


def _format_sheet(sheet, widths, header_rows=(1,)):
    sheet.freeze_panes = 'A2'
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if cell.row in header_rows:
                cell.fill = PatternFill('solid', fgColor='152848')
                cell.font = Font(color='FFFFFF', bold=True)
    for index in header_rows:
        sheet.row_dimensions[index].height = 30


def build_excel_report(run):
    """Return XLSX bytes for the existing storage.get_run() record format."""
    workbook = Workbook()
    general = workbook.active
    general.title = 'Общие сведения'
    general.append(['Параметр', 'Значение'])
    general.append(['№ проверки', run['id']])
    general.append(['Дата проверки (UTC)', None])
    _text(general['B3'], run['created_at'])
    general.append(['Количество файлов', len(run['files'])])
    for level, label in [('error', 'Ошибки'), ('warning', 'Предупреждения'), ('info', 'Информационные сообщения')]:
        general.append([label, sum(item['severity'] == level for item in run['issues'])])
        for cell in general[general.max_row]:
            cell.fill = PatternFill('solid', fgColor=LEVELS[level][1])
    general.append([])
    general.append(['№', 'Документ'])
    for index, filename in enumerate(run['files'], 1):
        general.append([index, None])
        _text(general.cell(general.max_row, 2), filename)
    general.auto_filter.ref = f'A9:B{max(9, general.max_row)}'
    _format_sheet(general, [34, 100], header_rows=(1, 9))

    findings = workbook.create_sheet('Замечания')
    findings.append(HEADERS)
    for index, item in enumerate(run['issues'], 1):
        label, color = LEVELS.get(item['severity'], (item['severity'], 'FFFFFF'))
        findings.append([index])
        values = [label] + [item.get(key, '') for key in ('code', 'file', 'location', 'message', 'recommendation')]
        for column, value in enumerate(values, 2):
            cell = findings.cell(index+1, column)
            _text(cell, value)
            cell.fill = PatternFill('solid', fgColor=color)
        findings.cell(index+1, 1).fill = PatternFill('solid', fgColor=color)
    findings.auto_filter.ref = f'A1:G{max(1, findings.max_row)}'
    _format_sheet(findings, [7, 20, 18, 38, 24, 80, 80])
    findings.print_title_rows = '1:1'
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
