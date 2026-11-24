"""Designation normalization and reference-class handling; no hardcoded rules.

Reference classes are configurable through configure_reference_classes(); the
default preserves the historical class list so existing behavior and tests are
unchanged. Invariant: class lists may appear in synthetic tests, never as
special-cased rules.
"""
from __future__ import annotations
import re

CYR_LOOKALIKES = str.maketrans('АВСЕНКМОРТХУавсенкмортху', 'ABCEHKMOPTXYaBcehkmoptxy')

# Default reference class prefix (historical behavior). Override via
# configure_reference_classes() with a config-driven pattern.
DEFAULT_CLASS_PREFIX = (
    r'(?:\d{1,2})?(?:HLG|HLW|HL|KLD|KM|KK|KV|KT|KA|KL|K|'
    r'QF|SF|TA|SB|XG|XT|XN|XL|X|PAV)'
)

TAG_PREFIX = DEFAULT_CLASS_PREFIX  # historical name, re-exported for compat

_CLASS_PREFIX = DEFAULT_CLASS_PREFIX
UNIVERSAL_CLASS_PREFIX = r'(?:\d{1,2})?[A-ZА-ЯЁ]{1,12}'

# An explicit range separator: three dots or an en/em dash.  An ASCII hyphen is
# not accepted because it is common inside product type codes.
RANGE = re.compile(r'^([\d]*[A-ZА-Я]+)(\d+)(?:\.{2,3}|[–—])([\d]*[A-ZА-Я]+)?(\d+)$', re.I)


DOTTED_RANGE = re.compile(r'^([\d]*[A-ZА-ЯЁ]+)(\d+)\.(\d+)(?:\.{2,3}|[–—])([\d]*[A-ZА-ЯЁ]+)(\d+)\.(\d+)(prefix_regex: str) -> None:
    """Set the reference class prefix pattern (designation-agnostic rule input)."""
    global _CLASS_PREFIX
    re.compile(prefix_regex)  # validate eagerly
    _CLASS_PREFIX = prefix_regex


def reset_reference_classes() -> None:
    configure_reference_classes(DEFAULT_CLASS_PREFIX)


def reference_tag(prefix: str | None = None):
    """Compile the designator tag matcher for a class prefix (default: current)."""
    return re.compile(r'(?<![\w.])' + (prefix or _CLASS_PREFIX) + r'\d+(?:\.\d+)?(?=\s*:|\b)', re.I)


def current_tag():
    return reference_tag()


def universal_reference_tag():
    """General table-designation matcher; independent of device class names."""
    return reference_tag(UNIVERSAL_CLASS_PREFIX)


def normalized(s):
    # Normalization is used only for *matching*, not silently rewriting source files.
    return s.translate(CYR_LOOKALIKES).upper().strip().replace(' ', '')


def _notation_part(raw, tag=None):
    """Join incomplete fragments; never append digits to a complete notation."""
    tag = tag or current_tag()
    fragments = [part.replace('…','...') for part in re.split(r'\s+', raw.strip()) if part]
    joined = ''
    for line in fragments:
        left = normalized(joined)
        if joined and (tag.fullmatch(left) or RANGE.fullmatch(left) or DOTTED_RANGE.fullmatch(left)) and not line.startswith(('.', '–', '—')):
            return None
        joined += line
    return normalized(joined)


def _is_complete_notation(text, tag=None):
    value = normalized(text)
    tag = tag or current_tag()
    return bool(tag.fullmatch(value) or RANGE.fullmatch(value) or DOTTED_RANGE.fullmatch(value) or letter_designator(text))

def _split_single_line(line, tag=None):
    """Split tokens of one physical line, preserving the legacy ambiguity rule.

    A complete designation followed by another token on the *same* line is
    ambiguous (``XT1 2`` must not become ``XT1`` or ``XT12``).  A wrapped range
    such as ``1TA1... 1TA3`` is joined because the first token is incomplete.
    """
    tag = tag or current_tag()
    joined = ''
    for token in (token for token in line.split() if token):
        if not joined:
            joined = token
            continue
        left = normalized(joined)
        if (tag.fullmatch(left) or RANGE.fullmatch(left) or DOTTED_RANGE.fullmatch(left)) and not token.startswith(('.', '–', '—')):
            return line.strip(), True
        joined += token
    return joined, False

def _notation_parts(raw, tag=None):
    """Split a PE3 designator cell into ``(fragment, ambiguous)`` pairs.

    Explicit separators ``;`` and ``,`` always start a new fragment.  A line
    break between two complete designations is also a separator (``TA2`` newline
    ``TA3``), while a line break inside a wrapped range continues it
    (``1TA1...`` + ``1TA3`` or ``3TA1`` + ``...3TA3``).  A complete designation
    followed by a non-continuation token on another line is kept ambiguous:
    ``XT1`` + ``2`` must never invent ``XT1`` or ``XT12``.
    """
    tag = tag or current_tag()
    parts = []
    for chunk in re.split(r'[;,]', raw.replace('…', '...')):
        stripped = chunk.strip()
        if not stripped:
            continue
        pending = None
        for line in re.split(r'\r?\n', stripped):
            line = line.strip()
            if not line:
                continue
            if pending is None:
                pending = line
                continue
            first = line.split()[0]
            if letter_designator(pending) and re.fullmatch(r'\d+(?:\.\d+)?', line):
                pending += line
                continue
            if not _is_complete_notation(pending, tag) or first.startswith(('.', '–', '—')):
                pending += line
                continue
            if _is_complete_notation(first, tag):
                fragment, ambiguous = _split_single_line(pending, tag)
                parts.append((fragment, ambiguous))
                pending = line
                continue
            # Complete designation followed by a non-continuation token on a
            # new line: record the whole fragment as ambiguous, do not guess.
            parts.append((pending + '\n' + line, True))
            pending = None
        if pending is not None:
            parts.append(_split_single_line(pending, tag))
    return parts

def expand_notation(raw, tag=None):
    """Universal group expansion: separators, wrapped ranges, line breaks.

    Never invents values: ambiguous fragments are preserved in ``unknown``.
    """
    tag = tag or universal_reference_tag()
    out, unknown = set(), []
    for fragment, ambiguous in _notation_parts(raw, tag):
        if ambiguous:
            unknown.append(fragment)
            continue
        part = normalized(fragment)
        if not part:
            continue
        dotted = _dotted_entries(part)
        if dotted is not None:
            if dotted:
                out.update(dotted)
            else:
                unknown.append(fragment)
            continue
        m = RANGE.fullmatch(part)
        if m:
            left, first, right, last = m.groups()
            if right and right != left or int(last) < int(first) or int(last) - int(first) > 100:
                unknown.append(fragment)
                continue
            out.update(left + str(i) for i in range(int(first), int(last) + 1))
            continue
        if tag.fullmatch(part) or (letter_designator(fragment) and (not re.search(r'\d', raw) or ';' in raw or ',' in raw)):
            out.add(part)
        else:
            unknown.append(fragment)
    return out, unknown


def letter_designator(value):
    """Letters are accepted only as an explicit uppercase cell fragment."""
    return bool(re.fullmatch(r'[A-ZА-ЯЁ]{1,12}', value.strip()))


def base_designator(value):
    value = normalized(value)
    match = re.fullmatch(r'(.+)\.\d+', value)
    return match.group(1) if match and universal_reference_tag().fullmatch(match.group(1)) else value
, re.I)


def _dotted_entries(part):
    match = DOTTED_RANGE.fullmatch(part)
    if not match:
        return None
    left, major, minor, right, end_major, end_minor = match.groups()
    a, b, c, d = map(int, (major, minor, end_major, end_minor))
    if left != right:
        return set()
    if b == d and 0 <= c - a <= 100:
        return {f'{left}{i}.{minor}' for i in range(a, c + 1)}
    if a == c and 0 <= d - b <= 100:
        return {f'{left}{major}.{i}' for i in range(b, d + 1)}
    return set()


def configure_reference_classes(prefix_regex: str) -> None:
    """Set the reference class prefix pattern (designation-agnostic rule input)."""
    global _CLASS_PREFIX
    re.compile(prefix_regex)  # validate eagerly
    _CLASS_PREFIX = prefix_regex


def reset_reference_classes() -> None:
    configure_reference_classes(DEFAULT_CLASS_PREFIX)


def reference_tag(prefix: str | None = None):
    """Compile the designator tag matcher for a class prefix (default: current)."""
    return re.compile(r'(?<![\w.])' + (prefix or _CLASS_PREFIX) + r'\d+(?:\.\d+)?(?=\s*:|\b)', re.I)


def current_tag():
    return reference_tag()


def universal_reference_tag():
    """General table-designation matcher; independent of device class names."""
    return reference_tag(UNIVERSAL_CLASS_PREFIX)


def normalized(s):
    # Normalization is used only for *matching*, not silently rewriting source files.
    return s.translate(CYR_LOOKALIKES).upper().strip().replace(' ', '')


def _notation_part(raw, tag=None):
    """Join incomplete fragments; never append digits to a complete notation."""
    tag = tag or current_tag()
    fragments = [part.replace('…','...') for part in re.split(r'\s+', raw.strip()) if part]
    joined = ''
    for line in fragments:
        left = normalized(joined)
        if joined and (tag.fullmatch(left) or RANGE.fullmatch(left) or DOTTED_RANGE.fullmatch(left)) and not line.startswith(('.', '–', '—')):
            return None
        joined += line
    return normalized(joined)


def _is_complete_notation(text, tag=None):
    value = normalized(text)
    tag = tag or current_tag()
    return bool(tag.fullmatch(value) or RANGE.fullmatch(value) or letter_designator(text))

def _split_single_line(line, tag=None):
    """Split tokens of one physical line, preserving the legacy ambiguity rule.

    A complete designation followed by another token on the *same* line is
    ambiguous (``XT1 2`` must not become ``XT1`` or ``XT12``).  A wrapped range
    such as ``1TA1... 1TA3`` is joined because the first token is incomplete.
    """
    tag = tag or current_tag()
    joined = ''
    for token in (token for token in line.split() if token):
        if not joined:
            joined = token
            continue
        left = normalized(joined)
        if (tag.fullmatch(left) or RANGE.fullmatch(left) or DOTTED_RANGE.fullmatch(left)) and not token.startswith(('.', '–', '—')):
            return line.strip(), True
        joined += token
    return joined, False

def _notation_parts(raw, tag=None):
    """Split a PE3 designator cell into ``(fragment, ambiguous)`` pairs.

    Explicit separators ``;`` and ``,`` always start a new fragment.  A line
    break between two complete designations is also a separator (``TA2`` newline
    ``TA3``), while a line break inside a wrapped range continues it
    (``1TA1...`` + ``1TA3`` or ``3TA1`` + ``...3TA3``).  A complete designation
    followed by a non-continuation token on another line is kept ambiguous:
    ``XT1`` + ``2`` must never invent ``XT1`` or ``XT12``.
    """
    tag = tag or current_tag()
    parts = []
    for chunk in re.split(r'[;,]', raw.replace('…', '...')):
        stripped = chunk.strip()
        if not stripped:
            continue
        pending = None
        for line in re.split(r'\r?\n', stripped):
            line = line.strip()
            if not line:
                continue
            if pending is None:
                pending = line
                continue
            first = line.split()[0]
            if letter_designator(pending) and re.fullmatch(r'\d+(?:\.\d+)?', line):
                pending += line
                continue
            if not _is_complete_notation(pending, tag) or first.startswith(('.', '–', '—')):
                pending += line
                continue
            if _is_complete_notation(first, tag):
                fragment, ambiguous = _split_single_line(pending, tag)
                parts.append((fragment, ambiguous))
                pending = line
                continue
            # Complete designation followed by a non-continuation token on a
            # new line: record the whole fragment as ambiguous, do not guess.
            parts.append((pending + '\n' + line, True))
            pending = None
        if pending is not None:
            parts.append(_split_single_line(pending, tag))
    return parts

def expand_notation(raw, tag=None):
    """Universal group expansion: separators, wrapped ranges, line breaks.

    Never invents values: ambiguous fragments are preserved in ``unknown``.
    """
    tag = tag or universal_reference_tag()
    out, unknown = set(), []
    for fragment, ambiguous in _notation_parts(raw, tag):
        if ambiguous:
            unknown.append(fragment)
            continue
        part = normalized(fragment)
        if not part:
            continue
        m = RANGE.fullmatch(part)
        if m:
            left, first, right, last = m.groups()
            if right and right != left or int(last) < int(first) or int(last) - int(first) > 100:
                unknown.append(fragment)
                continue
            out.update(left + str(i) for i in range(int(first), int(last) + 1))
            continue
        if tag.fullmatch(part) or (letter_designator(fragment) and not re.search(r'\d', raw)):
            out.add(part)
        else:
            unknown.append(fragment)
    return out, unknown


def letter_designator(value):
    """Letters are accepted only as an explicit uppercase cell fragment."""
    return bool(re.fullmatch(r'[A-ZА-ЯЁ]{1,12}', value.strip()))


def base_designator(value):
    value = normalized(value)
    match = re.fullmatch(r'(.+)\.\d+', value)
    return match.group(1) if match and universal_reference_tag().fullmatch(match.group(1)) else value
