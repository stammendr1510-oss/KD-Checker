"""Spot ambiguous repeated PDF document identities within ONE upload.

PDF content digests are ephemeral; neither content nor digest is written to
findings, logs or SQLite. This is a screening hint, not document versioning.
"""
from collections import defaultdict
from hashlib import sha256

CODE = "KD-SET-102"


def pdf_identity_findings(pdf_sources):
    """Return one informational finding per duplicate (kind, pair-key).

    pdf_sources is the already verified list of (filename, kind, key, bytes)
    passed around by the API. Documents of different kinds are independent.
    """
    groups = defaultdict(list)
    for name, kind, key, content in pdf_sources:
        if kind not in ("Э3", "ПЭ3"):
            continue
        groups[(kind, key)].append((name, sha256(content).digest()))

    findings = []
    for (kind, key), variants in sorted(groups.items()):
        if len(variants) < 2:
            continue
        same = len({digest for _, digest in variants}) == 1
        summary = ("полностью совпадающим содержимым" if same else "различающимся содержимым")
        names = ", ".join(sorted({name for name, _ in variants}, key=str.casefold))
        findings.append({
            "severity": "info",
            "code": CODE,
            "file": variants[0][0],
            "location": "Состав загруженного набора",
            "message": (
                f"Среди загруженных PDF найдено {len(variants)} документа типа {kind} "
                f"с совпадающим ключом имени и {summary}: {names}."
            ),
            "recommendation": (
                "Проверьте вручную, не загружены ли повторные копии или разные версии "
                "одного документа. Совпадающее имя не устанавливает редакцию, "
                "а различие файлов не доказывает ошибку в КД."
            ),
        })
    return findings
