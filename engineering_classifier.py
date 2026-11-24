"""Conservative engineering object kinds, based on evidence rather than tag classes."""
import re
from naming import normalized, universal_reference_tag, letter_designator

KINDS = frozenset({'device', 'terminal_assembly', 'potential_bus', 'cable', 'accessory'})
_TERMINAL = re.compile(r'\b(?:terminal\s+(?:strip|block|assembly)|distribution\s+block|клеммн\w*\s+(?:колод\w*|блок\w*|сбор\w*)|распределительн\w*\s+(?:блок\w*|колод\w*))\b', re.I)
_BUS = re.compile(r'\b(?:bus(?:bar)?|potential|шина|потенциал)\b', re.I)
_CABLE = re.compile(r'\b(?:multi[- ]?core|multiconductor|cable|кабел\w*|многожильн\w*)\b', re.I)
_WIRE = re.compile(r'\b(?:single[- ]core|single conductor|wire|провод\w*|одножильн\w*)\b', re.I)
_ACCESSORY = re.compile(r'\b(?:accessory|mounting kit|аксессуар\w*|принадлежност\w*|крепеж\w*)\b', re.I)


def classify_object(tag, *, description='', parent_ref=None, terminal='', role='', known_kind=None):
    """Return a semantic kind, or None where evidence is insufficient."""
    tag = str(tag or '').strip()
    description = str(description or '')
    if parent_ref and (role in ('accessory', 'assembly_element') or _ACCESSORY.search(description)):
        return 'accessory'
    if _TERMINAL.search(description):
        return 'terminal_assembly'
    if not terminal and (_BUS.search(description) or role == 'potential'):
        return 'potential_bus'
    if _CABLE.search(description) and not _WIRE.search(description):
        return 'cable'
    if known_kind in KINDS:
        return known_kind
    if role == 'cable' and tag and not _WIRE.search(description):
        return 'cable'
    if universal_reference_tag().fullmatch(normalized(tag)) or letter_designator(tag):
        return 'device'
    return None


def split_cable_reference(value, known_kinds=None, *, endpoints=()):
    """Preserve the original group label when corroborated as non-cable."""
    value = str(value if value is not None else '').strip()
    key = normalized(value)
    known = (known_kinds or {}).get(key)
    endpoint_keys = {normalized(e.device) for e in endpoints}
    if value and (known in {'device', 'terminal_assembly', 'potential_bus', 'accessory'}
                  or key in endpoint_keys):
        return '', value
    return value, ''
