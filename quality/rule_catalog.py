"""Review-only inventory of implemented KD Checker finding identifiers.

This module does not execute checks or claim regulatory compliance.
When an identifier changes, reviewers must classify its evidence and scope.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    direction: str
    evidence: str  # "direct", "heuristic", or "informational"
    note: str


RULES: dict[str, Rule] = {
    "KD-000": Rule("input", "direct", "Unsupported or invalid input"),
    "KD-001": Rule("designations", "heuristic", "Filename pattern match"),
    "KD-002": Rule("documents", "heuristic", "PDF text-layer sufficiency"),
    "KD-003": Rule("documents", "heuristic", "Empty DXF modelspace"),
    "KD-004": Rule("documents", "informational", "DWG cannot be deeply inspected"),
    "KD-005": Rule("documents", "informational", "Reader limits"),
    "KD-010": Rule("specification", "heuristic", "Configured spreadsheet headers"),
    "KD-011": Rule("duplicates", "direct", "Repeated position in extracted spreadsheet rows"),
    "KD-012": Rule("cross_document", "heuristic", "Filename-only specification reference"),
    "KD-020": Rule("duplicates", "heuristic", "Same designation and file extension"),
    "KD-PE-000": Rule("specification", "heuristic", "PE3 row extraction uncertain"),
    "KD-PE-100": Rule("cross_document", "informational", "E3 and PE3 text match only"),
    "KD-PE-101": Rule("cross_document", "informational", "No PE3 designator text match in E3"),
    "KD-PE-102": Rule("specification", "heuristic", "Extracted PE3 quantity discrepancy"),
    "KD-PE-103": Rule("designations", "heuristic", "Mixed-script lookalike warning"),
    "KD-PE-104": Rule("duplicates", "heuristic", "PE3 repeated designation in verified rows"),
    "KD-PE-105": Rule("cross_document", "heuristic", "E3 designators absent from expanded PE3 positions"),
    "KD-PE-106": Rule("duplicates", "heuristic", "Repeated PE3 designation with incompatible product attributes"),
    "KD-PE-107": Rule("designations", "heuristic", "Possible confusable-character mismatch between E3 and PE3"),
    "KD-SET-101": Rule("completeness", "informational", "Missing uploaded E3/PE3 counterpart"),
    "KD-SET-102": Rule("duplicates", "informational", "Repeated uploaded PDF identity"),
    "KD-SET-103": Rule("cross_document", "informational", "Ambiguous PDF version pairing"),
    "KD-SET-201": Rule("completeness", "informational", "Current upload inventory only"),
    "KD-SET-202": Rule("completeness", "informational", "Repeated uploaded document kind"),
    "KD-SET-203": Rule("completeness", "informational", "Explicit SP reference absent from upload"),
    "KD-CON-101": Rule("connections", "informational", "Repeated explicit structured connection record"),
    "KD-CON-102": Rule("connections", "informational", "Equal endpoints in explicit structured record"),
}

# KD-CON codes are staged in PR #47 and deliberately optional before its merge.
# No invented coverage: these targets are not demonstrated by the above rules.
NOT_YET_VERIFIED = {
    "connections_cables_terminals": "Structured electrical topology validation",
    "standards": "Clause-based ESKD/GOST compliance validation",
    "multi_pc": "Authenticated centralized multi-PC operation",
}
