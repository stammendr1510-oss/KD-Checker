"""Format readers. Extract only bounded information, never execute document content."""
from __future__ import annotations
import io
from itertools import islice
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED = {".pdf", ".xlsx", ".xlsm", ".csv", ".dxf", ".dwg"}

@dataclass
class Document:
    name: str
    ext: str
    text: str = ""
    pages: int = 0
    sheets: list = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    entities: int = 0
    notes: list[str] = field(default_factory=list)


def extract(name: str, content: bytes) -> Document:
    ext = Path(name).suffix.lower()
    d = Document(name=name, ext=ext)
    if ext == ".pdf":
        import fitz
        with fitz.open(stream=content, filetype="pdf") as pdf:
            d.pages = len(pdf)
            d.text = "\n".join(page.get_text()[:150_000] for page in islice(pdf, 100))[:300_000]
            if d.pages > 100:
                d.notes.append("Прочитаны только первые 100 страниц PDF")
    elif ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        try:
            for ws in wb.worksheets[:30]:
                rows = []
                for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, 5000), max_col=min(ws.max_column or 0, 40), values_only=True):
                    rows.append([str(x).strip() if x is not None else "" for x in row])
                d.sheets.append({"name": ws.title, "rows": rows})
            if len(wb.worksheets) > 30:
                d.notes.append("Проверены первые 30 листов Excel")
        finally:
            wb.close()
    elif ext == ".csv":
        import csv
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";"
        # Stop the parser at the record limit, before materializing the table.
        # CSV records can contain quoted newlines: do not slice physical lines.
        rows = csv.reader(io.StringIO(text), delimiter=delimiter)
        d.sheets = [{"name":"CSV", "rows":[r[:40] for r in islice(rows, 5000)]}]
    elif ext == ".dxf":
        import ezdxf
        doc = ezdxf.read(io.StringIO(content.decode("utf-8", errors="replace")))
        d.layers = [layer.dxf.name for layer in doc.layers]
        d.entities = sum(1 for _ in doc.modelspace())
    elif ext == ".dwg":
        d.notes.append("DWG: геометрия не анализируется; для проверки экспортируйте в DXF или PDF")
    return d
