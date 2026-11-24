"""Thin adapters: existing extractors -> EngineeringModel. All PDF/Excel I/O
stays in extractors/kd_crosscheck/full_set_completeness; this layer only maps
already-extracted data into the unified model.
"""
from __future__ import annotations

from full_set_completeness import classify_pdf_name
from engineering_model import (
    Designation, DocumentRef, EngineeringModel, SchemeMention, SourceRef, SpecRow,
    extract_attributes,
    run_universal_verifiers, run_verifiers,
)
from engineering_graph import connections_from_rows, run_graph_verifiers
from full_set_completeness import specification_references
from kd_crosscheck import designation_type, get_spec_rows, pair_key, scheme_designations
from confusable_designations import model_tokens
from naming import current_tag, expand_notation, normalized
import fitz
import re


def source_ref(document: str, page=None, row=None, bounds=None) -> SourceRef:
    return SourceRef(document=document, page=page, row=row, bounds=bounds)


def _em000(stage: str, filename: str, exc: BaseException) -> dict:
    """Structured adapter diagnostic: never silently swallow a failure."""
    from engine import issue as _issue
    detail = f'{type(exc).__name__}'
    return _issue(
        'error', 'KD-EM-000', filename or '(документ)', stage,
        f'Этап инженерного анализа «{stage}» завершился с ошибкой ({detail}); '
        'замечания этого документа по правилам KD-EM могут быть неполными. '
        'Это диагностика инструмента, а не ошибка КД.',
        'Повторите проверку после устранения сбоя; при повторении сохраните файл '
        'и передайте его разработчикам инструмента.',
    )


# --- KD-PE-113 universal PE3 row semantic classifier --------------------------
#
# Multi-factor classification: table geometry (row band), notation structure
# (expanded entries, ranges, unknown fragments), adjacent-row parent context
# (indented sub-row / empty designator cell after a parent device row), and
# item description/units. No factor alone decides; a blank designator cell
# without description cues and parent adjacency is never enough.


def _shared_parent(des: list, candidates: list[str]) -> str | None:
    """A parent is a preceding row designator sharing the alpha stem."""
    if not des:
        return None
    from naming import normalized as _norm
    stems = {''.join(c for c in d.normalized if c.isalpha()) for d in des}
    for cand in reversed(candidates):
        stem = ''.join(c for c in _norm(cand) if c.isalpha())
        if stem and any(s and (s.startswith(stem) or stem.startswith(s)) for s in stems):
            return _norm(cand)
    return None


def _classify_spec_row(r: dict, prev_designations: list[str], prev_has_band: bool) -> dict:
    """Return the KD-PE-113 semantic descriptor fields for one raw PE3 row."""
    entries = set(r.get('entries', set()) or set())
    unknown = list(r.get('unknown', []) or [])
    qty = r.get('quantity')
    band = r.get('row_band') is not None
    name = str(r.get('name') or '')
    indented = bool(r.get('designator_indented'))
    has_designator = bool(entries) or bool(str(r.get('source') or '').strip())

    has_length = bool(SpecRow('', None, [], name=name).has_length_or_bulk_units)
    has_acc_cues = bool(SpecRow('', None, [], name=name).has_accessory_cues)

    des_objs = [Designation.classify(d) for d in sorted(entries)]
    parent_ref = _shared_parent(des_objs, prev_designations)
    if parent_ref is None and not des_objs and prev_designations and prev_has_band:
        # A designator-less child row belongs to the immediately preceding
        # confirmed parent device row.
        from naming import normalized as _norm
        parent_ref = _norm(prev_designations[-1])

    if unknown and not band:
        role, sem, conf = 'ambiguous', 'device_count', 'ambiguous'
    elif not des_objs and prev_has_band and (has_acc_cues or indented):
        # Child accessory/sub-part row: empty designator cell (or indented
        # sub-row) immediately after a grid-confirmed parent device row, with
        # part/kit description cues. Blank cell alone is never sufficient.
        role, sem, conf = 'accessory', 'accessory_qty', 'confirmed'
    elif has_length and qty is not None and not band:
        role, sem, conf = 'assembly_element', 'length_or_bulk', 'ambiguous'
    elif parent_ref and indented:
        role, sem, conf = 'assembly_element', 'assembly_units', 'confirmed'
    elif band and des_objs and parent_ref and indented:
        # Indented sub-row with its own designator under a parent device:
        # assembly units, not standalone devices.
        role, sem, conf = 'assembly_element', 'assembly_units', 'confirmed'
    elif len(entries) > 1:
        role = 'grouped_position'
        sem = 'grouped_instances'
        conf = 'confirmed' if band and not unknown else 'ambiguous'
    elif band and not has_length and not has_acc_cues:
        # Standalone device row confirmed by grid geometry. qty > 1 stays a
        # genuine device-count mismatch (KD-EM-108-1 must still fire).
        role, sem = 'standalone_device', 'device_count'
        conf = 'confirmed' if not unknown else 'ambiguous'
    elif has_length:
        role, sem, conf = 'assembly_element', 'length_or_bulk', 'ambiguous'
    else:
        role, sem, conf = 'ambiguous', 'device_count', 'ambiguous'
    return {'row_role': role, 'parent_ref': parent_ref,
            'quantity_semantics': sem, 'classification_confidence': conf}


def model_from_spec_rows(rows, filename: str, model: EngineeringModel | None = None) -> EngineeringModel:
    """Map kd_crosscheck.get_spec_rows() output into SpecRow entities."""
    model = model or EngineeringModel()
    prev_designations: list[str] = []
    prev_has_band = False
    for r in rows:
        ref = source_ref(filename, page=r.get('page'), row=r.get('table_row'), bounds=r.get('row_band'))
        des = [Designation.classify(d) for d in sorted(r.get('entries', set()))]
        name = str(r.get('name') or '')
        semantic = _classify_spec_row(r, prev_designations, prev_has_band)
        row = SpecRow(raw_text=r.get('source', ''), quantity=r.get('quantity'),
                      designations=des, unknown=list(r.get('unknown', [])),
                      provenance=ref, name=name,
                      attributes=extract_attributes(name), **semantic)
        model.spec_rows.append(row)
        for d in des:
            model.add_device(d, ref)
        for token in model_tokens(name):
            model.pe3_model_tokens.setdefault(token, set()).add(ref)
        prev_designations = [d.raw for d in des] or prev_designations
        prev_has_band = r.get('row_band') is not None
    return model


def model_from_scheme(pdf_bytes: bytes, filename: str, model: EngineeringModel | None = None) -> EngineeringModel:
    """Map Э3 scheme designator mentions into the model."""
    model = model or EngineeringModel()
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        for pi, page in enumerate(pdf):
            ref = source_ref(filename, page=pi + 1)
            for raw in scheme_designations(page):
                des = Designation.classify(raw)
                model.scheme_mentions.append(SchemeMention(designation=des, provenance=ref))
                model.add_device(des, ref)
            for token in model_tokens(page.get_text()):
                model.e3_model_tokens.setdefault(token, set()).add(ref)
    return model


def model_from_sp_references(pdf_bytes: bytes, filename: str, model: EngineeringModel | None = None) -> EngineeringModel:
    """Map explicit СП table references into DocumentRef links."""
    model = model or EngineeringModel()
    for page, row, text, reference in specification_references(pdf_bytes):
        model.document_refs.append(DocumentRef(kind=reference.kind, key=reference.key,
                                               raw=text, provenance=source_ref(filename, page=page, row=row)))
    return model


def attach_upload_identities(names, model: EngineeringModel) -> EngineeringModel:
    """Record (kind, key) of uploaded PDFs for DocumentRef presence checks."""
    for name in names:
        identity = classify_pdf_name(name)
        if identity is not None:
            model.uploaded_identities.add((identity.kind, identity.key))
    return model


def attach_connections(records, model: EngineeringModel) -> EngineeringModel:
    """Bridge validated structured connection records (connection_checks)."""
    model.connections.extend(records)
    return model


_HEADER_ALIASES = {
    'designation': (r'^(?:поз(?:иция)?|обозначение|designat(?:ion|or)|reference)$',),
    'quantity': (r'^(?:кол(?:ичество)?|qty|quantity|count)$',),
    'name': (r'^(?:наименование|описание|name|description)$',),
    'document_ref': (r'^(?:документ|обозначение документа|document|document reference)$',),
    'source_device': (r'^(?:устройство источника|начальное устройство|source device|from device)$',),
    'source_terminal': (r'^(?:контакт источника|начальный контакт|source terminal|from terminal)$',),
    'target_device': (r'^(?:устройство назначения|конечное устройство|target device|to device)$',),
    'target_terminal': (r'^(?:контакт назначения|конечный контакт|target terminal|to terminal)$',),
    'wire': (r'^(?:провод|маркировка провода|wire|wire mark)$',),
    'cable': (r'^(?:кабель|cable)$',),
    'project': (r'^(?:проект|project)$',),
    'revision': (r'^(?:ревизия|версия|revision)$',),
}


def _header_key(value):
    text = re.sub(r'\s+', ' ', str(value or '').replace('\n', ' ')).strip().casefold()
    for key, patterns in _HEADER_ALIASES.items():
        if any(re.fullmatch(pattern, text, re.I) for pattern in patterns):
            return key
    return None


def _sheet_records(sheet):
    values = list(sheet.iter_rows(values_only=True))
    for header_index, row in enumerate(values[:20]):
        columns = {index: _header_key(value) for index, value in enumerate(row)}
        columns = {index: key for index, key in columns.items() if key}
        if len(columns) < 2 and set(columns.values()) != {'document_ref'}:
            continue
        records = []
        for source in values[header_index + 1:]:
            record = {key: source[index] if index < len(source) else None
                      for index, key in columns.items()}
            if any(value not in (None, '') for value in record.values()):
                records.append(record)
        return header_index + 1, records
    return None, []


def model_from_xlsx(content: bytes, filename: str,
                    model: EngineeringModel | None = None) -> EngineeringModel:
    """Map header-driven XLSX tables into model entities without filename rules."""
    from io import BytesIO
    from openpyxl import load_workbook

    model = model or EngineeringModel()
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            header_row, records = _sheet_records(sheet)
            if not records:
                continue
            keys = set().union(*(record.keys() for record in records))
            connection_keys = {'source_device', 'source_terminal', 'target_device',
                               'target_terminal', 'wire'}
            if connection_keys <= keys:
                model.connections.extend(connections_from_rows(
                    records, document=filename, sheet=sheet.title))
                continue
            if {'designation', 'quantity'} <= keys:
                rows = []
                for offset, record in enumerate(records, (header_row or 1) + 1):
                    raw = re.sub(r'\s+', ' ', str(record.get('designation') or '')).strip()
                    entries, unknown = expand_notation(raw)
                    try:
                        quantity = int(record.get('quantity'))
                    except (TypeError, ValueError):
                        quantity = None
                    rows.append({
                        'source': raw, 'entries': entries, 'unknown': unknown,
                        'quantity': quantity, 'name': re.sub(r'\s+', ' ', str(record.get('name') or '')).strip(),
                        'page': None, 'table_row': offset, 'row_band': (float(offset), float(offset + 1)),
                    })
                model_from_spec_rows(rows, filename, model)
                continue
            if 'document_ref' in keys:
                for offset, record in enumerate(records, (header_row or 1) + 1):
                    raw = re.sub(r'\s+', ' ', str(record.get('document_ref') or '')).strip()
                    kind = designation_type(raw) or designation_type(raw + '.pdf')
                    if kind:
                        model.document_refs.append(DocumentRef(
                            kind=kind, key=pair_key(raw), raw=raw,
                            provenance=source_ref(filename, row=offset)))
    finally:
        workbook.close()
    return model


def build_model(pdf_sources, names=None) -> EngineeringModel:
    """Convenience pipeline for one upload batch.

    pdf_sources: list of (filename, pdf_bytes); names defaults to the source
    names. Only PDF kinds already recognized by existing extractors are used.
    Adapter failures are surfaced as KD-EM-000 diagnostics, never swallowed.
    """
    model = EngineeringModel()
    diagnostics = []
    names = names if names is not None else [n for n, _ in pdf_sources]
    attach_upload_identities(names, model)
    parsed_counts = {}
    for name, content in pdf_sources:
        if _Path(name).suffix.casefold() == '.xlsx':
            before = (len(model.spec_rows), len(model.document_refs), len(model.connections))
            try:
                model_from_xlsx(content, name, model)
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(_em000('извлечение XLSX', name, exc))
            parsed_counts[name] = sum((len(model.spec_rows) - before[0],
                                       len(model.document_refs) - before[1],
                                       len(model.connections) - before[2]))
            continue
        identity = classify_pdf_name(name)
        if identity is None:
            continue
        try:
            if identity.kind == 'ПЭ3':
                before = len(model.spec_rows)
                model_from_spec_rows(get_spec_rows(content), name, model)
                parsed_counts[name] = len(model.spec_rows) - before
            elif identity.kind == 'Э3':
                before = len(model.scheme_mentions)
                model_from_scheme(content, name, model)
                parsed_counts[name] = len(model.scheme_mentions) - before
            elif identity.kind == 'СП':
                before = len(model.document_refs)
                model_from_sp_references(content, name, model)
                parsed_counts[name] = len(model.document_refs) - before
        except Exception as exc:  # noqa: BLE001 - must surface, never swallow
            diagnostics.append(_em000(f'извлечение {identity.kind}', name, exc))
            parsed_counts[name] = 0
    # Extraction-gap diagnostics: a recognized engineering document that
    # produced zero model entities means the analysis is incomplete.
    for name, count in parsed_counts.items():
        if count == 0:
            from engine import issue as _issue
            diagnostics.append(_issue(
                'info', 'KD-EM-000', name, 'Извлечение',
                'Документ распознан, но из него не извлечено ни одной сущности '
                '(строк, обозначений или ссылок); замечания KD-EM по этому '
                'документу неполные. Это ограничение инструмента, а не ошибка КД.',
                ('Проверьте наличие текстового слоя или выполните OCR.'
                 if _Path(name).suffix.casefold() == '.pdf' and not _pdf_has_text(dict(pdf_sources).get(name, b''))
                 else 'Текстовый слой доступен. Проверьте заголовки столбцов, границы строк, '
                      'ячейки обозначений и якоря количества; при необходимости используйте '
                      'структурированную выгрузку.'),
            ))
    model.adapter_diagnostics = diagnostics
    return model


def _pdf_has_text(content: bytes) -> bool:
    try:
        with fitz.open(stream=content, filetype='pdf') as pdf:
            return any(page.get_text().strip() for page in pdf)
    except Exception:
        return False


# --- KD-PE-109 pipeline seam ---------------------------------------------------

# Legacy codes only suppressed under explicit opt-in (engineering_model_replace_legacy).
LEGACY_CODES = frozenset({'KD-PE-102', 'KD-SET-203'})


import json
from pathlib import Path as _Path

FLAGS_PATH = _Path(__file__).resolve().parent / 'engineering_flags.json'
_FLAG_KEYS = ('engineering_model_checks', 'engineering_model_replace_legacy')
_FLAG_DEFAULTS = {'engineering_model_checks': True, 'engineering_model_replace_legacy': False}


def engineering_flags(cfg=None) -> dict:
    """Resolve EM flags: defaults < engineering_flags.json < explicit cfg keys.

    The persisted configuration schema is a frozen provenance contract and
    must not gain keys, so these flags live in a separate file.
    """
    flags = dict(_FLAG_DEFAULTS)
    try:
        flags.update({k: v for k, v in json.loads(FLAGS_PATH.read_text(encoding='utf-8')).items()
                      if k in _FLAG_KEYS and isinstance(v, bool)})
    except (OSError, ValueError):
        pass
    if cfg:
        flags.update({k: v for k, v in cfg.items() if k in _FLAG_KEYS and isinstance(v, bool)})
    return flags


def _name_content_pairs(pdf_sources) -> list:
    """Accept both (name, kind, pair_key, bytes) and (name, bytes) sources."""
    pairs = []
    for item in pdf_sources:
        pairs.append((item[0], item[-1]))
    return pairs


def engineering_findings(pdf_sources, cfg=None) -> list:
    """Build the model from uploaded PDFs and run EM-108/EM-109/EM-110 verifiers.

    pdf_sources: (name, kind, pair_key, bytes) or (name, bytes). ALL uploaded
    PDFs must be passed — including СП and ВО — otherwise document-reference
    checks have no model to verify against.
    Never raises: any failure yields a KD-EM-000 diagnostic.
    """
    if not engineering_flags(cfg)['engineering_model_checks']:
        return []
    try:
        findings, _summary = engineering_analysis(pdf_sources, cfg)
        return findings
    except Exception as exc:  # noqa: BLE001 - the seam must fail loudly, not silently
        return [_em000('построение инженерной модели', '', exc)]


_CANONICAL_RULE_GROUPS = {
    'KD-PE-102': 'quantity_mismatch',
    'KD-EM-108-1': 'quantity_mismatch',
    'KD-PE-101': 'inventory_coverage',
    'KD-PE-105': 'inventory_coverage',
    'KD-EM-109-2': 'inventory_coverage',
}
_SEVERITY = {'info': 0, 'warning': 1, 'error': 2}


def _finding_designator(finding):
    text = ' '.join(str(finding.get(key) or '') for key in ('message', 'location'))
    values = sorted({normalized(match.group(0)) for match in current_tag().finditer(text)})
    return ';'.join(values)


def _finding_source_pair(finding):
    details = finding.get('details') if isinstance(finding.get('details'), dict) else {}
    names = [details.get('e3_document'), details.get('pe3_document')]
    if not any(names):
        names = re.split(r'\s*/\s*', str(finding.get('file') or ''))
    return tuple(sorted({pair_key(str(name)) for name in names if name}))


def deduplicate_findings(findings):
    """Collapse legacy/model overlap while retaining strongest evidence."""
    output, positions = [], {}
    for finding in findings:
        code = str(finding.get('code') or '')
        group = _CANONICAL_RULE_GROUPS.get(code, code)
        designator = _finding_designator(finding)
        # Empty designators cannot prove semantic overlap across rule codes.
        if not designator:
            group = code
        key = (group, designator, _finding_source_pair(finding))
        if key not in positions:
            positions[key] = len(output)
            output.append(finding)
            continue
        index = positions[key]
        current = output[index]
        if _SEVERITY.get(finding.get('severity'), -1) > _SEVERITY.get(current.get('severity'), -1):
            output[index] = finding
        elif finding.get('evidence') and not current.get('evidence'):
            merged = dict(current)
            merged['evidence'] = finding['evidence']
            output[index] = merged
    return output


def _model_summary(model, sources):
    by_source = {}
    for name, _content in sources:
        by_source[name] = {'scheme_mentions': 0, 'spec_rows': 0,
                           'document_refs': 0, 'connections': 0}
    for mention in model.scheme_mentions:
        by_source.setdefault(mention.provenance.document, {}).setdefault('scheme_mentions', 0)
        by_source[mention.provenance.document]['scheme_mentions'] += 1
    for row in model.spec_rows:
        if row.provenance:
            by_source.setdefault(row.provenance.document, {}).setdefault('spec_rows', 0)
            by_source[row.provenance.document]['spec_rows'] += 1
    for ref in model.document_refs:
        if ref.provenance:
            by_source.setdefault(ref.provenance.document, {}).setdefault('document_refs', 0)
            by_source[ref.provenance.document]['document_refs'] += 1
    for record in model.connections:
        by_source.setdefault(record.document, {}).setdefault('connections', 0)
        by_source[record.document]['connections'] += 1
    xlsx_uploaded = any(_Path(name).suffix.casefold() == '.xlsx' for name, _ in sources)
    return {
        'scheme_mentions': len(model.scheme_mentions),
        'spec_rows': len(model.spec_rows),
        'document_refs': len(model.document_refs),
        'connections': len(model.connections),
        'by_source': by_source,
        'metadata': {
            'xlsx_uploaded': xlsx_uploaded,
            'scope_note': (None if xlsx_uploaded else
                           'Файлы XLSX не загружены: глубина проверки маркировки проводов ограничена данными PDF.'),
        },
    }


def engineering_analysis(sources, cfg=None):
    pairs = _name_content_pairs(sources)
    if not engineering_flags(cfg)['engineering_model_checks']:
        empty = EngineeringModel()
        return [], _model_summary(empty, pairs)
    model = build_model(pairs)
    findings = (list(getattr(model, 'adapter_diagnostics', []))
                + list(run_verifiers(model)) + list(run_universal_verifiers(model))
                + list(run_graph_verifiers(model)))
    return findings, _model_summary(model, pairs)


def apply_engineering_findings(issues, pdf_sources, cfg, *, return_summary=False):
    """Append KD-EM-* findings to an existing issue list (pure function).

    Strictly additive by default. Only when the explicit opt-in
    cfg['engineering_model_replace_legacy'] is true are the legacy codes
    KD-PE-102 / KD-SET-203 removed from the existing list first.
    """
    try:
        findings, summary = engineering_analysis(pdf_sources, cfg)
    except Exception as exc:  # noqa: BLE001
        findings = [_em000('построение инженерной модели', '', exc)]
        summary = _model_summary(EngineeringModel(), _name_content_pairs(pdf_sources))
    if engineering_flags(cfg)['engineering_model_replace_legacy']:
        issues = [i for i in issues if i.get('code') not in LEGACY_CODES]
    combined = deduplicate_findings(list(issues) + findings)
    return (combined, summary) if return_summary else combined
