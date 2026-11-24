"""Unified deterministic engineering CAD model (KD-PE-108).

Pure data model + verifiers. No PDF/Excel I/O here (adapters.py feeds it) and
no AI/network. Designation-agnostic: reference classes come from naming.py
configuration, never from per-class special cases.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from connection_checks import Connection
from engine import issue
from naming import CYR_LOOKALIKES


# --- Universal product-attribute signatures (KD-PE-112) ----------------------
#
# Attributes are extracted from already-extracted text; no rule is tied to a
# concrete device code, file name or sheet number.

VOLTAGE_RE = re.compile(
    r"(?<![\w.])(\d{1,4})\s*(V\s*DC|V\s*AC|VDC|VAC|V|В)\b",
    re.I,
)
TYPE_CODE_RE = re.compile(
    r"(?<![\w])([A-ZА-Я]{2,}[A-ZА-Я0-9]*(?:[-/][A-ZА-Я0-9]+)+)(?![\w])",
    re.I,
)


def extract_attributes(text: str) -> dict:
    """Return universal electrical/variant signatures found in a product name.

    ``voltages`` — normalized nominal voltage signatures (``24VDC``);
    ``type_tokens`` — multi-part type/model codes containing a digit.
    """
    clean = re.sub(r"\s+", " ", str(text or ""))
    voltages = set()
    for number, suffix in VOLTAGE_RE.findall(clean):
        normalized_suffix = re.sub(r"\s+", "", suffix).upper().replace("В", "V")
        if normalized_suffix in ("V", "VDC", "VAC"):
            voltages.add(f"{int(number)}{normalized_suffix}")
    type_tokens = set()
    for token in TYPE_CODE_RE.findall(clean):
        value = token.upper()
        if any(ch.isdigit() for ch in value):
            type_tokens.add(value)
    return {"voltages": frozenset(voltages), "type_tokens": frozenset(type_tokens)}


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

# --- PE3 row semantic descriptor (KD-PE-113) ---------------------------------
#
# Universal classification: table geometry (row band), notation structure
# (entries/ranges/unknown fragments), adjacent-row parent context, and
# item description/units are combined. No rule is keyed on a concrete
# device code, filename or sheet number.

ROW_ROLES = ('standalone_device', 'accessory', 'assembly_element',
             'grouped_position', 'ambiguous')
QUANTITY_SEMANTICS = ('device_count', 'accessory_qty', 'assembly_units',
                      'grouped_instances', 'length_or_bulk')

_LENGTH_UNITS_RE = re.compile(
    r'(?<![\w])(?:м|мм|мм²|м2|см|км|кг|г|м2/м|пог\.?\s*м|м\.)(?![\w])', re.I)
# Part/accessory cues in a product name (assembled sets, mounting parts).
_ACCESSORY_CUES_RE = re.compile(
    r'(?:комплект|набор|принадлежн|аксессуар|детал|креплен|зажим|планка|'
    r'перемычк|адаптер|гребенк|держател|уплотн|винт|гаек|шайб)', re.I)


@dataclass
class SpecRow:
    raw_text: str
    quantity: int | None
    designations: list[Designation]      # expanded entries
    unknown: list[str] = field(default_factory=list)
    provenance: SourceRef | None = None
    name: str = ""
    attributes: dict = field(default_factory=dict)  # extract_attributes() output
    # KD-PE-113 semantic descriptor (set by adapters._classify_spec_row).
    # Defaults describe a plain device row constructed without table context;
    # the adapter refines them from grid geometry, notation structure,
    # parent context and description cues.
    row_role: str = 'standalone_device'
    parent_ref: str | None = None
    quantity_semantics: str = 'device_count'
    classification_confidence: str = 'ambiguous'

    @property
    def confirmed_count(self) -> int:
        return len(self.designations)

    @property
    def has_length_or_bulk_units(self) -> bool:
        return bool(_LENGTH_UNITS_RE.search(self.name or ''))

    @property
    def has_accessory_cues(self) -> bool:
        return bool(_ACCESSORY_CUES_RE.search(self.name or ''))


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
    # Model-like tokens (e.g. SO1.PLC.01) observed in Э3 text and ПЭ3 names,
    # mapped to every place they were seen. Universal confusable check input.
    e3_model_tokens: dict = field(default_factory=dict)   # token -> set[SourceRef]
    pe3_model_tokens: dict = field(default_factory=dict)  # token -> set[SourceRef]

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

# KD-PE-113: rows whose quantity is not a per-designation device count must
# not produce KD-EM-108-1 quantity-mismatch findings. They are surfaced via
# KD-EM-109-3 (unresolved fragments) instead, or accepted as bulk rows.
_QTY_CHECK_ROLES = frozenset({'standalone_device', 'grouped_position'})
_QTY_CHECK_SEMANTICS = frozenset({'device_count', 'grouped_instances'})


def verify_row_quantities(model: EngineeringModel) -> list[dict]:
    """EM-108-1: PE3 row quantity differs from expanded designation count.

    Only rows confirmed to count whole devices are checked. Accessory,
    assembly-element, bulk/length and ambiguous rows carry a different
    quantity semantics; their apparent mismatches are not device-count
    errors (see KD-EM-109-3 for unresolved fragments).
    """
    out = []
    for row in model.spec_rows:
        if row.quantity is None or row.quantity == row.confirmed_count:
            continue
        if (row.row_role not in _QTY_CHECK_ROLES
                or row.quantity_semantics not in _QTY_CHECK_SEMANTICS):
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
            f'Смешение похожих латинских и кириллических букв в обозначении «{norm}": {", ".join(forms)}. Места исходных написаний: {"; ".join(places)}.',
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
    # KD-PE-113: accessory/sub-part and bulk/length rows list materials under
    # a parent device, not scheme devices of their own. Their designations
    # must not create "present in ПЭ3, absent in Э3" findings.
    spec_devices = {d.normalized for r in model.spec_rows
                    for d in r.designations
                    if r.row_role not in ('accessory', 'assembly_element')
                    and r.quantity_semantics != 'length_or_bulk'}
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
        # KD-PE-113: a confirmed device-count row with unknown fragments is a
        # genuine under-specification; accessory/assembly/ambiguous rows are
        # reported only when their role is unconfirmed.
        if (row.classification_confidence == 'confirmed'
                and row.quantity_semantics in _QTY_CHECK_SEMANTICS):
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
            + verify_contact_consistency(model)
            + verify_designation_attribute_conflicts(model)
            + verify_confusable_model_tokens(model))


# --- KD-PE-112 universal real-PDF bridge rules --------------------------------

CODE_PARAM_CONFLICT = 'KD-EM-112-1'
CODE_CONFUSABLE_MODEL = 'KD-EM-112-2'
MAX_PARAM_EXAMPLES = 8


def verify_designation_attribute_conflicts(model: EngineeringModel) -> list[dict]:
    """EM-112-1: same designator in ПЭ3 with incompatible product attributes.

    A repeated designation is not an error by itself. A warning is emitted
    only when two rows with the same normalized designation carry explicit,
    non-intersecting electrical parameter signatures (nominal voltage or
    product type token). Rows without explicit attributes never conflict.
    """
    occurrences: dict = {}
    for row in model.spec_rows:
        ref = row.provenance
        if ref is None:
            continue
        for designation in row.designations:
            occurrences.setdefault(designation.normalized, []).append((row, ref))
    out = []
    for designation, places in sorted(occurrences.items()):
        if len(places) < 2 or len(out) >= MAX_PARAM_EXAMPLES:
            continue
        conflicts = []
        for index, (left_row, left_ref) in enumerate(places):
            for right_row, right_ref in places[index + 1:]:
                left_attr = left_row.attributes or extract_attributes(left_row.name)
                right_attr = right_row.attributes or extract_attributes(right_row.name)
                left_v, right_v = set(left_attr.get('voltages', ())), set(right_attr.get('voltages', ()))
                if left_v and right_v and not (left_v & right_v):
                    conflicts.append((left_ref, right_ref, 'напряжение', sorted(left_v | right_v)))
                    continue
                left_t, right_t = set(left_attr.get('type_tokens', ())), set(right_attr.get('type_tokens', ()))
                if left_t and right_t and not (left_t & right_t):
                    conflicts.append((left_ref, right_ref, 'тип изделия', sorted(left_t | right_t)[:6]))
        if not conflicts:
            continue
        left_ref, right_ref, kind, values = conflicts[0]
        out.append(issue(
            'warning', CODE_PARAM_CONFLICT, right_ref.document,
            f'{left_ref.describe()}; {right_ref.describe()}',
            f'Для проверки инженером: обозначение «{designation}» встречается в нескольких строках ПЭ3 '
            f'с несовместимыми данными изделия. Атрибут «{kind}» различается: {", ".join(values)}. '
            'Это не подтверждённая ошибка КД; автоматический выбор правильного варианта не выполняется.',
            'Сопоставьте обе строки с Э3 и документацией изготовителя. Решение принимает инженер; '
            'исходные строки документа не изменяются.',
        ))
    return out


def verify_confusable_model_tokens(model: EngineeringModel) -> list[dict]:
    """EM-112-2: visually confusable model tokens across Э3 and ПЭ3.

    Universal: any pair of model-like tokens of equal structure differing in
    exactly one known confusable character (O/0, I/1, S/5, B/8, ...). Purely
    informational; no automatic replacement is performed.
    """
    from confusable_designations import find_candidates

    if not model.e3_model_tokens or not model.pe3_model_tokens:
        return []
    out = []
    for left, right, label in find_candidates(model.e3_model_tokens, model.pe3_model_tokens):
        e3_ref = sorted(model.e3_model_tokens[left], key=lambda r: (r.document, r.page or 0))[0]
        pe3_ref = sorted(model.pe3_model_tokens[right], key=lambda r: (r.document, r.page or 0))[0]
        out.append(issue(
            'info', CODE_CONFUSABLE_MODEL, pe3_ref.document,
            f'Э3: {e3_ref.describe()}; ПЭ3: {pe3_ref.describe()}',
            f'Возможное несоответствие обозначения исполнения: в Э3 «{left}», в ПЭ3 «{right}». '
            f'Отличие: {label} (визуально похожие символы). Это кандидат на опечатку, а не '
            'подтверждённая ошибка; символы не заменяются автоматически.',
            'Сверьте обозначение исполнения с исходным проектом и документацией изготовителя. '
            'Решение принимает инженер.',
        ))
    return out
