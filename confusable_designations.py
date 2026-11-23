"""Universal candidate detection for visually confusable designations (KD-PE-107).

The module is pure: it compares already extracted model-like tokens and never
opens a PDF, never uses the network and never rewrites source values.

Engineering rule
----------------
A candidate is reported only when two model tokens have the same structure and
differ in exactly one position whose characters belong to a known confusable
pair (``O``/``0``, ``I``/``1``, ``S``/``5``, ``B``/``8``, Cyrillic/Latin
lookalikes).  The rule is deliberately informational: a single confusable
character may be a legitimate different model, so no automatic replacement is
performed and no error is claimed.

No rule is tied to a concrete device code, document name or sheet number.
"""
from __future__ import annotations

import re
from collections import defaultdict


CODE = "KD-PE-107"
MAX_CANDIDATES = 5
MAX_TOKEN = 64
MAX_SEGMENTS = 8
MAX_ROWS = 5000

CYR_LOOKALIKES = str.maketrans(
    "АВСЕНКМОРТХУавсенкмортху",
    "ABCEHKMOPTXYaBcehkmoptxy",
)

# A model-like token has at least two dot-separated segments, for example
# SO1.PLC.01 or ABC.123.45.  Standalone designators without a dot are handled by
# the existing TAG machinery and are intentionally not part of this rule.
MODEL_RE = re.compile(
    r"(?<![\w.])([A-ZА-Я0-9]{1,}(?:\.[A-ZА-Я0-9]{1,})+)(?![\w.])",
    re.I,
)
# Document codes such as ТНБВ.666666.666 are not product model designations.
DOC_CODE_RE = re.compile(r"^[А-ЯA-Z]{2,}\.\d{3,}\.\d{3,}$", re.I)

_CONFUSABLE_GROUPS = (
    frozenset({"O", "0"}),
    frozenset({"I", "1"}),
    frozenset({"S", "5"}),
    frozenset({"B", "8"}),
    frozenset({"А", "A"}),
    frozenset({"С", "C"}),
    frozenset({"К", "K"}),
    frozenset({"Е", "E"}),
    frozenset({"М", "M"}),
    frozenset({"Н", "H"}),
    frozenset({"Р", "P"}),
    frozenset({"Т", "T"}),
    frozenset({"Х", "X"}),
    frozenset({"У", "Y"}),
)


def _is_model_token(token):
    if not token or len(token) > MAX_TOKEN:
        return False
    if DOC_CODE_RE.fullmatch(token):
        return False
    segments = token.split(".")
    if not 2 <= len(segments) <= MAX_SEGMENTS or any(not segment for segment in segments):
        return False
    if not any(ch.isdigit() for ch in token):
        return False
    if not any(ch.isalpha() for ch in token):
        return False
    return True


def model_tokens(text):
    """Return model-like tokens from a text fragment, bounded and filtered."""
    found = set()
    for match in MODEL_RE.finditer(str(text or "")):
        token = match.group(1)
        if _is_model_token(token):
            found.add(token)
        if len(found) >= 200:
            break
    return found


def _latin_upper(token):
    return token.translate(CYR_LOOKALIKES).upper()


def confusable_label(left, right):
    """Return ``"O/0"``-style label when two tokens differ by one confusable char.

    Returns ``None`` when the lengths differ, when more than one position
    differs, or when the differing characters are not a known confusable pair.
    """
    if not left or not right or len(left) != len(right) or left == right:
        return None
    differences = [
        (left_char, right_char)
        for left_char, right_char in zip(left.upper(), right.upper())
        if left_char != right_char
    ]
    if len(differences) != 1:
        return None
    left_char, right_char = differences[0]
    for group in _CONFUSABLE_GROUPS:
        if left_char in group and right_char in group:
            return f"{left_char}/{right_char}"
    return None


def find_candidates(e3_tokens, pe3_tokens):
    """Return bounded ``(e3_token, pe3_token, label)`` candidates.

    Both token collections may be mappings or iterables.  A pair is skipped
    when both spellings exist on the same side: in that case there is no
    evidence of a mismatch, only two legitimate values.
    """
    e3 = set(e3_tokens)
    pe3 = set(pe3_tokens)
    candidates = []
    seen = set()
    for left in sorted(e3):
        for right in sorted(pe3):
            key = (left, right)
            if key in seen:
                continue
            seen.add(key)
            if left == right:
                continue
            # A Cyrillic/Latin pair is already covered by KD-PE-103; do not
            # duplicate it as a numeric/letter confusion.
            if _latin_upper(left) == _latin_upper(right):
                continue
            if left in pe3 and right in e3:
                # Both spellings are present on both sides: legitimate variants.
                continue
            label = confusable_label(left, right)
            if label is None:
                continue
            candidates.append((left, right, label))
            if len(candidates) >= MAX_CANDIDATES:
                return candidates
    return candidates


def _page_list(pages):
    ordered = sorted(set(pages))
    shown = ", ".join(str(page) for page in ordered[:3])
    if len(ordered) > 3:
        shown += f" (+{len(ordered) - 3} листов)"
    return shown


def _pe3_locations(rows_by_token, token):
    places = rows_by_token.get(token) or []
    labels = []
    for page, table_row in places[:3]:
        label = f"лист PDF {page}"
        if table_row is not None:
            label += f", строка таблицы {table_row}"
        labels.append(label)
    if len(places) > 3:
        labels.append(f"ещё {len(places) - 3} мест(а)")
    return "; ".join(labels) if labels else "место не подтверждено"


def confusable_findings(e3_filename, pe3_filename, e3_model_pages, rows):
    """Return KD-PE-107 informational candidates for one E3/PE3 pair."""
    if not e3_model_pages or not rows:
        return []
    pe3_model_rows = defaultdict(list)
    for row in rows[:MAX_ROWS]:
        page = row.get("page")
        table_row = row.get("table_row") if row.get("row_band") is not None else None
        text = f"{row.get('name') or ''} {row.get('source') or ''}"
        for token in model_tokens(text):
            pe3_model_rows[token].append((page, table_row))
    if not pe3_model_rows:
        return []

    findings = []
    for left, right, label in find_candidates(e3_model_pages, pe3_model_rows):
        findings.append({
            "severity": "info",
            "code": CODE,
            "file": pe3_filename,
            "location": (
                f"Э3: лист(ы) PDF {_page_list(e3_model_pages.get(left, ()))}; "
                f"ПЭ3: {_pe3_locations(pe3_model_rows, right)}"
            ),
            "message": (
                f"Возможное несоответствие обозначения исполнения: в Э3 «{left}», "
                f"в ПЭ3 «{right}». Отличие: {label} (визуально похожие символы). "
                "Это кандидат на опечатку, а не подтверждённая ошибка: "
                "символы не заменяются автоматически, исходные значения сохранены."
            ),
            "recommendation": (
                "Сверьте обозначение исполнения с исходным проектом и документацией "
                "изготовителя. Не выполняйте глобальную замену похожих символов; "
                "решение принимает инженер."
            ),
        })
    return findings
