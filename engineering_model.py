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
