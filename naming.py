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
DEFAULT_CLASS_PREFIX = r'(?:[123])?(?:QF|SF|KM|KK|KV|KT|TA|SB|HLG|HLW|XG|XT|XN|XL|X|PAV)'

TAG_PREFIX = DEFAULT_CLASS_PREFIX  # historical name, re-exported for compat

_CLASS_PREFIX = DEFAULT_CLASS_PREFIX

RANGE = re.compile(r'^([\d]*[A-ZА-Я]+)(\d+)\.{2,3}([\d]*[A-ZА-Я]+)?(\d+)$', re.I)


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
        if joined and (tag.fullmatch(left) or RANGE.fullmatch(left)) and not line.startswith('.'):
            return None
        joined += line
    return normalized(joined)


def expand_notation(raw, tag=None):
    tag = tag or current_tag()
    out, unknown = set(), []
    for part in raw.split(';'):
        original = part
        part = _notation_part(part, tag)
        if part is None:
            unknown.append(original.strip())
            continue
        if not part: continue
        if '..' not in part:
            if tag.fullmatch(part): out.add(part)
            else: unknown.append(part)
            continue
        m = RANGE.fullmatch(part)
        if not m:
            unknown.append(part); continue
        left, first, right, last = m.groups()
        if right and right != left or int(last)<int(first) or int(last)-int(first)>100:
            unknown.append(part); continue
        out.update(left+str(i) for i in range(int(first),int(last)+1))
    return out, unknown
