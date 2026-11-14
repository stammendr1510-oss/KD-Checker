"""Conservative guard against comparing ambiguous PDF versions as paired documents.

The caller supplies deduplicated, successfully read PDF sources from one upload.
No document content or hashes are persisted by this module.
"""
from collections import defaultdict

CODE = "KD-SET-103"


def pair_analysis_plan(sources):
    """Return (unambiguous E3/PE3 pairs, informational ambiguity findings).

    A matched document key is analyzable only when exactly one distinct PDF
    survives for each required kind. The caller may still independently analyze
    every PE3 for its own duplicate positions.
    """
    groups = defaultdict(lambda: defaultdict(list))
    for source in sources:
        name, kind, key, _content = source
        if kind in ("Э3", "ПЭ3"):
            groups[key][kind].append(source)

    pairs, findings = [], []
    for key in sorted(groups):
        group = groups[key]
        schemes, specs = group.get("Э3", []), group.get("ПЭ3", [])
        if not schemes or not specs:
            continue
        if len(schemes) == 1 and len(specs) == 1:
            pairs.append((schemes[0], specs[0]))
            continue
        counts = f"Э3: {len(schemes)}, ПЭ3: {len(specs)}"
        filenames = sorted({s[0] for s in schemes + specs}, key=str.casefold)
        findings.append({
            "severity": "info",
            "code": CODE,
            "file": filenames[0],
            "location": "Сопоставление Э3 ↔ ПЭ3",
            "message": (
                f"Сверка Э3 ↔ ПЭ3 для группы «{key}» пропущена: "
                f"загружено несколько различающихся PDF с одним обозначением ({counts})."
            ),
            "recommendation": (
                "Выберите по одному корректному PDF Э3 и ПЭ3 одной редакции "
                "и запустите проверку заново. Различие байтов не доказывает "
                "различие инженерного содержания или ошибку КД."
            ),
        })
    return pairs, findings
