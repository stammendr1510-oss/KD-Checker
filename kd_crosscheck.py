"""Experimental, conservative E3 / PE3 PDF cross-check. No AI or network."""
from __future__ import annotations
import re
from collections import defaultdict
from pathlib import Path
import fitz

# Identify components in the electrical scheme primarily from unambiguous wire tags.
TAG = re.compile(r'(?<![\w.])(?:[123])?(?:QF|SF|KM|KK|KV|KT|TA|SB|HLG|HLW|XG|XT|XN|XL|X|PAV)\d+(?:\.\d+)?(?=\s*:|\b)', re.I)
RANGE = re.compile(r'^([\d]*[A-ZА-Я]+)(\d+)\.{2,3}([\d]*[A-ZА-Я]+)?(\d+)$', re.I)
CYR_LOOKALIKES = str.maketrans('АВСЕНКМОРТХУавсенкмортху', 'ABCEHKMOPTXYaBcehkmoptxy')

def normalized(s):
    # Normalization is used only for *matching*, not silently rewriting source files.
    return s.translate(CYR_LOOKALIKES).upper().strip().replace(' ', '')

def designation_type(name):
    m = re.search(r'\.((?:ПЭ3)|(?:Э3))(?=\s|\.|$)', Path(name).stem, re.I)
    return m.group(1).upper() if m else None

def pair_key(name):
    return re.sub(r'\.(?:ПЭ3|Э3)(?=\s|\.|$)', '.', Path(name).stem, flags=re.I).strip('. ').casefold()

def issue(level, code, file, place, message, recommendation):
    return {'severity':level, 'code':code, 'file':file, 'location':place, 'message':message, 'recommendation':recommendation}

def expand_notation(raw):
    raw = re.sub(r'\s+', '', raw).replace('…','...')
    out, unknown = set(), []
    for part in raw.split(';'):
        part = normalized(part)
        if not part: continue
        if '..' not in part:
            if TAG.fullmatch(part): out.add(part)
            else: unknown.append(part)
            continue
        m = RANGE.fullmatch(part)
        if not m:
            unknown.append(part); continue
        left, first, right, last = m.groups()
        if right and right != left or int(last)<int(first) or int(last)-int(first)>100:
            unknown.append(part); continue
        out.update(left+str(i) for i in range(int(first),int(last)+1))
    return out, unknown

def _row_rules(page, column_left):
    """Horizontal rules crossing the designator column (PDF points)."""
    left, right = column_left, page.rect.width*.188
    rules = set()
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.y-b.y) < .5 and min(a.x,b.x) <= left and max(a.x,b.x) >= right:
                    rules.add((a.y+b.y)/2)
            elif item[0] == 're':
                rect = item[1]
                if rect.x0 <= left and rect.x1 >= right:
                    rules.update((rect.y0, rect.y1))
    return sorted(rules)

def _quantity_words(page, words):
    """Use the quantity header's vertical borders to exclude numeric notes."""
    left, right = page.rect.width*.70, page.rect.width*.79
    headers = [w for w in words if left < w[0] < right
               and w[1] < page.rect.height*.1 and w[4].casefold().rstrip('.') == 'кол']
    if len(headers) == 1:
        header = headers[0]
        cx, cy = (header[0]+header[2])/2, (header[1]+header[3])/2
        borders = set()
        for drawing in page.get_drawings():
            for item in drawing['items']:
                if item[0] == 'l':
                    a, b = item[1:3]
                    if abs(a.x-b.x) < .5 and min(a.y,b.y) <= cy <= max(a.y,b.y):
                        borders.add((a.x+b.x)/2)
                elif item[0] == 're':
                    rect = item[1]
                    if rect.y0 <= cy <= rect.y1:
                        borders.update((rect.x0, rect.x1))
        before, after = [x for x in borders if x < cx], [x for x in borders if x > cx]
        if before and after:
            left, right = max(before), min(after)
    candidates = sorted([w for w in words if left < w[0] < right and re.fullmatch(r'\d+', w[4])
                         and w[1] < page.rect.height*.83], key=lambda w:(w[1],w[0]))
    rows = []
    for word in candidates:
        if rows and abs(word[1]-rows[-1][1]) < 2:
            # With no usable header, the leftmost number is the quantity;
            # a second number to its right belongs to notes on the same row.
            rows[-1] = min(rows[-1], word, key=lambda w:w[0])
        else:
            rows.append(word)
    return rows

def _cell_words(words, quantities, index, rules, previous):
    qty = quantities[index]
    center = (qty[1]+qty[3])/2
    above = [y for y in rules if y < center]
    below = [y for y in rules if y > center]
    if above and below:
        top, bottom = above[-1], below[0]
        # A border around the whole table is not a row boundary.
        if sum(top < (q[1]+q[3])/2 < bottom for q in quantities) == 1:
            return [w for w in words if top < (w[1]+w[3])/2 < bottom], bottom

    # Without a grid, extend only within a PDF text block that intersects
    # this quantity and no other quantity. Never join arbitrary nearby lines.
    limit = min(qty[1]+8, quantities[index+1][1]-2) if index+1 < len(quantities) else qty[1]+8
    selected = [w for w in words if previous-2 <= w[1] <= limit]
    blocks = {w[5] for w in selected if w[1] <= center <= w[3]}
    for block in blocks:
        cell = [w for w in words if w[5] == block]
        top, bottom = min(w[1] for w in cell), max(w[3] for w in cell)
        if top >= previous-2 and sum(top <= (q[1]+q[3])/2 <= bottom for q in quantities) == 1:
            selected.extend(w for w in cell if w not in selected)
    last = max((w[1] for w in selected), default=qty[1])
    return selected, max(qty[1], last)+9

def get_spec_rows(pdf_bytes):
    """Read designator column and qty using original PDF word coordinates.
    Only rows with an identifiable numeric qty and designator are accepted.
    """
    rows=[]
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        for pi, page in enumerate(pdf):
            words=page.get_text('words',sort=True)
            qty = _quantity_words(page, words)
            previous=50
            column=[t for t in words if page.rect.width*.09 <= t[0] < page.rect.width*.188 and t[1]<page.rect.height*.83]
            rules = _row_rules(page, min((w[0] for w in column), default=page.rect.width*.09))
            for index, w in enumerate(qty):
                des, previous = _cell_words(column, qty, index, rules, previous)
                # PDF block/line identifiers preserve wrapped fragments without
                # rounding y coordinates into arbitrary six-point bands.
                lines = defaultdict(list)
                for t in des:
                    lines[(t[5],t[6])].append(t)
                ordered = sorted(lines.values(), key=lambda line:(min(t[1] for t in line), min(t[0] for t in line)))
                raw='\n'.join(''.join(t[4] for t in sorted(line,key=lambda t:t[0])) for line in ordered)
                entries,unknown=expand_notation(raw)
                if entries:
                    rows.append({'page':pi+1,'source':raw,'quantity':int(w[4]),'entries':entries,'unknown':unknown})
    return rows

def run(e3_filename,e3_bytes,pe_filename,pe_bytes):
    findings=[]
    rows=get_spec_rows(pe_bytes)
    if not rows:
        return [issue('warning','KD-PE-000',pe_filename,'PDF','Не удалось надёжно извлечь строки перечня элементов','Используйте структурированную выгрузку или Excel для точной сверки')]
    scheme=set(); script_candidates=defaultdict(set)
    with fitz.open(stream=e3_bytes, filetype='pdf') as pdf:
        for pi,page in enumerate(pdf):
            text=page.get_text()
            for m in TAG.finditer(text):
                value=m.group(0)
                n=normalized(value)
                scheme.add(n)
                script_candidates[n].add(value)
                if len(script_candidates[n])>1 and any(any('А'<=c<='я' for c in form) for form in script_candidates[n]):
                    pass
    spec=set().union(*(r['entries'] for r in rows))
    # No blanket "missing" reporting: drawings may contain auxiliary devices,
    # split representations and OCR/text-order artifacts.
    for r in rows:
        if not r['unknown'] and len(r['entries'])>1 and len(r['entries'])!=r['quantity']:
            findings.append(issue('warning','KD-PE-102',pe_filename,f"Лист {r['page']}",f"Для дополнительной проверки: количество {r['quantity']} отличается от числа распознанных обозначений ({len(r['entries'])}) в записи «{r['source']}»",'Требуется проверка инженером: сопоставьте количество и явно указанные обозначения в исходной строке ПЭ3, включая переносы и диапазоны. Это не подтверждённая ошибка КД.'))
    for code,forms in sorted(script_candidates.items()):
        if len(forms)>1 and any(any('А'<=c<='я' for c in s) for s in forms):
            findings.append(issue('warning','KD-PE-103',e3_filename,'Обозначения в Э3',f"Смешение похожих латинских и кириллических букв: {', '.join(sorted(forms))}",'Унифицируйте алфавит в исходном проекте; проверьте, не разные ли это объекты'))
    overlap=sorted(spec & scheme)
    findings.append(issue('info','KD-PE-100',pe_filename,'Сопоставление Э3 ↔ ПЭ3',f'Из ПЭ3 извлечено {len(rows)} строк с обозначениями и количеством; уникальных обозначений — {len(spec)}. Из текста Э3 распознано {len(scheme)} обозначений; совпадений — {len(overlap)}.','Для проверки полноты сопоставьте исходные Э3 и ПЭ3 или структурированный экспорт проекта. Это выборочная машинная сверка: отсутствие замечаний не подтверждает полноту схем и перечня'))
    missing=sorted(spec-scheme)
    if missing:
        findings.append(issue('info','KD-PE-101',pe_filename,'Ограничения анализа',f'{len(missing)} обозначений ПЭ3 не подтверждены по извлечённому тексту Э3 (примеры: {", ".join(missing[:8])}). Это не считается ошибкой КД.','Для достоверной проверки использовать структурированный экспорт проекта EPLAN или проверить графику вручную'))
    return findings

