"""Unified deterministic engineering CAD model (KD-PE-108).

Pure data model + verifiers. No PDF/Excel I/O here (adapters.py feeds it) and
no AI/network. Designation-agnostic: reference classes come from naming.py
configuration, never from per-class special cases.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from connection_checks import Connection
from engine import issue
from naming import CYR_LOOKALIKES


# --- Provenance -------------------------------------------------------------

@dataclass(frozen=True)
class SourceRef:
    """Where an entity was observed: document + page/row/cell coordinates."""
    document: str
    page: int | None = None
    row: int | None = None
    bounds: tuple | None = None  # (x0, y0, x1, y1) in PDF points, optional

    def describe(self) -> str:
        parts = [self.document]
        if self.page is not None:
            parts.append(f'лист {self.page}')
        if self.row is not None:
            parts.append(f'строка {self.row}')
        return ', '.join(parts)


# --- Designations & devices -------------------------------------------------

@dataclass(frozen=True)
class Designation:
    raw: str            # exact source spelling
    normalized: str     # Cyrillic-lookalike folded, uppercased
    alphabet: str       # 'lat' | 'cyr' | 'mixed'

    @staticmethod
    def classify(raw: str) -> 'Designation':
        folded = raw.translate(CYR_LOOKALIKES)
        upper = folded.upper().strip()
        if upper != raw.upper().strip():
            alphabet = 'mixed'
        else:
            alphabet = 'lat'
        return Designation(raw=raw, normalized=upper.replace(' ', ''), alphabet=alphabet)


@dataclass
class Device:
    designation: Designation
    cls: str                       # reference class (e.g. prefix letters), not a rule key
    provenance: list[SourceRef] = field(default_factory=list)


@dataclass
class AmbiguousIdentity:
    """Same normalized form spelled in different alphabets across sources."""
    normalized: str
    forms: dict[str, SourceRef] = field(default_factory=dict)  # raw spelling -> first seen


@dataclass
class SchemeMention:
    designation: Designation
    provenance: SourceRef


# --- PE3 / SP rows ----------------------------------------------------------

@dataclass
class SpecRow:
    raw_text: str
    quantity: int | None
    designations: list[Designation]      # expanded entries
    unknown: list[str] = field(default_factory=list)
    provenance: SourceRef | None = None

    @property
    def confirmed_count(self) -> int:
        return len(self.designations)


@dataclass
class DocumentRef:
    """A designation of another document explicitly listed (e.g. in СП)."""
    kind: str      # Э3 | ПЭ3 | ВО | СП | ...
    key: str       # base designation (casefold, no kind suffix)
    raw: str
    provenance: SourceRef | None = None


# --- Model ------------------------------------------------------------------

@dataclass
class EngineeringModel:
    devices: dict[str, Device] = field(default_factory=dict)          # normalized -> Device
    scheme_mentions: list[SchemeMention] = field(default_factory=list)
    spec_rows: list[SpecRow] = field(default_factory=list)
    document_refs: list[DocumentRef] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    ambiguous: dict[str, AmbiguousIdentity] = field(default_factory=dict)
    # (kind, key) identities of uploaded documents, supplied by the adapter.
    uploaded_identities: set = field(default_factory=set)

    def add_device(self, des: Designation, ref: SourceRef):
        dev = self.devices.get(des.normalized)
        if dev is None:
            dev = Device(designation=des, cls=''.join(c for c in des.normalized if c.isalpha()))
            self.devices[des.normalized] = dev
        dev.provenance.append(ref)
        if des.alphabet == 'mixed' or (dev.designation.alphabet != des.alphabet):
            slot = self.ambiguous.setdefault(des.normalized, AmbiguousIdentity(des.normalized))
            slot.forms.setdefault(dev.designation.raw, dev.provenance[0])
            slot.forms.setdefault(des.raw, ref)


# --- Verifiers (EM-108-1..3) -------------------------------------------------

CODE_QTY = 'KD-EM-108-1'
CODE_REF = 'KD-EM-108-2'
CODE_AMB = 'KD-EM-108-3'


def verify_row_quantities(model: EngineeringModel) -> list[dict]:
    """EM-108-1: PE3 row quantity differs from expanded designation count."""
    out = []
    for row in model.spec_rows:
        if row.quantity is None or row.quantity == row.confirmed_count:
            continue
        place = row.provenance.describe() if row.provenance else 'Перечень элементов'
        out.append(issue('warning', CODE_QTY, row.provenance.document if row.provenance else '',
            place,
            f'Для дополнительной проверки: количество {row.quantity} отличается от числа распознанных обозначений ({row.confirmed_count}) в записи «{row.raw_text}»',
            'Требуется проверка инженером: сопоставьте количество и явно указанные обозначения в исходной строке, включая переносы и диапазоны. Это не подтверждённая ошибка КД.'))
    return out


def verify_document_refs(model: EngineeringModel) -> list[dict]:
    """EM-108-2: document explicitly listed in СП is absent from the model."""
    # A ref is a claim; presence is checked against uploaded identities
    # supplied by the adapter (model.uploaded_identities).
    uploaded = model.uploaded_identities
    out = []
    seen_codes = set()
    for ref in model.document_refs:
        if (ref.kind, ref.key) in uploaded:
            continue
        if ref.provenance is None:
            continue
        key = (CODE_REF, ref.kind, ref.key)
        if key in seen_codes:
            continue
        seen_codes.add(key)
        out.append(issue('warning', CODE_REF, ref.provenance.document,
            ref.provenance.describe(),
            f'«{ref.raw}» явно перечислен в документе, но не включён в загрузку среди распознанных PDF. Это не утверждение об отсутствии документа в выпуске.',
            'Проверьте комплектность загрузки; при необходимости сверьте с полным составом проекта.'))
    return out


def verify_ambiguous_identities(model: EngineeringModel) -> list[dict]:
    """EM-108-3: lookalike/mixed-alphabet spellings of one normalized form."""
    out = []
    for norm, amb in sorted(model.ambiguous.items()):
        forms = sorted(amb.forms)
        places = [f'{raw} ({ref.describe()})' for raw, ref in sorted(amb.forms.items())]
        out.append(issue('info', CODE_AMB, amb.forms[forms[0]].document,
            amb.forms[forms[0]].describe(),
            f'Смешение похожих латинских и кириллических букв в обозначении «{norm}»: {", ".join(forms)}. Места исходных написаний: {"; ".join(places)}.',
            'Унифицируйте алфавит в исходном проекте; проверьте, не разные ли это объекты'))
    return out


def run_verifiers(model: EngineeringModel) -> list[dict]:
    return (verify_row_quantities(model)
            + verify_document_refs(model)
            + verify_ambiguous_identities(model))


# --- KD-PE-109 universal rule pack -------------------------------------------

CODE_CONFLICT = 'KD-EM-109-1'
CODE_COVERAGE = 'KD-EM-109-2'
CODE_CONTACT = 'KD-EM-109-3'
MAX_EXAMPLES = 8


def verify_endpoint_conflicts(model: EngineeringModel) -> list[dict]:
    """EM-109-1: one (device, terminal) endpoint claimed by different wires."""
    from naming import normalized as _norm
    claims: dict = {}
    for record in model.connections:
        for end in (record.source, record.target):
            key = (_norm(end.device), _norm(end.terminal))
            claims.setdefault(key, []).append(record)
    out = []
    for (device, terminal), records in sorted(claims.items()):
        wires = {r.wire for r in records}
        if len(records) < 2 or len(wires) < 2:
            continue
        first = records[0]
        places = '; '.join(sorted({f'{r.wire}: {r.document}, {r.location}' for r in records}))
        out.append(issue('warning', CODE_CONFLICT, first.document, first.location,
            f'Контакт «{device}:{terminal}» использован в разных проводах: {places}. '
            'Это не подтверждённая ошибка КД; возможны отводки или повтор экспорта.',
            'Сверьте структурированный экспорт и назначение контактов инженером'))
    return out


def verify_coverage(model: EngineeringModel) -> list[dict]:
    """EM-109-2: devices mentioned in Э3 but absent from ПЭ3 rows and inverse."""
    if not model.scheme_mentions or not model.spec_rows:
        return []
    scheme_devices = {m.designation.normalized for m in model.scheme_mentions}
    spec_devices = {d.normalized for r in model.spec_rows for d in r.designations}
    out = []
    only_scheme = sorted(scheme_devices - spec_devices)[:MAX_EXAMPLES]
    if only_scheme:
        places = [f'{n} ({_first_ref(model, n).describe()})' for n in only_scheme]
        ref = _first_ref(model, only_scheme[0])
        out.append(issue('info', CODE_COVERAGE, ref.document, ref.describe(),
            f'{len(scheme_devices - spec_devices)} обозначений распознано в Э3, но не подтверждено строками ПЭ3 '
            f'(примеры: {"; ".join(places)}). Это не считается ошибкой КД.',
            'Для проверки полноты сопоставьте исходные Э3 и ПЭ3 или структурированный экспорт проекта'))
    only_spec = sorted(spec_devices - scheme_devices)[:MAX_EXAMPLES]
    if only_spec:
        places = [f'{n} ({_row_ref(model, n).describe()})' for n in only_spec]
        ref = _row_ref(model, only_spec[0])
        out.append(issue('info', CODE_COVERAGE, ref.document, ref.describe(),
            f'{len(spec_devices - scheme_devices)} обозначений извлечено из ПЭ3, но не подтверждено текстом Э3 '
            f'(примеры: {"; ".join(places)}). Это не считается ошибкой КД.',
            'Для проверки полноты сопоставьте исходные Э3 и ПЭ3 или структурированный экспорт проекта'))
    return out


def verify_contact_consistency(model: EngineeringModel) -> list[dict]:
    """EM-109-3: rows with unresolved fragments and quantity above confirmed count."""
    out = []
    for row in model.spec_rows:
        if not row.unknown or row.quantity is None or row.quantity <= row.confirmed_count:
            continue
        ref = row.provenance
        place = ref.describe() if ref else 'Перечень элементов'
        fragments = ', '.join(f'«{u}»' for u in row.unknown[:MAX_EXAMPLES])
        out.append(issue('info', CODE_CONTACT, ref.document if ref else '', place,
            f'В записи «{row.raw_text}» количество {row.quantity} больше числа подтверждённых обозначений '
            f'({row.confirmed_count}); нераспознанные фрагменты: {fragments}. Это не подтверждённая ошибка КД.',
            'Проверьте исходную строку перечня вручную: переносы, диапазоны и составные позиции'))
    return out


def _first_ref(model: EngineeringModel, normalized_name: str) -> SourceRef:
    for m in model.scheme_mentions:
        if m.designation.normalized == normalized_name:
            return m.provenance
    return SourceRef(document='')


def _row_ref(model: EngineeringModel, normalized_name: str) -> SourceRef:
    for r in model.spec_rows:
        if any(d.normalized == normalized_name for d in r.designations):
            return r.provenance or SourceRef(document='')
    return SourceRef(document='')


def run_universal_verifiers(model: EngineeringModel) -> list[dict]:
    return (verify_endpoint_conflicts(model)
            + verify_coverage(model)
            + verify_contact_consistency(model))
