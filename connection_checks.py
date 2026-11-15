"""Opt-in checks of explicit structured connections; never infer PDF topology."""
from dataclasses import dataclass, fields
import unicodedata

from engine import issue


def _text(value, *, optional=False):
    if not isinstance(value, str):
        raise ValueError('Connection fields must be strings')
    value = unicodedata.normalize('NFC', value.strip())
    if (not value and not optional) or len(value) > 500 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Missing, oversized or ambiguous connection field')
    return value


@dataclass(frozen=True)
class Endpoint:
    device: str
    terminal: str

    def __post_init__(self):
        for field in fields(self):
            object.__setattr__(self, field.name, _text(getattr(self, field.name)))


@dataclass(frozen=True)
class Connection:
    project: str
    revision: str
    wire: str
    cable: str
    source: Endpoint
    target: Endpoint
    document: str
    location: str

    def __post_init__(self):
        if not isinstance(self.source, Endpoint) or not isinstance(self.target, Endpoint):
            raise ValueError('Explicit device and terminal required at both ends')
        for name in ('project', 'revision', 'wire', 'cable', 'document', 'location'):
            object.__setattr__(self, name, _text(getattr(self, name), optional=name == 'cable'))


def check_connections(records):
    """Return informational review prompts for validated structured records.

    Scope/revision are supplied by a trusted importer, not inferred from names.
    Direction and exact spelling are preserved. Repeated wires on other terminals
    can be legitimate branches and are deliberately not reported as duplicates.
    """
    records = list(records)
    if any(not isinstance(record, Connection) for record in records):
        raise ValueError('Only validated Connection records are supported')
    out, seen, occurrences = [], {}, set()
    for record in records:
        key = (record.project, record.revision, record.wire, record.cable, record.source, record.target)
        occurrence = (key, record.document, record.location)
        if occurrence in occurrences:
            continue
        occurrences.add(occurrence)
        if record.source == record.target:
            out.append(issue('info', 'KD-CON-102', record.document, record.location,
                f'Источник и приёмник записи совпадают: {record.source.device}:{record.source.terminal}',
                'Проверьте структурированный экспорт и назначение соединения; электрическая ошибка не подтверждена'))
        if key in seen:
            first = seen[key]
            out.append(issue('info', 'KD-CON-101', record.document, record.location,
                f'Повтор структурированной записи соединения {record.wire}; первая: {first.document}, {first.location}',
                'Сверьте источники: это может быть повтор экспорта или допустимое повторное представление'))
        else:
            seen[key] = record
    return out
