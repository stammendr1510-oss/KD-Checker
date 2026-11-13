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
    raw = raw.replace('\n','').replace(' ', '').replace('…','...')
    out, unknown = set(), []
    for part in raw.split(';'):
        part = normalized(part)
        if not part: continue
        if '...' not in part:
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

def get_spec_rows(pdf_bytes):
    """Read designator column and qty using original PDF word coordinates.
    Only rows with an identifiable numeric qty and designator are accepted.
    """
    rows=[]
    with fitz.open(stream=pdf_bytes, filetype='pdf') as pdf:
        for pi, page in enumerate(pdf):
            words=page.get_text('words',sort=True)
            qty=[w for w in words if page.rect.width*.70 < w[0] < page.rect.width*.79 and re.fullmatch(r'\d+',w[4]) and w[1]<page.rect.height*.83]
            # Some tables use different x alignment, but the supplied sample uses 0.73W.
            qty.sort(key=lambda w:w[1]); previous=50
            for w in qty:
                y=w[1]
                des=[t for t in words if page.rect.width*.09 <= t[0] < page.rect.width*.188 and previous-2 <= t[1] <= y+8 and t[1]<page.rect.height*.83]
                previous=y+9
                raw=''.join(t[4] for t in sorted(des,key=lambda t:(round(t[1]/6),t[0])))
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
            findings.append(issue('info','KD-PE-102',pe_filename,f"Лист {r['page']}",f"Для дополнительной проверки: количество {r['quantity']} отличается от числа распознанных обозначений ({len(r['entries'])}) в записи «{r['source']}»",'Это не подтверждённая ошибка: проверьте исходную таблицу и переносы строк'))
    for code,forms in sorted(script_candidates.items()):
        if len(forms)>1 and any(any('А'<=c<='я' for c in s) for s in forms):
            findings.append(issue('warning','KD-PE-103',e3_filename,'Обозначения в Э3',f"Смешение похожих латинских и кириллических букв: {', '.join(sorted(forms))}",'Унифицируйте алфавит в исходном проекте; проверьте, не разные ли это объекты'))
    overlap=sorted(spec & scheme)
    findings.append(issue('info','KD-PE-100',pe_filename,'Сопоставление Э3 ↔ ПЭ3',f'Из ПЭ3 извлечено {len(rows)} строк с обозначениями и количеством; уникальных обозначений — {len(spec)}. Из текста Э3 распознано {len(scheme)} обозначений; совпадений — {len(overlap)}.','Это выборочная машинная сверка: отсутствие замечаний не подтверждает полноту схем и перечня'))
    missing=sorted(spec-scheme)
    if missing:
        findings.append(issue('info','KD-PE-101',pe_filename,'Ограничения анализа',f'{len(missing)} обозначений ПЭ3 не подтверждены по извлечённому тексту Э3 (примеры: {", ".join(missing[:8])}). Это не считается ошибкой КД.','Для достоверной проверки использовать структурированный экспорт проекта EPLAN или проверить графику вручную'))
    return findings
