"""KD-PE-106 repeatable engineering acceptance stand (synthetic only).

Purpose
-------
Give an engineer a deterministic, offline way to re-run the engineering
scenarios A-H of the KD-PE-106 acceptance task without ChatGPT, cloud AI or
paid services.

Design rules
------------
* Synthetic documents only.  PDFs are generated in memory by PyMuPDF; nothing
  from a real enterprise bundle is read, stored or uploaded.
* The stand calls the *existing* pipeline (``kd_crosscheck.run``,
  ``extractors.extract`` and the existing classification/confidence modules).
  It is not a parallel test framework and it does not re-implement engineering
  rules.
* ``NOT VERIFIED`` is never converted into ``PASS``.  Scenarios that cannot be
  executed in the current environment are reported as ``NOT_VERIFIED``.
* Exit code is non-zero when the acceptance cannot be confirmed:
  ``1`` - at least one executed scenario FAILED (critical);
  ``2`` - no failures, but at least one scenario is NOT_VERIFIED;
  ``0`` - every scenario PASSED.

Usage
-----
    python acceptance_stand.py --list
    python acceptance_stand.py
    python acceptance_stand.py --json acceptance-report.json
    python acceptance_stand.py --scenario A --scenario B --json report.json
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import fitz

import kd_crosscheck as checker
from e3_pe3_inventory import Mention, compare_inventories, valid_details
from extractors import extract
from identifier_classification import (
    AMBIGUOUS,
    DEVICE_CANDIDATE,
    TERMINAL_OR_PIN,
    classify_identifier,
)
from set_completeness import unpaired_document_findings

KIND = "kd-checker-engineering-acceptance-v1"
E3_NAME = "SYNTH.106.001.Э3.pdf"
PE3_NAME = "SYNTH.106.001.ПЭ3.pdf"

PASS = "PASS"
FAIL = "FAIL"
NOT_VERIFIED = "NOT_VERIFIED"

PAGE_W, PAGE_H = 600.0, 800.0
GRID_LEFT, GRID_RIGHT = 57.0, 480.0
QUANTITY_X = 438.0
NOTE_X = 456.0

# A Cyrillic-capable font is required for realistic note markers such as "см.".
# If no such font exists, the cross-reference scenario falls back to a Latin
# document number and the limitation is stated explicitly.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
)


def _font_path() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


# ---------------------------------------------------------------------------
# Synthetic PDF builders
# ---------------------------------------------------------------------------

def build_e3_pages(pages: list[list[str]]) -> bytes:
    """Build an E3-like PDF: one list of text lines per page."""
    font = _font_path()
    with fitz.open() as pdf:
        for lines in pages:
            page = pdf.new_page(width=PAGE_W, height=PAGE_H)
            fontname = "helv"
            if font:
                page.insert_font(fontname="kd106", fontfile=font)
                fontname = "kd106"
            for index, line in enumerate(lines):
                page.insert_text((60, 90 + index * 14), line, fontsize=7, fontname=fontname)
        return pdf.tobytes()


def build_pe3(rows: list[dict]) -> bytes:
    """Build a PE3-like PDF table with horizontal row bands and a quantity column.

    ``rows`` items: ``{"entries": "1QF1;1QF2", "quantity": "3", "note": "17"}``.
    A numeric note is placed in a separate column so it cannot be mistaken for
    the quantity (the existing parser must keep them apart).
    """
    with fitz.open() as pdf:
        page = pdf.new_page(width=PAGE_W, height=PAGE_H)
        top = 80.0
        for row in rows:
            bottom = top + 55.0
            for y in (top, bottom):
                page.draw_line((GRID_LEFT, y), (GRID_RIGHT, y))
            page.insert_text((60, top + 18), str(row["entries"]), fontsize=6)
            page.insert_text((QUANTITY_X, top + 18), str(row["quantity"]), fontsize=6)
            note = row.get("note")
            if note:
                page.insert_text((NOTE_X, top + 18), str(note), fontsize=6)
            top = bottom
        return pdf.tobytes()


def build_blank_pdf() -> bytes:
    """A PDF with no text layer at all (only a drawn rectangle)."""
    with fitz.open() as pdf:
        page = pdf.new_page(width=PAGE_W, height=PAGE_H)
        page.draw_rect(fitz.Rect(60, 60, 400, 300), color=(0, 0, 0))
        return pdf.tobytes()


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    scenario: str
    title: str
    engineering_rule: str
    expected: str
    actual: str
    criterion: str
    status: str
    evidence: dict = field(default_factory=dict)
    limitation: str = ""

    def to_dict(self) -> dict:
        payload = {
            "scenario": self.scenario,
            "title": self.title,
            "engineering_rule": self.engineering_rule,
            "expected": self.expected,
            "actual": self.actual,
            "criterion": self.criterion,
            "status": self.status,
            "evidence": self.evidence,
        }
        if self.limitation:
            payload["limitation"] = self.limitation
        return payload


def _codes(findings) -> list[str]:
    return [str(item.get("code")) for item in findings]


def _kd105(findings) -> list[dict]:
    return [item for item in findings if item.get("code") == "KD-PE-105"]


def _warnings(findings) -> list[dict]:
    return [item for item in findings if item.get("severity") == "warning"]


def _issues105(findings) -> list[dict]:
    return [item for item in _kd105(findings) if isinstance(item.get("details"), dict)]


def _valid(findings) -> list[bool]:
    return [valid_details(item["details"], {E3_NAME, PE3_NAME}, item) for item in _issues105(findings)]


def _run(e3_bytes: bytes, pe3_bytes: bytes) -> list[dict]:
    return checker.run(E3_NAME, e3_bytes, PE3_NAME, pe3_bytes, include_evidence=True)


def _case(scenario, title, rule, expected, criterion, findings, ok, actual,
          *, evidence=None, limitation="") -> CaseResult:
    return CaseResult(
        scenario=scenario,
        title=title,
        engineering_rule=rule,
        expected=expected,
        actual=actual,
        criterion=criterion,
        status=PASS if ok else FAIL,
        evidence=evidence or {"codes": _codes(findings)},
        limitation=limitation,
    )


# ---------------------------------------------------------------------------
# Scenarios A-H
# ---------------------------------------------------------------------------

def scenario_a() -> CaseResult:
    """A. Correct bundle: no false critical findings."""
    e3 = build_e3_pages([["1QF1 1QF2", "1KM1", "1TA1", "1SB1"]])
    pe3 = build_pe3([
        {"entries": "1QF1;1QF2", "quantity": "2"},
        {"entries": "1KM1", "quantity": "1"},
        {"entries": "1TA1", "quantity": "1"},
        {"entries": "1SB1", "quantity": "1"},
    ])
    findings = _run(e3, pe3)
    bad = [item for item in _warnings(findings) if item.get("code") == "KD-PE-105"]
    actual = "warning KD-PE-105: %d; все коды: %s" % (len(bad), ", ".join(_codes(findings)))
    return _case(
        "A", "Корректный комплект",
        "Полное совпадение обозначений не должно порождать ложных замечаний об отсутствии.",
        "Нет ни одного warning KD-PE-105.",
        "len(warnings KD-PE-105) == 0",
        findings, not bad, actual,
    )


def scenario_b() -> CaseResult:
    """B. A component present in E3 is absent from PE3."""
    e3 = build_e3_pages([["1QF1 1QF2 1QF3"]])
    pe3 = build_pe3([{"entries": "1QF1;1QF2", "quantity": "3"}])
    findings = _run(e3, pe3)
    issues = _issues105(findings)
    missing = sorted({item["designation"] for issue in issues
                      for item in issue["details"].get("missing", [])})
    warnings = [item for item in _warnings(findings) if item.get("code") == "KD-PE-105"]
    details = issues[0]["details"] if issues else {}
    ok = (
        len(warnings) == 1
        and missing == ["1QF3"]
        and bool(issues)
        and all(_valid(findings))
        and details.get("reason") == "quantity_mismatch"
        and details.get("pe3_page") is not None
    )
    actual = "warning=%d missing=%s reason=%s confidence=%s valid=%s" % (
        len(warnings), missing, details.get("reason"), details.get("confidence"), _valid(findings),
    )
    return _case(
        "B", "Компонент отсутствует в ПЭ3",
        "Если строка ПЭ3 уникальна и её количество больше числа раскрытых обозначений, "
        "отсутствующее обозначение Э3 является предупреждением с доказательством.",
        "Ровно один warning KD-PE-105 с missing=['1QF3'] и подтверждённым листом ПЭ3.",
        "warning==1, missing==['1QF3'], reason==quantity_mismatch, valid_details",
        findings, ok, actual,
    )


def scenario_c() -> CaseResult:
    """C. An extra PE3 position without a scheme counterpart."""
    e3 = build_e3_pages([["1QF1 1QF2"]])
    pe3 = build_pe3([
        {"entries": "1QF1;1QF2", "quantity": "2"},
        {"entries": "1QF9", "quantity": "1"},
    ])
    findings = _run(e3, pe3)
    kd105 = _kd105(findings)
    unconfirmed = [item for item in findings if item.get("code") == "KD-PE-101"]
    ok = not kd105 and all(item.get("severity") == "info" for item in unconfirmed)
    actual = "KD-PE-105=%d KD-PE-101=%s" % (len(kd105), [item.get("severity") for item in unconfirmed])
    return _case(
        "C", "Лишняя позиция ПЭ3",
        "Позиция ПЭ3 без прямого обозначения на схеме не является ошибкой КД: "
        "правило проверяет только обозначения Э3, отсутствующие в ПЭ3.",
        "Нет KD-PE-105; неподтверждённые позиции ПЭ3 только info.",
        "нет KD-PE-105; KD-PE-101 только info",
        findings, ok, actual,
        limitation="Обратное направление (ПЭ3 без Э3) не является ошибкой по контракту KD-PE-105.",
    )


def _mentions(items) -> list[Mention]:
    return [
        Mention(
            designation=designation,
            raw=designation,
            page=page,
            kind="label",
            context=designation,
            confidence="high",
            line_text=designation,
        )
        for designation, page in items
    ]


def _compare(mentions, rows) -> list[dict]:
    pages = max((item.page for item in mentions), default=1)
    return compare_inventories(
        E3_NAME, PE3_NAME, mentions, rows,
        e3_word_count=len(mentions),
        e3_pages_processed=pages,
        e3_pages_with_text=pages,
    )


def _row(entries, quantity, *, page=2, table_row=2, band=(70.0, 130.0)):
    return {
        "page": page,
        "source": ";".join(entries),
        "quantity": quantity,
        "entries": set(entries),
        "unknown": [],
        "row_band": band,
        "table_row": table_row if band is not None else None,
        "name": "",
        "name_confidence": "low",
        "page_size_pt": [PAGE_W, PAGE_H],
        "pdf_truncated": False,
        "duplicate_eligible": False,
    }


def scenario_d() -> CaseResult:
    """D. Duplicates: real duplicate vs legitimate multi-sheet references."""
    mentions = _mentions([("1QF1", 1), ("1QF1", 2), ("1QF2", 2)])
    rows = [_row(["1QF1", "1QF2"], 2)]
    findings = _compare(mentions, rows)
    kd105 = _kd105(findings)
    ok = not kd105
    actual = "KD-PE-105=%d; страницы 1QF1=%s" % (
        len(kd105), sorted({item.page for item in mentions if item.designation == "1QF1"}),
    )
    return _case(
        "D", "Повторное обозначение на разных листах",
        "Повтор одного и того же обозначения на нескольких листах Э3 — законная "
        "многолистовая ссылка, а не дубль и не отсутствие в ПЭ3.",
        "Нет KD-PE-105 для обозначения, повторённого на двух листах.",
        "нет KD-PE-105 при повторении 1QF1 на листах 1 и 2",
        findings, ok, actual,
        limitation="Дубли внутри ПЭ3 проверяет отдельное правило KD-PE-104 (tests/test_pe3_duplicates.py).",
    )


def scenario_e() -> CaseResult:
    """E. Ambiguous and family-specific designations."""
    probes = [
        ("X1", "X1", AMBIGUOUS),
        ("XT1", "XT1", DEVICE_CANDIDATE),
        ("SF1", "SF1", DEVICE_CANDIDATE),
        ("QF1", "QF1", DEVICE_CANDIDATE),
        ("KM1", "KM1", DEVICE_CANDIDATE),
        ("SB1", "SB1", DEVICE_CANDIDATE),
        ("K1", "K1", AMBIGUOUS),
        ("KA1", "KA1", AMBIGUOUS),
        ("HL1", "HL1", AMBIGUOUS),
        ("X1", "X1:2", TERMINAL_OR_PIN),
    ]
    observed = {}
    mismatches = []
    for raw, context, expected in probes:
        item = classify_identifier(raw, kind="label", line_text=context)
        observed[f"{raw} / {context}"] = item.category
        if item.category != expected:
            mismatches.append(f"{raw}:{item.category}!={expected}")
    actual = "; ".join(f"{key}={value}" for key, value in observed.items())
    return _case(
        "E", "Неоднозначные и семейственные обозначения",
        "Одиночное X-обозначение неоднозначно и не может дать warning; XT/SF/QF/KM/SB — "
        "самостоятельные изделия; контакт X1:2 — вывод, а не изделие.",
        "X1=ambiguous, XT1/SF1/QF1/KM1/SB1=device_candidate, X1:2=terminal_or_pin.",
        "категория каждого пробного обозначения совпадает с ожидаемой",
        [], not mismatches, actual,
        evidence={"observed": observed, "mismatches": mismatches},
        limitation="K1, KA1, HL1 не распознаются как изделия: их семейств нет в TAG "
                   "(см. docs/kd-pe-106-known-limitations.md).",
    )


def scenario_f() -> CaseResult:
    """F. Different notation formats must not be silently repaired."""
    probes = [
        ("км1", "KM1", DEVICE_CANDIDATE),
        ("КМ1", "KM1", DEVICE_CANDIDATE),
        ("1-KM1", "1-KM1", AMBIGUOUS),
        ("KM 1", "KM 1", DEVICE_CANDIDATE),
        ("3TA2", "3TA2", DEVICE_CANDIDATE),
        ("XT4.2", "XT4.2", TERMINAL_OR_PIN),
    ]
    observed = {}
    problems = []
    for raw, context, expected in probes:
        item = classify_identifier(raw, kind="label", line_text=context)
        observed[f"{raw} / {context}"] = item.category
        if item.category != expected:
            problems.append(f"{raw}:{item.category}!={expected}")
    actual = "; ".join(f"{key}={value}" for key, value in observed.items())
    return _case(
        "F", "Разные форматы обозначений",
        "Регистр и кириллические похожие буквы нормализуются только для сопоставления; "
        "пробел внутри обозначения нормализуется, а дефис — нет и не «исправляется».",
        "км1/КМ1/KM 1=device_candidate; 1-KM1=ambiguous; XT4.2=terminal_or_pin.",
        "категория каждого формата совпадает с ожидаемой",
        [], not problems, actual,
        evidence={"observed": observed, "mismatches": problems},
        limitation="Нормализация пробелов и похожих букв документирована в "
                   "docs/kd-pe-105-contextual-classification.md и не переписывает исходные файлы.",
    )


def _upload_path(name: str, data: bytes) -> dict:
    """Model the real upload path: extraction either accepts or rejects a file.

    The application converts an extraction exception into an explicit KD-000
    error finding (verified by tests/test_kd_pe_106_acceptance.py), so a
    rejection is a blocked check, never a silent success.
    """
    try:
        document = extract(name, data)
        return {"extract": "accepted", "pages": document.pages, "text_chars": len(document.text or "")}
    except Exception as error:  # noqa: BLE001 - the class of parse failure is the evidence
        return {"extract": "rejected", "error": type(error).__name__}


def scenario_g() -> CaseResult:
    """G. Damaged or missing documents must not produce a silent success."""
    blank = build_blank_pdf()
    results = {
        "empty_pe3": _upload_path(PE3_NAME, b""),
        "corrupt_pe3": _upload_path(PE3_NAME, b"not a pdf at all"),
        "corrupt_xlsx": _upload_path("SYNTH.106.001.СП.xlsx", b"PK\x03\x04broken"),
        "no_text_pe3": _upload_path(PE3_NAME, blank),
    }
    no_text_findings = _run(build_e3_pages([["1QF1"]]), blank)
    results["no_text_pe3"]["codes"] = _codes(no_text_findings)
    results["missing_pair"] = {
        "codes": _codes(unpaired_document_findings([E3_NAME, "SYNTH.106.999.ПЭ3.pdf"])),
    }
    rejected = all(results[key]["extract"] == "rejected"
                   for key in ("empty_pe3", "corrupt_pe3", "corrupt_xlsx"))
    no_text_blocked = any(code in ("KD-PE-000", "KD-000", "KD-002")
                          for code in results["no_text_pe3"]["codes"])
    missing_pair_flagged = "KD-SET-101" in results["missing_pair"]["codes"]
    actual = json.dumps(results, ensure_ascii=False, sort_keys=True)
    return _case(
        "G", "Повреждённые документы",
        "Если документ не удалось обработать, программа обязана сообщить ограничение "
        "проверки и не выдавать успешный результат.",
        "Пустой/повреждённый PDF и XLSX отклоняются при чтении (KD-000), PDF без текста "
        "даёт блокирующее замечание, отсутствие пары Э3/ПЭ3 отмечается KD-SET-101.",
        "rejected for empty/corrupt, blocking code for no-text, KD-SET-101 for missing pair",
        no_text_findings, rejected and no_text_blocked and missing_pair_flagged, actual,
        evidence=results,
    )


def scenario_h() -> CaseResult:
    """H. Multi-sheet scheme must preserve the origin of the finding."""
    cross_reference = "см. QF1" if _font_path() else "ABCD.123456.789 QF1"
    e3 = build_e3_pages([
        ["1QF1"],
        ["1QF2", cross_reference],
    ])
    pe3 = build_pe3([{"entries": "1QF1", "quantity": "2"}])
    findings = _run(e3, pe3)
    issues = _issues105(findings)
    pages = sorted({page for issue in issues for page in issue["details"].get("e3_pages", [])})
    missing = sorted({item["designation"] for issue in issues
                      for item in issue["details"].get("missing", [])})
    ok = (
        missing == ["1QF2"]
        and 2 in pages
        and bool(issues)
        and all(_valid(findings))
        and not any(item["designation"] == "1QF1" for issue in issues
                    for item in issue["details"].get("missing", []))
    )
    actual = "missing=%s e3_pages=%s valid=%s" % (missing, pages, _valid(findings))
    return _case(
        "H", "Многолистовая схема",
        "Обозначение со второго листа Э3 должно диагностироваться с сохранением листа "
        "происхождения, а обозначение с первого листа не должно считаться отсутствующим.",
        "missing=['1QF2'], лист 2 присутствует в e3_pages, 1QF1 не отсутствует.",
        "missing==['1QF2'] и 2 in e3_pages и valid_details",
        findings, ok, actual,
    )


SCENARIOS = {
    "A": scenario_a,
    "B": scenario_b,
    "C": scenario_c,
    "D": scenario_d,
    "E": scenario_e,
    "F": scenario_f,
    "G": scenario_g,
    "H": scenario_h,
}

SCENARIO_TITLES = {
    "A": "Корректный комплект",
    "B": "Компонент отсутствует в ПЭ3",
    "C": "Лишняя позиция ПЭ3",
    "D": "Дубли обозначений",
    "E": "Неоднозначные обозначения",
    "F": "Разные форматы обозначений",
    "G": "Повреждённые документы",
    "H": "Многолистовые схемы",
}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _source_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
            cwd=str(Path(__file__).resolve().parent),
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and len(value) == 40 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _digest(results: list[CaseResult]) -> str:
    payload = json.dumps(
        [item.to_dict() for item in results],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_report(selected: list[str] | None = None) -> dict:
    keys = selected or list(SCENARIOS)
    results = []
    for key in keys:
        try:
            results.append(SCENARIOS[key]())
        except Exception as error:  # noqa: BLE001 - a crash is a NOT_VERIFIED scenario
            results.append(CaseResult(
                scenario=key,
                title=SCENARIO_TITLES.get(key, key),
                engineering_rule="Сценарий должен выполняться без исключений.",
                expected="Сценарий завершается и возвращает критерий.",
                actual=f"Исключение: {type(error).__name__}: {error}",
                criterion="нет исключения",
                status=NOT_VERIFIED,
            ))
    passed = [item for item in results if item.status == PASS]
    failed = [item for item in results if item.status == FAIL]
    unverified = [item for item in results if item.status == NOT_VERIFIED]
    if failed:
        verdict = FAIL
    elif unverified:
        verdict = NOT_VERIFIED
    else:
        verdict = PASS
    return {
        "kind": KIND,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": _source_sha(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "synthetic_only": True,
        "verdict": verdict,
        "counts": {"total": len(results), "passed": len(passed),
                   "failed": len(failed), "not_verified": len(unverified)},
        "verdict_digest": _digest(results),
        "scenarios": [item.to_dict() for item in results],
        "windows11": "NOT VERIFIED (Linux environment; use docs/kd-pe-106-windows11-test-plan.md)",
        "real_documents": "NOT VERIFIED (no enterprise E3/PE3 bundle available)",
        "multi_pc": "NOT VERIFIED (see docs/kd-pe-106-known-limitations.md)",
    }


def exit_code(report: dict) -> int:
    if report["counts"]["failed"]:
        return 1
    if report["counts"]["not_verified"]:
        return 2
    return 0


def _print_summary(report: dict) -> None:
    print(f"KD-PE-106 acceptance stand: {report['verdict']} "
          f"({report['counts']['passed']} passed, {report['counts']['failed']} failed, "
          f"{report['counts']['not_verified']} not verified)")
    print(f"source_sha={report['source_sha']} platform={report['platform']} python={report['python']}")
    for item in report["scenarios"]:
        print(f"  [{item['status']:>12}] {item['scenario']} {item['title']}")
        print(f"               ожидалось: {item['expected']}")
        print(f"               фактически: {item['actual']}")
        if item.get("limitation"):
            print(f"               ограничение: {item['limitation']}")
    print(f"windows11: {report['windows11']}")
    print(f"real_documents: {report['real_documents']}")
    print(f"verdict_digest: {report['verdict_digest']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KD-PE-106 synthetic engineering acceptance stand")
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS),
                        help="Run only the selected scenario(s); repeatable.")
    parser.add_argument("--json", type=Path, help="Write a machine-readable report to this path.")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit.")
    args = parser.parse_args(argv)

    if args.list:
        for key, title in SCENARIO_TITLES.items():
            print(f"{key}: {title}")
        return 0

    report = build_report(args.scenario)
    _print_summary(report)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        print(f"report: {args.json}")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
