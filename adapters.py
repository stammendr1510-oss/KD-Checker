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
from connection_import import RoutedWorkbook, route_xlsx, classify_connection_row
from full_set_completeness import specification_references
from kd_crosscheck import (designation_type, expand_notation, get_spec_rows,
                           pair_key, scheme_designations)
from confusable_designations import model_tokens
from naming import normalized, universal_reference_tag, letter_designator
import fitz
import re
from itertools import islice


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
    elif has_acc_cues:
        role, sem, conf = 'accessory', 'accessory_qty', 'confirmed' if band else 'ambiguous'
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
        for pi, page in enumerate(islice(pdf, 100)):
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


def model_from_xlsx(content: bytes, filename: str,
                    model: EngineeringModel | None = None,
                    routed: RoutedWorkbook | None = None) -> EngineeringModel:
    """Map header-driven XLSX tables into model entities without filename rules."""
    model = model or EngineeringModel()
    routed = routed or route_xlsx(content)
    for sheet in routed.sheets:
        records = sheet.records
        if not records:
            continue
        if sheet.role == 'connections':
            model.connections.extend(connections_from_rows(
                records, document=filename, sheet=sheet.name))
            continue
        if sheet.role == 'pe3':
            rows = []
            for record in records:
                raw = str(record.get('designation') or '').strip()
                entries, unknown = expand_notation(raw)
                try:
                    value = float(record.get('quantity'))
                    quantity = int(value) if value >= 0 and value.is_integer() else None
                except (TypeError, ValueError, OverflowError):
                    quantity = None
                rows.append({
                    'source': raw, 'entries': entries, 'unknown': unknown,
                    'quantity': quantity, 'name': re.sub(r'\s+', ' ', str(record.get('name') or '')).strip(),
                    'page': None, 'table_row': record['_row_number'],
                    'row_band': (float(record['_row_number']), float(record['_row_number'] + 1)),
                })
            model_from_spec_rows(rows, filename, model)
            continue
        if sheet.role == 'sp':
            for record in records:
                raw = re.sub(r'\s+', ' ', str(record.get('document_ref')
                                                or record.get('designation') or '')).strip()
                kind = designation_type(raw) or designation_type(raw + '.pdf')
                if kind:
                    model.document_refs.append(DocumentRef(
                        kind=kind, key=pair_key(raw), raw=raw,
                        provenance=source_ref(filename, row=record['_row_number'])))
    return model


def build_model(pdf_sources, names=None) -> EngineeringModel:
    """Convenience pipeline for one upload batch.

    pdf_sources: list of (filename, pdf_bytes); names defaults to the source
    names. Only PDF kinds already recognized by existing extractors are used.
    Adapter failures are surfaced as KD-EM-000 diagnostics, never swallowed.
    """
    model = EngineeringModel()
    diagnostics = []
    sources = [_source_parts(item) for item in pdf_sources]
    source_content = {name: content for name, content, _routed in sources}
    names = names if names is not None else [name for name, _content, _routed in sources]
    attach_upload_identities(names, model)
    parsed_counts = {}
    for name, content, routed in sources:
        if _Path(name).suffix.casefold() == '.xlsx':
            before = (len(model.spec_rows), len(model.document_refs), len(model.connections))
            try:
                model_from_xlsx(content, name, model, routed=routed)
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
    # Letter-only labels need corroboration from a designation column; uppercase
    # prose in a drawing alone is insufficient engineering identity evidence.
    letters = {d.raw for row in model.spec_rows for d in row.designations if letter_designator(d.raw)}
    if letters:
        for name, content, _routed in sources:
            identity = classify_pdf_name(name)
            if identity and identity.kind == 'Э3':
                with fitz.open(stream=content, filetype='pdf') as pdf:
                    for pi, page in enumerate(islice(pdf, 100)):
                        existing = {m.designation.raw for m in model.scheme_mentions
                                    if m.provenance.document == name and m.provenance.page == pi + 1}
                        for raw in (set(scheme_designations(page, letter_candidates=letters)) & letters) - existing:
                            ref = source_ref(name, page=pi + 1)
                            des = Designation.classify(raw)
                            model.scheme_mentions.append(SchemeMention(designation=des, provenance=ref))
                            model.add_device(des, ref)
                            parsed_counts[name] = parsed_counts.get(name, 0) + 1
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
                 if _Path(name).suffix.casefold() == '.pdf' and not _pdf_has_text(source_content.get(name, b''))
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


def _source_parts(item):
    """Return (name, bytes, optional pre-routed workbook)."""
    if len(item) >= 3 and isinstance(item[2], RoutedWorkbook):
        return item[0], item[1], item[2]
    return item[0], item[-1], None


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
    'KD-PE-101': 'scheme_pe3_mismatch',
    'KD-PE-105': 'scheme_pe3_mismatch',
    'KD-EM-109-2': 'scheme_pe3_mismatch',
    'KD-PE-106': 'parameter_conflict',
    'KD-EM-112-1': 'parameter_conflict',
    'KD-PE-107': 'confusable_model',
    'KD-EM-112-2': 'confusable_model',
    'KD-PE-103': 'confusable_designator',
    'KD-EM-108-3': 'confusable_designator',
    'KD-SET-203': 'missing_set_document',
    'KD-EM-108-2': 'missing_set_document',
    'KD-EM-109-1': 'terminal_conflict',
}
_SEVERITY = {'info': 0, 'warning': 1, 'error': 2, 'critical': 3}


def _entity_text(value):
    return normalized(str(value))


def _canonical_identity(finding):
    code = finding.get('code', '')
    group = _CANONICAL_RULE_GROUPS.get(code)
    message = str(finding.get('message') or '')
    details = finding.get('details') or {}
    if not isinstance(details, dict):
        details = {}
    # An ambiguous repeat is not an established parameter conflict.
    if code == 'KD-PE-106' and 'без явного' in message:
        return None
    if code == 'KD-PE-105' and (details.get('status') == 'not_evaluated'
                               or 'not_evaluated' in message):
        return None
    if not group:
        return None
    if finding.get('canonical_type') == group and finding.get('target_entity'):
        return group, _entity_text(finding['target_entity'])
    quotes = re.findall(r'«([^»]+)»', message)
    entity = ''
    if group == 'confusable_model':
        if len(quotes) >= 2:
            entity = '|'.join(sorted(_entity_text(q) for q in quotes[:2]))
    elif group in ('parameter_conflict', 'terminal_conflict', 'missing_set_document'):
        if quotes:
            entity = _entity_text(quotes[0])
    elif group == 'confusable_designator':
        if code == 'KD-EM-108-3' and quotes:
            entity = _entity_text(quotes[0])
        else:
            forms = message.split('Места исходных написаний:', 1)[0]
            values = {_entity_text(m.group(0)) for m in universal_reference_tag().finditer(forms)}
            if len(values) == 1:
                entity = values.pop()
    elif group == 'quantity_mismatch' and quotes:
        entries, unknown = expand_notation(quotes[0])
        if entries and not unknown:
            entity = ';'.join(sorted({_entity_text(v) for v in entries}))
    elif group == 'scheme_pe3_mismatch':
        values = {_entity_text(item['designation']) for item in details.get('missing', [])
                  if isinstance(item, dict) and item.get('designation')}
        if not values:
            examples = re.search(r'примеры:\s*(.+)', message)
            if examples:
                values = {_entity_text(m.group(0)) for m in
                          universal_reference_tag().finditer(examples.group(1).split(').')[0])}
        if values:
            direction = ('spec_to_scheme' if code == 'KD-PE-101' or 'извлечено из ПЭ3' in message
                         else 'scheme_to_spec')
            entity = direction + ':' + ';'.join(sorted(values))
    return (group, entity) if entity else None


def _finding_refs(finding):
    refs = list(finding.get('source_refs') or [])
    location = str(finding.get('location') or '')
    for part in filter(None, (p.strip() for p in location.split(';'))):
        page = re.search(r'лист(?:\s+PDF)?\s+(\d+)', part, re.I)
        row = re.search(r'строк[а-я]*(?:\s+таблицы)?\s+(\d+)', part, re.I)
        ref = {'document': finding.get('file'), 'page': int(page[1]) if page else None,
               'row': int(row[1]) if row else None, 'location': part}
        if ref not in refs:
            refs.append(ref)
    return refs


def _dedup_identity(finding):
    identity = _canonical_identity(finding)
    if identity:
        return identity
    # Older quantity diagnostics did not quote their designator.
    if _CANONICAL_RULE_GROUPS.get(finding.get('code')) == 'quantity_mismatch':
        message = str(finding.get('message') or '')
        if '«' not in message:
            values = {_entity_text(m.group(0)) for m in universal_reference_tag().finditer(message)}
            if len(values) == 1:
                return 'quantity_mismatch', values.pop()
    return None


def _shared_conflict(current, incoming):
    # Explicit evidence and concrete source references must agree. A common
    # entity or page alone does not establish two different messages as duplicates.
    left, right = _finding_refs(current), _finding_refs(incoming)
    shared = any(ref in right for ref in left)
    explicit_left = current.get('source_refs') or []
    explicit_right = incoming.get('source_refs') or []
    explicit_shared = any(ref in explicit_right for ref in explicit_left)
    evidence = current.get('evidence')
    same_evidence = bool(evidence) and evidence == incoming.get('evidence')
    same_message = current.get('message') == incoming.get('message')
    same_location = bool(current.get('file') and current.get('location')) and (
        current.get('file'), current.get('location')) == (
        incoming.get('file'), incoming.get('location'))
    if explicit_left and explicit_right and not explicit_shared:
        return False
    if current.get('code') == incoming.get('code') and not same_message:
        return False
    return (same_message and (shared or same_location)) or (
        (explicit_shared or same_evidence) and shared)


def deduplicate_findings(findings, *, preserve_legacy=None):
    """Collapse evidenced duplicates, retaining additive legacy diagnostics."""
    output, positions = [], {}
    for finding in findings:
        code = str(finding.get('code') or '')
        identity = _dedup_identity(finding)
        key = ('canonical', *identity) if identity else (
            'exact', code, finding.get('file'), finding.get('location'), finding.get('message'))
        incoming = dict(finding)
        if identity:
            incoming['canonical_type'], incoming['target_entity'] = identity
        index = None
        for candidate in positions.get(key, []):
            current = output[candidate]
            legacy_pair = (current.get('code') != code and
                           any(str(item.get('code') or '').startswith(('KD-SET-', 'KD-PE-'))
                               for item in (current, incoming)))
            # An already finalized additive result must remain idempotent
            # when passed to the public helper without configuration.
            finalized = all(item.get('canonical_type') for item in (current, finding))
            if legacy_pair and (preserve_legacy is True or
                                (preserve_legacy is None and finalized)):
                continue
            if current == incoming or _shared_conflict(current, incoming):
                index = candidate
                break
        if index is None:
            positions.setdefault(key, []).append(len(output))
            output.append(incoming)
            continue
        current = output[index]
        winner = (incoming if _SEVERITY.get(str(incoming.get('severity')).lower(), -1) >
                  _SEVERITY.get(str(current.get('severity')).lower(), -1) else current)
        merged = dict(winner)
        merged['related_codes'] = sorted(set(current.get('related_codes', [])) |
                                         set(incoming.get('related_codes', [])) |
                                         {current['code'], code})
        refs = []
        for item in (current, incoming):
            for ref in _finding_refs(item):
                if ref not in refs:
                    refs.append(ref)
        merged['source_refs'] = refs
        for field in ('evidence', 'details'):
            if field not in merged:
                for item in (current, incoming):
                    if field in item:
                        merged[field] = item[field]
        output[index] = merged
    return output


def _document_completeness(model, sources):
    reports = []
    for name, content, routed in sources:
        report = dict(document=name, detected_type='Не определён', parser='Не выбран',
                      raw_rows=None, extracted=0, incomplete=0, ambiguous=0, rejected=0,
                      reasons=[], applicable_checks=[], skipped_checks=[], confidence_limits=[])
        if _Path(name).suffix.casefold() == '.xlsx':
            routed = routed or route_xlsx(content)
            report.update(detected_type=', '.join(dict.fromkeys(
                {'connections': 'Соединения', 'pe3': 'ПЭ3', 'sp': 'СП'}.get(sh.role, 'Не определён')
                for sh in routed.sheets)), parser='openpyxl: заголовки и позиции столбцов', raw_rows=0)
            report['confidence_limits'].append('Формулы не вычисляются: используются сохранённые значения ячеек')
            for sheet in routed.sheets:
                report['confidence_limits'].extend(sheet.limits)
                report['raw_rows'] += len(sheet.records)
                for row in sheet.records:
                    if sheet.role == 'connections':
                        status, reason = classify_connection_row(row)
                    elif sheet.role == 'pe3':
                        entries, unknown = expand_notation(str(row.get('designation') or ''))
                        if unknown:
                            status, reason = 'ambiguous', 'Неоднозначные фрагменты обозначения: ' + '; '.join(unknown)
                        elif not entries or row.get('quantity') is None:
                            status, reason = 'incomplete', 'Нет обозначения или количества'
                        else:
                            try:
                                quantity = float(row['quantity'])
                                valid = quantity >= 0 and quantity.is_integer()
                            except (TypeError, ValueError, OverflowError):
                                valid = False
                            status, reason = ('extracted', '') if valid else ('rejected', 'Некорректное количество')
                    elif sheet.role == 'sp':
                        raw = str(row.get('document_ref') or row.get('designation') or '')
                        status, reason = (('extracted', '') if designation_type(raw) or designation_type(raw + '.pdf')
                                          else ('incomplete', 'Тип ссылки на документ не распознан'))
                    else:
                        status, reason = 'rejected', 'Заголовки таблицы не распознаны'
                    report[status] += 1
                    if status != 'extracted':
                        report['reasons'].append(dict(sheet=sheet.name, row=row['_row_number'], status=status, reason=reason))
            roles = {sh.role for sh in routed.sheets}
            for role, check in [('connections', 'Топология соединений'), ('pe3', 'Количество и обозначения'), ('sp', 'Ссылки на документы')]:
                report['applicable_checks' if role in roles else 'skipped_checks'].append(check)
            if len(routed.sheets) >= 30:
                report['confidence_limits'].append('Обработка ограничена 30 листами XLSX')
        else:
            identity = classify_pdf_name(name)
            report['detected_type'] = identity.kind if identity else 'PDF без распознанного типа'
            report['parser'] = 'PyMuPDF: текст и геометрия'
            rows = [r for r in model.spec_rows if r.provenance and r.provenance.document == name]
            refs = [r for r in model.document_refs if r.provenance and r.provenance.document == name]
            mentions = [r for r in model.scheme_mentions if r.provenance.document == name]
            report['extracted'] = len(rows) + len(refs) + len(mentions)
            for row in rows:
                if row.unknown or row.quantity is None:
                    report['extracted'] -= 1
                    report['ambiguous'] += 1
                    report['reasons'].append(dict(row=row.provenance.row, status='ambiguous',
                        reason='Неоднозначное обозначение или количество'))
            report['applicable_checks'] = ['Текстовые проверки']
            if identity and report['extracted']:
                report['applicable_checks'].append('Сопоставление извлечённых сущностей')
            else:
                report['skipped_checks'].append('Сопоставление: тип или сущности не распознаны')
            report['skipped_checks'].append('Топология без структурированных соединений')
            report['confidence_limits'] = ['Число исходных строк PDF достоверно не определено; OCR не выполняется',
                                           'Обработка Э3/ПЭ3 ограничена первыми 100 страницами']
            if not _pdf_has_text(content):
                report['confidence_limits'].append('Текстовый слой отсутствует')
        reports.append(report)
    return reports


def _model_summary(model, sources):
    by_source = {}
    normalized_sources = [_source_parts(item) for item in sources]
    for name, _content, _routed in normalized_sources:
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
    xlsx_uploaded = any(_Path(name).suffix.casefold() == '.xlsx'
                        for name, _content, _routed in normalized_sources)
    devices = []
    for key in sorted(model.devices)[:20]:
        device = model.devices[key]
        raw = getattr(getattr(device, 'designation', None), 'raw', None) or key
        devices.append({'designation': raw})
    spec_rows = []
    for row in model.spec_rows[:20]:
        spec_rows.append({
            'document': row.provenance.document if row.provenance else '',
            'row': row.provenance.row if row.provenance else None,
            'designation': row.raw_text,
            'quantity': row.quantity,
            'name': row.name,
        })
    connections = []
    for name, _content, routed in normalized_sources:
        if routed is None:
            continue
        for sheet in routed.sheets_for('connections'):
            for row in sheet.records:
                connections.append({
                    'document': name, 'sheet': sheet.name, 'row': row.get('_row_number'),
                    'source': f"{row.get('source_device') or ''}:{row.get('source_terminal') or ''}",
                    'target': f"{row.get('target_device') or ''}:{row.get('target_terminal') or ''}",
                    'wire': row.get('wire') or '', 'cable': row.get('cable') or '',
                    'color': row.get('color') or '',
                    'cross_section': row.get('cross_section') or '',
                })
                if len(connections) >= 20:
                    break
            if len(connections) >= 20:
                break
        if len(connections) >= 20:
            break
    return {
        'devices': len(model.devices),
        'scheme_mentions': len(model.scheme_mentions),
        'spec_rows': len(model.spec_rows),
        'document_refs': len(model.document_refs),
        'connections': len(model.connections),
        'preview': {'devices': devices, 'pe3_rows': spec_rows,
                    'connections': connections},
        'by_source': by_source,
        'documents': _document_completeness(model, normalized_sources),
        'metadata': {
            'xlsx_uploaded': xlsx_uploaded,
            'scope_note': (None if xlsx_uploaded else
                           'Файлы XLSX не загружены: глубина проверки маркировки проводов ограничена данными PDF.'),
        },
    }


def engineering_analysis(sources, cfg=None, *, uploaded_names=None):
    if not engineering_flags(cfg)['engineering_model_checks']:
        empty = EngineeringModel()
        return [], _model_summary(empty, sources)
    model = (build_model(sources) if uploaded_names is None
             else build_model(sources, names=uploaded_names))
    findings = (list(getattr(model, 'adapter_diagnostics', []))
                + list(run_verifiers(model)) + list(run_universal_verifiers(model))
                + list(run_graph_verifiers(model)))
    return findings, _model_summary(model, sources)


def apply_engineering_findings(issues, pdf_sources, cfg, *, return_summary=False, uploaded_names=None):
    """Append KD-EM-* findings to an existing issue list (pure function).

    Strictly additive by default. Only when the explicit opt-in
    cfg['engineering_model_replace_legacy'] is true are the legacy codes
    KD-PE-102 / KD-SET-203 removed from the existing list first.
    """
    try:
        findings, summary = engineering_analysis(pdf_sources, cfg, uploaded_names=uploaded_names)
    except Exception as exc:  # noqa: BLE001
        findings = [_em000('построение инженерной модели', '', exc)]
        summary = _model_summary(EngineeringModel(), pdf_sources)
    if engineering_flags(cfg)['engineering_model_replace_legacy']:
        issues = [i for i in issues if i.get('code') not in LEGACY_CODES]
    combined = list(issues) + findings
    return (combined, summary) if return_summary else combined
