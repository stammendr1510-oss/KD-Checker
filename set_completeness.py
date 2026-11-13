"""Conservative upload-set completeness hints, not certification of a KD package."""
from collections import defaultdict

from kd_crosscheck import designation_type, pair_key

CODE = "KD-SET-101"


def unpaired_document_findings(pdf_names):
    """Describe absent upload counterparts only when both document types are present.

    Only successfully extracted PDF names with explicit .Э3/.ПЭ3 document types
    should be passed. A single-file or single-type upload is intentionally
    inconclusive: the user may be checking one document, not a full package.
    """
    groups = defaultdict(lambda: defaultdict(list))
    kinds = set()
    for name in pdf_names:
        kind = designation_type(name)
        if kind not in ("Э3", "ПЭ3"):
            continue
        kinds.add(kind)
        key = pair_key(name)
        # Case variants of the same uploaded filename do not create repeated hints.
        if name.casefold() not in {x.casefold() for x in groups[key][kind]}:
            groups[key][kind].append(name)

    if kinds != {"Э3", "ПЭ3"}:
        return []

    findings = []
    for key in sorted(groups):
        group = groups[key]
        if "Э3" in group and "ПЭ3" in group:
            continue
        present_kind = next(iter(group))
        expected_kind = "ПЭ3" if present_kind == "Э3" else "Э3"
        for name in sorted(group[present_kind], key=str.casefold):
            findings.append({
                "severity": "info",
                "code": CODE,
                "file": name,
                "location": "Состав загруженного набора",
                "message": (
                    f"Для документа {present_kind} не найден парный {expected_kind} "
                    "с соответствующим обозначением среди успешно прочитанных PDF этой проверки."
                ),
                "recommendation": (
                    f"Если проверяется полный комплект, добавьте PDF {expected_kind} с корректным "
                    "обозначением в имени или сверьте состав вручную. "
                    "Отсутствие файла в загрузке не означает его отсутствия в КД."
                ),
            })
    return findings
