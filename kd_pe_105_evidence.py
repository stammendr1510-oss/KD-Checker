"""Human-readable evidence view for KD-PE-105 (block 5).

The module is deliberately pure: it receives an already stored finding and
turns its schema v1 ``details`` into an engineer-facing explanation.  It never
opens PDF files, never calls the network and never writes data.

The stored schema is **not extended**.  The view is derived on demand for the
API, the workbench and the Excel export, so old reports keep working and the
six-column CSV contract is untouched.
"""
from __future__ import annotations

import re

MAX_DETECTED = 50
MAX_ITEMS = 200
MAX_AMBIGUOUS = 50
MAX_CONTEXT_ITEMS = 3
MAX_TEXT = 30000
MAX_TEXT_DETECTED = 20
MAX_TEXT_ITEMS = 20

STATUS_LABELS = {
    "evaluated": "проверено",
    "not_evaluated": "не проверено",
}

CONFIDENCE_LABELS = {
    "high": "высокая",
    "medium": "средняя",
    "low": "низкая",
}

KIND_LABELS = {
    "label": "явная метка",
    "split_label": "перенос или разрыв обозначения",
    "reference": "ссылка или вывод",
    "note": "примечание",
    "table": "табличный фрагмент",
    "unknown": "нераспознанный фрагмент",
}

REASON_LABELS = {
    "quantity_mismatch": "количество в ПЭ3 не совпадает с раскрытыми обозначениями",
    "row_matched_without_coverage": (
        "строка ПЭ3 похожа по типу и группе, но не доказывает принадлежность обозначения"
    ),
    "row_ambiguous": "несколько строк ПЭ3 одинаково подходят; выбор строки не выполняется",
    "no_matching_pe3_row": "не найдена строка ПЭ3 с подходящим типом",
    "reference_only": "обозначения встречены только в ссылках или примечаниях",
    "no_text_layer": "в PDF Э3 не обнаружен текстовый слой",
    "pe3_not_parsed": "позиционные обозначения ПЭ3 не извлечены достоверно",
    "truncated": "обработка PDF ограничена",
    "needs_context": "обозначение неоднозначно; требуется дополнительный контекст",
    "ambiguous_row": "устаревшая причина: неоднозначная строка ПЭ3",
}

REASON_EXPLANATIONS = {
    "quantity_mismatch": (
        "В строке ПЭ3 указано больше позиций, чем раскрыто обозначений. "
        "Обозначения из Э3 не входят в раскрытый перечень этой строки."
    ),
    "row_matched_without_coverage": (
        "Найдена единственная строка ПЭ3 того же семейства и группы, но она "
        "не содержит отсутствующие обозначения и не подтверждает их диапазон. "
        "Совпадение типа само по себе не доказывает принадлежность."
    ),
    "row_ambiguous": (
        "Найдено несколько строк-кандидатов с одинаковыми признаками. "
        "Автоматический выбор одной строки создал бы ложное доказательство, "
        "поэтому строка не выбрана."
    ),
    "no_matching_pe3_row": (
        "Среди извлечённых строк ПЭ3 нет строки с подходящим семейством и "
        "группой. Это ограничение текстового сопоставления, а не доказательство "
        "физического отсутствия изделия."
    ),
    "reference_only": (
        "Обозначение классифицировано как вывод, контакт, конец соединения или "
        "перекрёстная ссылка. Само по себе оно не является отдельным изделием "
        "для проверки наличия в ПЭ3."
    ),
    "no_text_layer": (
        "Без текстового слоя или OCR сопоставление обозначений Э3 и ПЭ3 "
        "невозможно. Отсутствие обозначений в ПЭ3 не утверждается."
    ),
    "pe3_not_parsed": (
        "Строки ПЭ3 не дали пригодных позиционных обозначений. "
        "Отсутствие обозначений в ПЭ3 не утверждается."
    ),
    "truncated": (
        "Часть страниц или строк не обработана, поэтому вывод неполный. "
        "Недостающие данные не считаются отсутствующими в документе."
    ),
    "needs_context": (
        "Доступного контекста недостаточно, чтобы отнести обозначение к "
        "самостоятельному изделию. Требуется ручная проверка по исходному Э3."
    ),
    "ambiguous_row": (
        "Запись создана старой версией проверки. Трактуйте её как "
        "предположительное совпадение, а не как подтверждённую ошибку."
    ),
}

REASON_ACTIONS = {
    "quantity_mismatch": (
        "Сверьте количество и раскрытые обозначения в указанной строке ПЭ3; "
        "при подтверждении дополните перечень позиционных обозначений."
    ),
    "row_matched_without_coverage": (
        "Откройте указанную строку ПЭ3 и проверьте, входит ли обозначение в её "
        "диапазон. Не изменяйте документы автоматически."
    ),
    "row_ambiguous": (
        "Сопоставьте обозначение с конкретной строкой ПЭ3 вручную: "
        "автоматический выбор строки не выполнен."
    ),
    "no_matching_pe3_row": (
        "Проверьте, есть ли подходящая строка ПЭ3 в исходном документе. "
        "При необходимости используйте структурированную выгрузку проекта."
    ),
    "reference_only": (
        "Проверьте, является ли обозначение самостоятельным изделием или "
        "выводом, контактом, клеммой либо перекрёстной ссылкой."
    ),
    "no_text_layer": "Предоставьте PDF с текстовым слоем или выполните OCR.",
    "pe3_not_parsed": (
        "Предоставьте ПЭ3 с распознаваемой таблицей или структурированную "
        "выгрузку перечня элементов."
    ),
    "truncated": "Повторите проверку с полным набором страниц и документов.",
    "needs_context": "Уточните контекст обозначения по исходному листу Э3.",
}


def _clean(value, limit=300):
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _pages_text(pages):
    ordered = [page for page in pages or [] if type(page) is int and page > 0]
    return ", ".join(str(page) for page in ordered)


def _kinds_text(kinds):
    labels = [KIND_LABELS.get(kind, kind) for kind in kinds or []]
    return ", ".join(labels)


def _candidate_label(page, table_row):
    if type(page) is not int or page < 1:
        return ""
    if type(table_row) is int and table_row > 0:
        return f"лист PDF {page}, строка таблицы {table_row}"
    return f"лист PDF {page} (строка не подтверждена)"


def _candidate_entries(issue, details):
    """Return ``(candidate_labels, candidate_count)``.

    Current schema v1 does not store candidate rows, so the producer adds a
    bounded human-readable line to ``message``/``location``.  If a future
    report stores ``details["candidates"]``, it is preferred.
    """
    labels = []
    raw = details.get("candidates")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                label = _candidate_label(item.get("page"), item.get("table_row"))
                if label:
                    labels.append(label)
            elif isinstance(item, str) and item.strip():
                labels.append(_clean(item, 200))
    text = " ".join((str(issue.get("message") or ""), str(issue.get("location") or "")))
    if not labels:
        # ``_finding`` normalises whitespace, so the producer line may not be
        # separated by a newline any more.  Match it inside the whole text.
        match = re.search(
            r"(?:Кандидаты ПЭ3|кандидаты):\s*(.+?)(?:\.(?:\s|$)|$)",
            text,
            re.I,
        )
        if match:
            raw_line = match.group(1).strip().rstrip(".")
            labels.extend(part.strip() for part in raw_line.split(";") if part.strip())
    unique = []
    for label in labels:
        if label and label not in unique:
            unique.append(label)
    match = re.search(r"кандидатов:\s*(\d+)", text, re.I)
    count = int(match.group(1)) if match else len(unique)
    return unique, max(count, len(unique))


def _classification(details, missing, ambiguous):
    """Explain whether the result is about a product, a contact or an open case."""
    reason = details.get("reason")
    family = _clean(details.get("type_family"), 20)
    terminal_families = {"X", "XT", "XN", "XL", "XG"}
    if reason == "reference_only":
        return {
            "result_kind": "contact_or_terminal",
            "label": "Вывод, контакт, клемма или ссылка — не отдельное изделие",
            "note": (
                "Такое обозначение может подтверждать существование устройства, "
                "но само по себе не является позицией перечня элементов."
            ),
        }
    if reason in ("needs_context", "ambiguous_row") or (ambiguous and not missing):
        return {
            "result_kind": "undefined",
            "label": "Неопределённый случай: недостаточно контекста",
            "note": (
                "Классификация не позволяет надёжно отнести обозначение к изделию, "
                "контакту или клемме. Требуется ручная проверка по Э3."
            ),
        }
    if missing:
        note = ""
        if family in terminal_families:
            note = (
                "Семейство обозначений относится к клеммникам или соединениям; "
                "результат носит информационный характер."
            )
        return {
            "result_kind": "product",
            "label": "Самостоятельное изделие — проверяется наличие в ПЭ3",
            "note": note,
        }
    return {
        "result_kind": "undefined",
        "label": "Неопределённый случай: нет подтверждённых обозначений",
        "note": "",
    }


def _confidence_basis(details):
    reason = details.get("reason")
    confidence = details.get("confidence")
    basis = []
    if details.get("partial") or details.get("truncated"):
        basis.append(
            "Анализ неполный: часть страниц или строк не обработана, "
            "поэтому категоричный вывод недопустим."
        )
    if reason == "quantity_mismatch":
        quantity = details.get("pe3_quantity")
        entries = details.get("pe3_entries_count")
        if isinstance(quantity, int) and isinstance(entries, int) and quantity > entries:
            basis.append(
                f"Количество в строке ПЭ3 ({quantity}) больше числа раскрытых "
                f"обозначений ({entries})."
            )
        if details.get("pe3_page") is not None:
            basis.append("Строка ПЭ3 подтверждена листом и, при наличии сетки, номером строки.")
        if confidence == "high":
            basis.append("Все обязательные доказательства достаточны для высокой достоверности.")
        elif confidence == "medium":
            basis.append(
                "Одно из обязательных доказательств ограничено: качество извлечения "
                "Э3/ПЭ3 или точность привязки строки. Поэтому достоверность средняя."
            )
        else:
            basis.append(
                "Есть ограничение полноты или извлечения, поэтому достоверность низкая."
            )
    elif reason == "row_matched_without_coverage":
        basis.append(
            "Строка ПЭ3 совпадает только по семейству и группе; принадлежность "
            "отсутствующих обозначений не доказана."
        )
        basis.append("Достоверность ограничена средней: нет подтверждения диапазона или количества.")
    elif reason == "row_ambiguous":
        basis.append(
            "Несколько строк ПЭ3 одинаково подходят; автоматический выбор не выполнен."
        )
        basis.append("Достоверность низкая: единственная строка-источник не определена.")
    elif reason == "no_matching_pe3_row":
        basis.append("Не найдена строка ПЭ3 с подходящим семейством и группой.")
        basis.append(
            "Количество отсутствующих обозначений не повышает достоверность; "
            "она ограничена средней."
        )
    elif reason in ("reference_only", "no_text_layer", "pe3_not_parsed", "truncated"):
        basis.append("Проверка не выполнена или выполнена частично; итоговая достоверность низкая.")
    elif reason == "needs_context":
        basis.append("Классификация обозначения неоднозначна; достоверность низкая.")
    elif reason == "ambiguous_row":
        basis.append("Устаревшая запись; достоверность нельзя считать подтверждённой.")
    else:
        basis.append("Достоверность определена по сохранённым признакам замечания.")

    kinds = set()
    for item in details.get("missing") or []:
        if isinstance(item, dict):
            kinds.update(item.get("kinds") or [])
    if kinds:
        weak = kinds & {"split_label", "reference", "note", "table", "unknown"}
        if weak:
            basis.append(
                "Среди доказательств есть обозначения, извлечённые как перенос, "
                "ссылка или табличный фрагмент, а не как явная метка."
            )
        elif "label" in kinds:
            basis.append("Обозначения извлечены как явные метки Э3.")
    return basis


def _what_is_missing(details):
    reason = details.get("reason")
    result = []
    if details.get("partial") or details.get("truncated"):
        result.append("Не хватает полной обработки всех страниц и строк документов.")
    if reason == "row_ambiguous":
        result.append(
            "Не хватает единственной подтверждённой строки ПЭ3: несколько строк "
            "одинаково подходят, поэтому автоматический выбор невозможен."
        )
    elif reason == "row_matched_without_coverage":
        result.append(
            "Не хватает доказательства принадлежности: совпали только тип и группа, "
            "нет подтверждения диапазона или количества."
        )
    elif reason == "no_matching_pe3_row":
        result.append(
            "Не хватает строки ПЭ3 с подходящим семейством и группой; отсутствие "
            "строки не доказывает отсутствие изделия."
        )
    elif reason == "reference_only":
        result.append(
            "Не хватает подтверждения, что обозначение является самостоятельным "
            "изделием, а не выводом, контактом, клеммой или ссылкой."
        )
    elif reason == "no_text_layer":
        result.append("Не хватает текстового слоя PDF или OCR.")
    elif reason == "pe3_not_parsed":
        result.append("Не хватает достоверно извлечённых обозначений и строк ПЭ3.")
    elif reason == "truncated":
        result.append("Не хватает полного набора страниц и документов.")
    elif reason == "needs_context":
        result.append("Не хватает контекста для классификации обозначения.")
    elif reason == "ambiguous_row":
        result.append("Не хватает однозначной строки ПЭ3 по правилам текущей проверки.")

    if details.get("pe3_page") is not None and details.get("pe3_table_row") is None:
        result.append(
            "Не хватает подтверждённой сетки PDF для номера строки таблицы; "
            "показан только лист ПЭ3."
        )
    if details.get("pe3_page") is None:
        result.append("Не хватает подтверждённой строки ПЭ3.")
    ambiguous = details.get("ambiguous") or []
    if ambiguous:
        result.append(
            "Не хватает контекста для неоднозначных обозначений: "
            + ", ".join(str(item.get("designation", "")) for item in ambiguous[:MAX_AMBIGUOUS])
            + "."
        )
    return result


def _recommendations(issue, details):
    result = []
    recommendation = _clean(issue.get("recommendation"), 4000)
    if recommendation:
        result.append(recommendation)
    action = REASON_ACTIONS.get(details.get("reason"))
    if action and action not in result:
        result.append(action)
    if details.get("status") == "not_evaluated":
        result.append(
            "Решение о необходимости изменений принимает инженер; "
            "автоматическое изменение документов не выполняется."
        )
    return result


def _items(details):
    items = []
    for item in (details.get("missing") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") or []
        contexts = []
        for entry in evidence[:MAX_CONTEXT_ITEMS]:
            if isinstance(entry, dict):
                context = _clean(entry.get("context"), 300)
                if context and context not in contexts:
                    contexts.append(context)
        items.append({
            "designation": _clean(item.get("designation"), 100),
            "raw": _clean(item.get("raw"), 100),
            "pages": [page for page in (item.get("pages") or []) if type(page) is int and page > 0],
            "pages_text": _pages_text(item.get("pages")),
            "kinds": list(item.get("kinds") or []),
            "kinds_text": _kinds_text(item.get("kinds")),
            "contexts": contexts,
            "evidence_count": len(evidence),
        })
    return items


def _ambiguous_items(details):
    items = []
    for item in (details.get("ambiguous") or [])[:MAX_AMBIGUOUS]:
        if not isinstance(item, dict):
            continue
        reason = item.get("reason") or ""
        items.append({
            "designation": _clean(item.get("designation"), 100),
            "pages": [page for page in (item.get("pages") or []) if type(page) is int and page > 0],
            "pages_text": _pages_text(item.get("pages")),
            "kinds": list(item.get("kinds") or []),
            "kinds_text": _kinds_text(item.get("kinds")),
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
        })
    return items


def build_evidence_view(issue):
    """Return a JSON-serialisable engineer-facing view, or ``None``.

    The view intentionally contains no PDF coordinates.  Only a page number and
    a grid-confirmed table row are exposed; both come from the validated
    ``details`` source.
    """
    if not isinstance(issue, dict):
        return None
    details = issue.get("details")
    if not isinstance(details, dict) or details.get("kind") != "e3_pe3_missing_designations":
        return None

    reason = details.get("reason") or ""
    confidence = details.get("confidence") or "low"
    status = details.get("status") or ""
    missing = details.get("missing") or []
    detected = [
        _clean(item.get("designation"), 100)
        for item in missing[:MAX_DETECTED]
        if isinstance(item, dict) and item.get("designation")
    ]
    items = _items(details)
    ambiguous = _ambiguous_items(details)
    candidates, candidate_count = _candidate_entries(issue, details)
    notes = []
    if len(missing) > MAX_DETECTED:
        notes.append(
            f"Показаны первые {MAX_DETECTED} обозначений из {len(missing)}; "
            "полный список — в Excel."
        )
    if details.get("pe3_page") is not None and details.get("pe3_table_row") is None:
        notes.append("Строка таблицы ПЭ3 не подтверждена сеткой PDF; показан только лист.")
    if details.get("pe3_page") is None:
        notes.append("Строка ПЭ3 не подтверждена источником.")
    if (details.get("limits") or {}).get("truncated_list"):
        notes.append("Список обозначений усечён по ограничению размера; полный список ограничен.")
    notes.append("Показаны только подтверждённые лист и строка; координаты PDF не отображаются.")

    pe3 = {
        "document": _clean(details.get("pe3_document"), 1000),
        "page": details.get("pe3_page"),
        "table_row": details.get("pe3_table_row"),
        "row_confirmed": details.get("pe3_table_row") is not None,
        "quantity": details.get("pe3_quantity"),
        "entries_count": details.get("pe3_entries_count"),
        "name": _clean(details.get("pe3_name"), 300),
        "source": _clean(details.get("pe3_source"), 300),
    }
    e3 = {
        "document": _clean(details.get("e3_document"), 1000),
        "pages": [page for page in (details.get("e3_pages") or []) if type(page) is int and page > 0],
        "pages_text": _pages_text(details.get("e3_pages")),
    }
    classification = _classification(details, missing, ambiguous)
    summary = (
        f"{REASON_LABELS.get(reason, reason)}; достоверность "
        f"{CONFIDENCE_LABELS.get(confidence, confidence)}."
    )
    return {
        "kind": "e3_pe3_evidence",
        "version": 1,
        "available": True,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "reason": reason,
        "reason_label": REASON_LABELS.get(reason, reason),
        "reason_explanation": REASON_EXPLANATIONS.get(reason, ""),
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS.get(confidence, confidence),
        "type_family": _clean(details.get("type_family"), 20),
        "type_name": _clean(details.get("type_name"), 120),
        "classification": classification,
        "confidence_basis": _confidence_basis(details),
        "what_is_missing": _what_is_missing(details),
        "recommendations": _recommendations(issue, details),
        "detected": detected,
        "detected_count": len(missing),
        "detected_note": notes[0] if len(missing) > MAX_DETECTED else "",
        "e3": e3,
        "pe3": pe3,
        "candidates": candidates,
        "candidate_count": candidate_count,
        "candidates_note": (
            "Список строк-кандидатов ограничен; полный перечень — в исходной таблице ПЭ3."
            if candidates else ""
        ),
        "items": items,
        "ambiguous": ambiguous,
        "partial": bool(details.get("partial")),
        "truncated": bool(details.get("truncated")),
        "limits": dict(details.get("limits") or {}),
        "notes": notes,
        "summary": summary,
    }


def enrich_issue(issue):
    """Return a copy of ``issue`` with ``evidence_view`` when applicable."""
    view = build_evidence_view(issue)
    if view is None:
        return issue
    result = dict(issue)
    result["evidence_view"] = view
    return result


def enrich_issues(issues):
    return [enrich_issue(issue) for issue in issues]


def enrich_run(record):
    """Return a copy of a saved run with evidence views on its findings."""
    if not isinstance(record, dict):
        return record
    issues = record.get("issues")
    if not isinstance(issues, list):
        return record
    result = dict(record)
    result["issues"] = enrich_issues(issues)
    return result


def format_evidence_text(issue):
    """Human-readable evidence block for the Excel export.

    Coordinates and internal JSON are never rendered here.  The structured JSON
    remains available in its own Excel column.
    """
    view = build_evidence_view(issue)
    if view is None:
        return ""

    lines = ["Доказательства KD-PE-105"]
    lines.append(
        f"Статус: {view['status_label']}; достоверность: {view['confidence_label']}."
    )
    detected = view["detected"]
    shown_detected = detected[:MAX_TEXT_DETECTED]
    if shown_detected:
        lines.append(
            f"Обнаруженные обозначения ({view['detected_count']}): "
            + ", ".join(shown_detected)
            + ("." if view["detected_count"] <= len(shown_detected) else " …")
        )
        if view["detected_count"] > len(shown_detected):
            lines.append(
                f"Показаны первые {len(shown_detected)} из {view['detected_count']}; "
                "полный список — в Excel."
            )
    else:
        lines.append("Подтверждённых отсутствующих обозначений нет.")

    e3 = view["e3"]
    lines.append(f"Документ Э3: {e3['document'] or 'не указан'}; листы PDF: {e3['pages_text'] or 'не установлены'}.")
    pe3 = view["pe3"]
    pe3_place = pe3["document"] or "не указан"
    if pe3["page"] is not None:
        pe3_place += f"; лист PDF {pe3['page']}"
        if pe3["table_row"] is not None:
            pe3_place += f"; строка таблицы {pe3['table_row']}"
        else:
            pe3_place += "; строка таблицы не подтверждена"
    else:
        pe3_place += "; строка не сопоставлена"
    lines.append(f"Документ ПЭ3: {pe3_place}.")
    if pe3["quantity"] is not None or pe3["entries_count"] is not None:
        lines.append(
            f"Количество в ПЭ3: {pe3['quantity'] if pe3['quantity'] is not None else '—'}; "
            f"раскрыто обозначений: {pe3['entries_count'] if pe3['entries_count'] is not None else '—'}."
        )
    if pe3["name"]:
        lines.append(f"Наименование позиции ПЭ3: {pe3['name']}.")

    if view["type_family"]:
        lines.append(
            f"Тип обозначения: {view['type_family']}"
            + (f" ({view['type_name']})" if view["type_name"] else "")
            + "."
        )
    lines.append(f"Классификация результата: {view['classification']['label']}.")
    if view["classification"].get("note"):
        lines.append(view["classification"]["note"])
    lines.append(f"Характер расхождения: {view['reason_label']}.")
    if view["reason_explanation"]:
        lines.append(view["reason_explanation"])
    lines.append(f"Почему такая достоверность ({view['confidence_label']}):")
    for item in view["confidence_basis"]:
        lines.append(f"- {item}")
    if view["what_is_missing"]:
        lines.append("Чего не хватило для достоверного вывода:")
        for item in view["what_is_missing"]:
            lines.append(f"- {item}")
    if view["candidates"]:
        lines.append(f"Кандидаты ПЭ3 ({view['candidate_count']}):")
        for candidate in view["candidates"]:
            lines.append(f"- {candidate}")
    elif view["candidate_count"]:
        lines.append(
            f"Кандидаты ПЭ3: {view['candidate_count']}; список строк не сохранён в details v1."
        )
    lines.append("Рекомендации инженеру:")
    for item in view["recommendations"]:
        lines.append(f"- {item}")

    if view["items"]:
        lines.append("Доказательства по обозначениям:")
        shown_items = view["items"][:MAX_TEXT_ITEMS]
        for item in shown_items:
            details = []
            if item["pages_text"]:
                details.append(f"листы PDF {item['pages_text']}")
            if item["kinds_text"]:
                details.append(f"контекст: {item['kinds_text']}")
            if item["contexts"]:
                details.append("фрагменты: " + " | ".join(item["contexts"]))
            suffix = "; ".join(details)
            lines.append(f"- {item['designation']}" + (f": {suffix}" if suffix else ""))
        if len(shown_items) < view["detected_count"]:
            lines.append(f"Показаны первые {len(shown_items)} из {view['detected_count']}.")
    if view["ambiguous"]:
        lines.append("Неоднозначные обозначения:")
        for item in view["ambiguous"]:
            reason = f" ({item['reason_label']})" if item["reason_label"] else ""
            lines.append(f"- {item['designation']}{reason}")
    if view["notes"]:
        lines.append("Примечания:")
        for item in view["notes"]:
            lines.append(f"- {item}")
    return "\n".join(lines)[:MAX_TEXT]
