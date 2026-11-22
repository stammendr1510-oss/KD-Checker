"""Local XLSX export of saved checks. No document processing or network calls."""
from io import BytesIO
import json

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from issue_tracking import STATUS_LABELS
from kd_pe_105_evidence import format_evidence_text


LEVELS = {
    'error': ('Ошибка', 'FDE9E7'),
    'warning': ('Предупреждение', 'FFF4CC'),
    'info': ('Информация', 'E8F1FB'),
}
HEADERS = ['№', 'Уровень', 'Код', 'Документ', 'Лист/место', 'Описание', 'Рекомендация',
           'Статус', 'Комментарий инженера', 'Дата изменения']


def _text(cell, value):
    # XLSX has an explicit string type: retain even =,+,-,@ prefixes literally.
    # Never let openpyxl infer formulas or Excel error values from saved text.
    text = str(value if value is not None else '').replace('\r\n', '\n').replace('\r', '\n')
    cell.value = ILLEGAL_CHARACTERS_RE.sub('', text)
    cell.data_type = 's'


def _details_json(details):
    try:
        value = json.dumps(details, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        return ''
    return value if len(value) <= 30000 else value[:29900] + '… (усечено)'


def _details_text(details, issue=None):
    """Human-readable evidence block derived from schema v1 ``details``.

    Coordinates and internal JSON are never rendered here; the structured JSON
    keeps its own Excel column.
    """
    if not isinstance(details, dict):
        return ''
    return format_evidence_text(issue if isinstance(issue, dict) else {'details': details})


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
    has_details = any(isinstance(item, dict) and 'details' in item for item in run['issues'])
    header = list(HEADERS)
    if has_details:
        header += ['Доказательства KD-PE-105', 'Детали KD-PE-105 (JSON)']
    findings.append(header)
    states = {state['issue_index']: state for state in run.get('finding_states', [])}
    for index, item in enumerate(run['issues'], 1):
        label, color = LEVELS.get(item['severity'], (item['severity'], 'FFFFFF'))
        findings.append([index])
        values = [label] + [item.get(key, '') for key in ('code', 'file', 'location', 'message', 'recommendation')]
        state = states.get(index-1, {})
        values += [STATUS_LABELS.get(state.get('status', 'new'), 'Новое'),
                   state.get('comment', ''), state.get('updated_at') or '']
        if has_details:
            details = item.get('details')
            values += [_details_text(details, item), _details_json(details)] if details else ['', '']
        for column, value in enumerate(values, 2):
            cell = findings.cell(index+1, column)
            _text(cell, value)
            cell.fill = PatternFill('solid', fgColor=color)
        findings.cell(index+1, 1).fill = PatternFill('solid', fgColor=color)
    findings.auto_filter.ref = (
        f'A1:L{max(1, findings.max_row)}' if has_details else f'A1:J{max(1, findings.max_row)}'
    )
    widths = [7, 20, 18, 38, 24, 80, 80, 32, 70, 36]
    if has_details:
        widths += [70, 80]
    _format_sheet(findings, widths)
    findings.print_title_rows = '1:1'
    checklist = run.get('checklist')
    if checklist is not None:
        # Preserve the exact historical checklist snapshot; never fetch at
        # export time or present an offline response as a current checklist.
        checklist_sheet = workbook.create_sheet('EPLAN Checklist')
        checklist_sheet.append(['Параметр', 'Значение'])
        statuses = {
            'verified': 'Актуальный список получен на момент проверки',
            'unavailable': 'Источник недоступен — актуальность не подтверждена',
            'not_configured': 'Источник не настроен — актуальность не подтверждена',
        }
        status = checklist.get('status', 'not_configured')
        for label, value in [
            ('Статус синхронизации', statuses.get(status, 'Актуальность не подтверждена')),
            ('Причина', checklist.get('reason') or ''),
            ('Версия (SHA-256)', checklist.get('revision') or ''),
            ('Время получения (UTC)', checklist.get('retrieved_at') or ''),
        ]:
            checklist_sheet.append([label, None])
            _text(checklist_sheet.cell(checklist_sheet.max_row, 2), value)
        checklist_sheet.append([])
        checklist_sheet.append(['ID пункта', 'Раздел', 'Требование', 'Статус', 'Пояснение', 'Комментарий к пункту', 'Ссылка'])
        if status == 'verified':
            for review in checklist.get('reviews', []):
                checklist_sheet.append([review.get('item_id')])
                values = [
                    review.get('section_title', ''),
                    review.get('title', ''),
                    'Требует проверки инженером' if review.get('status') == 'manual_review'
                    else 'Не подтверждено автоматически',
                    review.get('explanation', ''),
                    review.get('comment', ''),
                    review.get('url', ''),
                ]
                for col, value in enumerate(values, 2):
                    _text(checklist_sheet.cell(checklist_sheet.max_row, col), value)
        checklist_sheet.auto_filter.ref = f'A7:G{max(7, checklist_sheet.max_row)}'
        _format_sheet(checklist_sheet, [16, 39, 85, 32, 80, 80, 55], header_rows=(1, 7))

    if 'provenance' in run:
        provenance = run['provenance']
        sheet = workbook.create_sheet('Происхождение')
        sheet.append(['Параметр', 'Значение'])
        rows = [('Статус', 'Снимок записан' if provenance.get('status')=='recorded' else 'Не записано: исторические настройки неизвестны')]
        if provenance.get('status')=='recorded':
            evidence = provenance['evidence']
            rows += [('Pipeline', evidence['pipeline']), ('Формат снимка', evidence['format_version']),
                     ('Версия конфигурации', evidence['configuration_version'] if evidence['configuration_version'] is not None else 'Неприменимо'),
                     ('Настройки', json.dumps(evidence['configuration'],ensure_ascii=False,sort_keys=True)),
                     ('Исходники (SHA-256)', evidence['source_fingerprint']), ('Python', evidence['python_version']),
                     ('Зависимости', json.dumps(evidence['dependencies'],ensure_ascii=False,sort_keys=True)),
                     ('Лимиты парсера', json.dumps(evidence['parser_limits'],ensure_ascii=False,sort_keys=True))]
        rows.append(('Ограничение', 'Локальный снимок не является аттестацией, доказательством ГОСТ или инженерной правильности'))
        for label,value in rows:
            sheet.append([label,None])
            _text(sheet.cell(sheet.max_row,2),value)
        _format_sheet(sheet,[34,110])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()

