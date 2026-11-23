"""Thin adapters: existing extractors -> EngineeringModel. All PDF/Excel I/O
stays in extractors/kd_crosscheck/full_set_completeness; this layer only maps
already-extracted data into the unified model.
"""
from __future__ import annotations

from full_set_completeness import classify_pdf_name
from engineering_model import (
    Designation, DocumentRef, EngineeringModel, SchemeMention, SourceRef, SpecRow,
    run_universal_verifiers, run_verifiers,
)
from engineering_graph import run_graph_verifiers
from full_set_completeness import specification_references
from kd_crosscheck import get_spec_rows, scheme_designations
import fitz


def source_ref(document: str, page=None, row=None, bounds=None) -> SourceRef:
    return SourceRef(document=document, page=page, row=row, bounds=bounds)


def model_from_spec_rows(rows, filename: str, model: EngineeringModel | None = None) -> EngineeringModel:
    """Map kd_crosscheck.get_spec_rows() output into SpecRow entities."""
    model = model or EngineeringModel()
    for r in rows:
        ref = source_ref(filename, page=r.get('page'), row=r.get('table_row'), bounds=r.get('row_band'))
        des = [Designation.classify(d) for d in sorted(r.get('entries', set()))]
        row = SpecRow(raw_text=r.get('source', ''), quantity=r.get('quantity'),
                      designations=des, unknown=list(r.get('unknown', [])),
                      provenance=ref)
        model.spec_rows.append(row)
        for d in des:
            model.add_device(d, ref)
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


def build_model(pdf_sources, names=None) -> EngineeringModel:
    """Convenience pipeline for one upload batch.

    pdf_sources: list of (filename, pdf_bytes); names defaults to the source
    names. Only PDF kinds already recognized by existing extractors are used.
    """
    model = EngineeringModel()
    names = names if names is not None else [n for n, _ in pdf_sources]
    attach_upload_identities(names, model)
    for name, content in pdf_sources:
        identity = classify_pdf_name(name)
        if identity is None:
            continue
        if identity.kind == 'ПЭ3':
            try:
                model_from_spec_rows(get_spec_rows(content), name, model)
            except (RuntimeError, ValueError):
                pass
        elif identity.kind == 'Э3':
            try:
                model_from_scheme(content, name, model)
            except (RuntimeError, ValueError):
                pass
        elif identity.kind == 'СП':
            try:
                model_from_sp_references(content, name, model)
            except (RuntimeError, ValueError):
                pass
    return model


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


def engineering_findings(pdf_sources, cfg=None) -> list:
    """Build the model from uploaded PDFs and run EM-108/EM-109/EM-110 verifiers.

    pdf_sources: (name, kind, pair_key, bytes) as produced by /api/check.
    Never raises: any failure yields no findings.
    """
    if not engineering_flags(cfg)['engineering_model_checks']:
        return []
    try:
        model = build_model([(name, content) for name, _, _, content in pdf_sources])
        return (list(run_verifiers(model)) + list(run_universal_verifiers(model))
                + list(run_graph_verifiers(model)))
    except Exception:
        return []


def apply_engineering_findings(issues, pdf_sources, cfg) -> list:
    """Append KD-EM-* findings to an existing issue list (pure function).

    Strictly additive by default. Only when the explicit opt-in
    cfg['engineering_model_replace_legacy'] is true are the legacy codes
    KD-PE-102 / KD-SET-203 removed from the existing list first.
    """
    findings = engineering_findings(pdf_sources, cfg)
    if engineering_flags(cfg)['engineering_model_replace_legacy']:
        issues = [i for i in issues if i.get('code') not in LEGACY_CODES]
    return list(issues) + findings
