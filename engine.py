"""Deterministic, editable rules; no AI or network services."""
from __future__ import annotations
import re
from collections import defaultdict
from pathlib import Path
from extractors import Document
from document_reference import has_document_reference

SEVERITY = {"error": "Ошибка", "warning": "Предупреждение", "info": "Информация"}


def issue(level: str, code: str, file: str, location: str, message: str, action: str) -> dict:
    return {"severity": level, "code": code, "file": file, "location": location, "message": message, "recommendation": action}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def header_mapping(rows, required):
    for row_num, row in enumerate(rows[:15], 1):
        indexes = {norm(v): i for i, v in enumerate(row)}
        if all(h in indexes for h in required):
            return row_num, indexes
    return None, {}


def analyze(docs: list[Document], cfg: dict) -> list[dict]:
    out = []
    pattern = re.compile(cfg["designation_pattern"], re.IGNORECASE)
    designations = defaultdict(list)
    for d in docs:
        base = Path(d.name).stem
        match = pattern.search(base)
        designation = match.group(0).casefold() if match else ""
        if cfg["require_designation_in_filename"] and not match:
            out.append(issue("warning", "KD-001", d.name, "Имя файла", "В имени файла не найдено обозначение по заданному шаблону", "Проверьте имя или измените шаблон в настройках"))
        if designation:
            designations[designation].append(d.name)
        if d.ext == ".pdf" and cfg["require_pdf_text"] and len(d.text.strip()) < cfg["min_pdf_text_chars"]:
            out.append(issue("warning", "KD-002", d.name, "PDF", "Мало извлекаемого текста: вероятно, скан или пустой документ", "Проверьте документ вручную или выполните OCR отдельно"))
        if d.ext == ".dxf" and cfg["warn_empty_dxf"] and not d.entities:
            out.append(issue("warning", "KD-003", d.name, "Modelspace DXF", "Нет объектов в пространстве модели", "Проверьте содержимое листов и пространства модели"))
        if d.ext == ".dwg":
            out.append(issue("info", "KD-004", d.name, "DWG", "Прямой анализ геометрии DWG не реализован", "Экспортируйте DWG в DXF/PDF для содержательной проверки"))
        for note in d.notes:
            if d.ext != ".dwg":
                out.append(issue("info", "KD-005", d.name, "Ограничение", note, "При необходимости разделите документ на части"))
        if d.ext in (".xlsx", ".xlsm", ".csv"):
            required = [norm(x) for x in cfg["specification_headers"]]
            for sheet in d.sheets:
                row_num, mapping = header_mapping(sheet["rows"], required)
                if row_num is None:
                    if cfg["require_excel_headers"]:
                        out.append(issue("warning", "KD-010", d.name, sheet["name"], "Не найдены заголовки спецификации: " + ", ".join(cfg["specification_headers"]), "Проверьте шапку таблицы или измените список столбцов"))
                    continue
                pos_col = mapping.get("позиция")
                des_col = mapping.get("обозначение")
                seen_pos = {}
                for ridx, row in enumerate(sheet["rows"][row_num:], row_num + 1):
                    pos = row[pos_col].strip() if pos_col is not None and pos_col < len(row) else ""
                    des = row[des_col].strip() if des_col is not None and des_col < len(row) else ""
                    if not any(v.strip() for v in row):
                        continue
                    location = f"{sheet['name']}!{ridx}"
                    if pos and cfg["check_duplicate_positions"]:
                        key = norm(pos)
                        if key in seen_pos:
                            out.append(issue("error", "KD-011", d.name, location, f"Повтор позиции «{pos}» (первая: строка {seen_pos[key]})", "Уточните номера позиций"))
                        else:
                            seen_pos[key] = ridx
                    # Heuristic only: a designation is not necessarily an attached file.
                    if des and cfg["check_specification_references"] and not has_document_reference(des, (other.name for other in docs if other.name != d.name)):
                        out.append(issue("warning", "KD-012", d.name, location, f"Не найден файл с обозначением «{des}»", "Проверьте комплектность; стандартные изделия могут не требовать отдельного файла"))
    if cfg["detect_duplicate_designations"]:
        for des, names in designations.items():
            # Same designation across different output formats is normal.
            by_ext = defaultdict(list)
            for name in names:
                by_ext[Path(name).suffix.lower()].append(name)
            for ext, subset in by_ext.items():
                if len(subset) > 1:
                    for name in subset:
                        out.append(issue("warning", "KD-020", name, "Обозначение", f"Повтор обозначения «{des}» в формате {ext}: {', '.join(subset[:5])}", "Проверьте версии, дубликаты и имена файлов"))
    return out
