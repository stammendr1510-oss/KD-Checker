"""Structured connection import and header-driven XLSX routing."""
from dataclasses import dataclass
from io import BytesIO
import json
import re
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from engineering_classifier import split_cable_reference
from connection_checks import Connection, Endpoint, check_connections, is_potential

Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]

MAX_XLSX_SHEETS = 30
MAX_XLSX_ROWS = 5000
MAX_XLSX_COLUMNS = 40

HEADER_ALIASES = {
    'position': (r'^(?:поз(?:иция)?|position|item)$',),
    'designation': (r'^(?:обозначение|designat(?:ion|or)|reference)$',),
    'quantity': (r'^(?:кол(?:ичество)?|qty|quantity|count)$',),
    'name': (r'^(?:наименование|описание|name|description)$',),
    'format': (r'^(?:формат|format)$',),
    'zone': (r'^(?:зона|zone)$',),
    'document_ref': (r'^(?:документ|обозначение документа|document|document reference)$',),
    'source_device': (r'^(?:устройство источника|начальное устройство|source device|from device|откуда)$',),
    'source_terminal': (r'^(?:контакт источника|начальный контакт|source terminal|from terminal)$',),
    'target_device': (r'^(?:устройство назначения|конечное устройство|target device|to device|куда)$',),
    'target_terminal': (r'^(?:контакт назначения|конечный контакт|target terminal|to terminal)$',),
    'wire': (r'^(?:провод|маркировка провода|wire|wire mark|соединение)$',),
    'cable': (r'^(?:кабель|наименование кабеля|cable|group|группа|cable group)$',),
    'color': (r'^(?:цвет|цвет провода|wire colou?r|colou?r)$',),
    'cross_section': (r'^(?:сечение|поперечное сечение|сечение провода|площадь сечения|cross[ -]?section|wire size|area)$',),
    'contact': (r'^номер контакта$',),
    'wire_type': (r'^(?:тип провода|wire type)$',),
    'sheet_number': (r'^лист\s*№$',),
    'project': (r'^(?:проект|project)$',),
    'revision': (r'^(?:ревизия|версия|revision)$',),
}

CONNECTION_KEYS = frozenset({
    'source_device', 'source_terminal', 'target_device',
    'target_terminal', 'wire',
})


@dataclass(frozen=True)
class RoutedSheet:
    name: str
    role: str
    header_row: int | None
    records: tuple[dict, ...]
    limits: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedWorkbook:
    sheets: tuple[RoutedSheet, ...]

    def sheets_for(self, *roles):
        accepted = set(roles)
        return tuple(sheet for sheet in self.sheets if sheet.role in accepted)


def normalize_header(value):
    text = re.sub(r'\s+', ' ', str(value or '').replace('\n', ' ')).strip().casefold()
    for key, patterns in HEADER_ALIASES.items():
        if any(re.fullmatch(pattern, text, re.I) for pattern in patterns):
            return key
    return None


def _route_role(keys):
    keys = set(keys)
    if {'source_device', 'target_device'} <= keys:
        return 'connections'
    if 'document_ref' in keys:
        return 'sp'
    if {'designation', 'quantity'} <= keys and ({'format', 'zone', 'position'} & keys):
        return 'sp'
    if {'designation', 'quantity'} <= keys:
        return 'pe3'
    if {'position', 'quantity'} <= keys:
        return 'pe3'
    return 'unknown'


def _route_sheet(sheet):
    values = list(sheet.iter_rows(
        min_row=1, max_row=min(sheet.max_row or 0, MAX_XLSX_ROWS),
        max_col=min(sheet.max_column or 0, MAX_XLSX_COLUMNS), values_only=True,
    ))
    for header_index, row in enumerate(values[:20]):
        columns = {index: normalize_header(value) for index, value in enumerate(row)}
        columns = {index: key for index, key in columns.items() if key}
        # Assign duplicate contacts by the preceding endpoint column, never by name.
        endpoint = None
        for index in sorted(columns):
            key = columns[index]
            if key in ('source_device', 'target_device'):
                endpoint = key.split('_')[0]
            elif key == 'contact' and endpoint:
                columns[index] = endpoint + '_terminal'
        duplicates = len(columns.values()) != len(set(columns.values()))
        role = _route_role(columns.values())
        if role == 'unknown':
            continue
        records = []
        for row_number, source in enumerate(values[header_index + 1:], header_index + 2):
            record = {key: source[index] if index < len(source) else None
                      for index, key in columns.items()}
            if role == 'pe3' and 'designation' not in record and 'position' in record:
                record['designation'] = record['position']
            if any(value not in (None, '') for value in source):
                record['_row_number'] = row_number
                if duplicates:
                    record['_header_ambiguous'] = True
                records.append(record)
        return RoutedSheet(sheet.title, role, header_index + 1, tuple(records),
                           ('Достигнут предел строк или столбцов',) if
                           sheet.max_row > MAX_XLSX_ROWS or sheet.max_column > MAX_XLSX_COLUMNS else ())
    return RoutedSheet(sheet.title, 'unknown', None, tuple(
        {'_row_number': i, '_unrecognized': True} for i, row in enumerate(values, 1)
        if any(v not in (None, '') for v in row)), ('Заголовки таблицы не распознаны',))


def route_xlsx(content: bytes) -> RoutedWorkbook:
    """Classify bounded workbook sheets once, using headers rather than names."""
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        return RoutedWorkbook(tuple(_route_sheet(sheet)
                                    for sheet in workbook.worksheets[:MAX_XLSX_SHEETS]))
    finally:
        workbook.close()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class End(StrictModel):
    device: Text
    terminal: Text


class Record(StrictModel):
    wire: Text
    cable: Annotated[str, StringConstraints(strict=True, max_length=500)]
    source: End
    target: End
    document: Text
    location: Text


class Payload(StrictModel):
    schema_version: Literal[1]
    project: Text
    revision: Text
    records: list[Record] = Field(min_length=1, max_length=1000)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_connections(raw):
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        payload = Payload.model_validate(data)
        if type(data.get('schema_version')) is not int:
            raise ValueError('Invalid schema version')
        records = [Connection(project=payload.project, revision=payload.revision,
                              source=Endpoint(**row.source.model_dump()), target=Endpoint(**row.target.model_dump()),
                              **row.model_dump(exclude={'source', 'target'})) for row in payload.records]
        files = list(dict.fromkeys(row.document for row in records))
        if len(files) > 100:
            raise ValueError('Too many source documents')
        return payload, files, check_connections(records)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid structured input') from None


def classify_connection_row(row):
    """Return a complete accounting result; never fabricate contacts/wire IDs."""
    if row.get('_header_ambiguous'):
        return 'ambiguous', 'Повторяющиеся столбцы не имеют однозначной роли'
    keys = ('source_device', 'source_terminal', 'target_device', 'target_terminal', 'wire')
    def cell(key):
        value = row.get(key)
        return str(value if value is not None else '').strip()

    missing = [key for key in keys if not cell(key)]
    for side in ('source', 'target'):
        terminal = side + '_terminal'
        if terminal in missing and is_potential(cell(side + '_device')):
            missing.remove(terminal)
    if missing:
        labels = dict(source_device='источник', source_terminal='контакт источника',
                      target_device='назначение', target_terminal='контакт назначения', wire='маркировка соединения')
        return 'incomplete', 'Не заполнены: ' + ', '.join(labels[key] for key in missing)
    import unicodedata
    for key in keys + ('cable', 'project', 'revision', 'color', 'cross_section', 'wire_type'):
        value = str(row.get(key) or '').strip()
        if len(value) > 500 or any(unicodedata.category(c).startswith('C') for c in value):
            return 'rejected', 'Недопустимая длина или управляющие символы в ячейке'
        if key.endswith('_device') and any(c in value for c in ';,\n'):
            return 'ambiguous', 'Несколько конечных точек в одной ячейке'
    return 'extracted', 'Оба конца и маркировка соединения заполнены'


def connection_metadata(row, known_kinds=None, *, endpoints=()):
    """Retain wire properties independently of the group/cable decision."""
    cable, block = split_cable_reference(row.get('cable'), known_kinds, endpoints=endpoints)
    def cell(key):
        value = row.get(key)
        return str(value if value is not None else '').strip()
    return dict(cable=cable, bus_or_block_ref=block, color=cell('color'),
                cross_section=cell('cross_section'), wire_type=cell('wire_type'))
