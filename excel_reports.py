"""Local XLSX export of saved checks. No document processing or network calls."""
from io import BytesIO
import json

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from issue_tracking import STATUS_LABELS


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
    states = {state['issue_index']: state for state in run.get('finding_states', [])}
    for index, item in enumerate(run['issues'], 1):
        label, color = LEVELS.get(item['severity'], (item['severity'], 'FFFFFF'))
        findings.append([index])
        values = [label] + [item.get(key, '') for key in ('code', 'file', 'location', 'message', 'recommendation')]
        state = states.get(index-1, {})
        values += [STATUS_LABELS.get(state.get('status', 'new'), 'Новое'),
                   state.get('comment', ''), state.get('updated_at') or '']
        for column, value in enumerate(values, 2):
            cell = findings.cell(index+1, column)
            _text(cell, value)
            cell.fill = PatternFill('solid', fgColor=color)
        findings.cell(index+1, 1).fill = PatternFill('solid', fgColor=color)
    findings.auto_filter.ref = f'A1:J{max(1, findings.max_row)}'
    _format_sheet(findings, [7, 20, 18, 38, 24, 80, 80, 32, 70, 36])
    findings.print_title_rows = '1:1'

    extraction = run.get('extracted_entities')
    application = run.get('app_metadata')
    if extraction or application:
        observed = workbook.create_sheet('Извлечение')
        observed.append(['Параметр', 'Значение'])
        for label, value in [
            ('Версия приложения', (application or {}).get('version', 'не указана')),
            ('Ветка сборки', (application or {}).get('branch', 'не указана')),
            ('Версия сборки', (application or {}).get('build', 'не указана')),
            ('Устройства', (extraction or {}).get('devices', 0)),
            ('Строки ПЭ3', (extraction or {}).get('spec_rows', 0)),
            ('Соединения', (extraction or {}).get('connections', 0)),
            ('Ссылки на документы', (extraction or {}).get('document_refs', 0)),
            ('Упоминания в Э3', (extraction or {}).get('scheme_mentions', 0)),
        ]:
            observed.append([label, None])
            _text(observed.cell(observed.max_row, 2), value)
        preview = (extraction or {}).get('preview', {})
        header_rows = [1]
        observed.append([])
        header_rows.append(observed.max_row + 1)
        observed.append(['Обозначение устройства'])
        for device in preview.get('devices', []):
            observed.append([None])
            _text(observed.cell(observed.max_row, 1), device.get('designation', ''))
        observed.append([])
        header_rows.append(observed.max_row + 1)
        observed.append(['Документ ПЭ3', 'Строка', 'Обозначение', 'Количество', 'Наименование'])
        for row in preview.get('pe3_rows', []):
            observed.append([None, row.get('row'), None, row.get('quantity'), None])
            _text(observed.cell(observed.max_row, 1), row.get('document', ''))
            _text(observed.cell(observed.max_row, 3), row.get('designation', ''))
            _text(observed.cell(observed.max_row, 5), row.get('name', ''))
        observed.append([])
        header_rows.append(observed.max_row + 1)
        observed.append(['Документ', 'Лист', 'Строка', 'Источник', 'Назначение',
                         'Провод', 'Кабель', 'Цвет', 'Сечение'])
        for row in preview.get('connections', []):
            observed.append([None, None, row.get('row'), None, None, None, None, None, None])
            for column, key in enumerate(
                    ('document', 'sheet', None, 'source', 'target', 'wire',
                     'cable', 'color', 'cross_section'), 1):
                if key:
                    _text(observed.cell(observed.max_row, column), row.get(key, ''))
        _format_sheet(observed, [34, 24, 14, 28, 28, 20, 20, 20, 20],
                      header_rows=tuple(header_rows))

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

