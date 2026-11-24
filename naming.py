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
        if joined and (tag.fullmatch(left) or RANGE.fullmatch(left)) and not line.startswith('.'):
            return None
        joined += line
    return normalized(joined)


def expand_notation(raw, tag=None):
    """Expand semicolon-delimited designations and explicit numeric ranges."""
    tag = tag or current_tag()
    out, unknown = set(), []
    for part in raw.split(';'):
        original = part
        part = _notation_part(part, tag)
        if part is None:
            unknown.append(original.strip())
            continue
        if not part: continue
        m = RANGE.fullmatch(part)
        if m:
            left, first, right, last = m.groups()
            if right and right != left or int(last)<int(first) or int(last)-int(first)>100:
                unknown.append(part); continue
            out.update(left+str(i) for i in range(int(first),int(last)+1))
        elif tag.fullmatch(part):
            out.add(part)
        else:
            unknown.append(part)
    return out, unknown
