"""Independent, upload-only EPLAN set inventory; never certifies project completeness."""
from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata

import fitz

KINDS = ('ВО', 'Э3', 'ПЭ3', 'СП')
BASE = r'[A-ZА-ЯЁ]{2,12}(?:\s*\.\s*\d{2,}){2,6}'
IDENTITY = re.compile(r'(?<![\w.])('+BASE+r')\s*\.\s*(ПЭ3|Э3|ВО|СП)(?=$|\s|[_ (])', re.I)
REFERENCE = re.compile(r'(?<![\w.])('+BASE+r')\s*\.\s*(ПЭ3|Э3|ВО|СП|ПС|РЭ)(?=$|\s|[_ (])', re.I)


@dataclass(frozen=True)
class PdfIdentity:
    kind: str
    key: str
    designation: str


def _identity(text, reference=False):
    matches = list((REFERENCE if reference else IDENTITY).finditer(unicodedata.normalize('NFC', text)))
    if len(matches) != 1:
        return None
    match = matches[0]
    base = re.sub(r'\s+', '', match[1]).upper()
    kind = match[2].upper()
    return PdfIdentity(kind, base.casefold(), base+'.'+kind)


def _classify_name(name, reference=False):
    """Require exactly one explicit, bounded designation and a PDF extension.

    No type inference from titles, latin lookalikes, or a bare base designation.
    """
    filename = str(name).replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not filename.casefold().endswith('.pdf'):
        return None
    stem = filename[:-4].strip()
    identity = _identity(stem, reference)
    # Multiple document codes (even without another base) make the title ambiguous.
    codes = re.findall(r'(?<![A-ZА-ЯЁ0-9])(?:ПЭ3|Э3|ВО|СП|ПС|РЭ)(?![A-ZА-ЯЁ0-9])', stem, re.I)
    bases = {re.sub(r'\s+', '', m.group()).casefold() for m in re.finditer(BASE, stem, re.I)}
    return identity if identity and len(codes) == 1 and len(bases) == 1 else None


def classify_pdf_name(name):
    return _classify_name(name)


def upload_inventory(names):
    """Pure inventory; counts files, not releases or revisions of a project."""
    groups = defaultdict(lambda: defaultdict(list))
    unknown = []
    for name in names:
        identity = classify_pdf_name(name)
        if identity is None:
            unknown.append(name)
        else:
            groups[identity.key][identity.kind].append(name)
    return {key: dict(kinds) for key, kinds in sorted(groups.items())}, unknown


def _finding(code, filename, location, message):
    return dict(severity='info', code=code, file=filename, location=location, message=message,
                recommendation='Сверьте состав выпуска вручную. Отсутствие PDF в загрузке не доказывает '
                'отсутствия документа в выпущенной КД; машинная сводка не подтверждает полноту проекта.')


def specification_references(content):
    """Only explicitly headed designation cells enclosed by stroked grid lines.

    Return page, row band, exact source text and identity. Ignore plain mentions,
    inferred sections, split cells and drawings without reliable table geometry.
    """
    references = []
    with fitz.open(stream=content, filetype='pdf') as pdf:
        for page_index, page in enumerate(pdf):
            if page_index >= 100:
                break
            words = page.get_text('words')
            headers = [w for w in words if w[4].casefold() == 'обозначение']
            if len(headers) != 1:
                continue
            header = headers[0]
            cx, cy = (header[0]+header[2])/2, (header[1]+header[3])/2
            vertical, horizontal = [], []
            for drawing in page.get_drawings():
                if 's' not in drawing.get('type', ''):
                    continue
                for item in drawing['items']:
                    if item[0] == 'l':
                        segments = [(item[1], item[2])]
                    elif item[0] == 're':
                        r = item[1]
                        segments = [(r.tl,r.tr),(r.bl,r.br),(r.tl,r.bl),(r.tr,r.br)]
                    else:
                        continue
                    for a,b in segments:
                        if abs(a.x-b.x)<.5:
                            vertical.append(((a.x+b.x)/2, min(a.y,b.y), max(a.y,b.y)))
                        if abs(a.y-b.y)<.5:
                            horizontal.append(((a.y+b.y)/2, min(a.x,b.x), max(a.x,b.x)))
            before = [x for x,y0,y1 in vertical if x<cx and y0<=cy<=y1]
            after = [x for x,y0,y1 in vertical if x>cx and y0<=cy<=y1]
            if not before or not after:
                continue
            left,right = max(before),min(after)
            rules = sorted({y for y,x0,x1 in horizontal if x0<=left+.5 and x1>=right-.5})
            for row_index,(top,bottom) in enumerate(zip(rules,rules[1:]),1):
                if top <= header[3]:
                    continue
                if not all(any(abs(x-edge)<.5 and y0<=top+.5 and y1>=bottom-.5
                               for x,y0,y1 in vertical) for edge in (left,right)):
                    continue
                cell = [w for w in words if w[0]<right and w[2]>left and w[1]<bottom and w[3]>top]
                if not cell or any(w[0]<left or w[2]>right or w[1]<top or w[3]>bottom for w in cell):
                    continue
                # One physical text line, one explicit identity, no ancillary text.
                if len({(w[5],w[6]) for w in cell}) != 1:
                    continue
                text = ' '.join(w[4] for w in sorted(cell,key=lambda w:w[0]))
                match = REFERENCE.fullmatch(text.strip())
                identity = _identity(text, reference=True) if match else None
                if identity:
                    references.append((page_index+1,row_index,text,identity))
    return references


def full_set_findings(sources, unreadable_pdf_names=()):
    """sources: (filename, PDF bytes), successfully extracted in this upload only.

    Full-set hints activate for groups containing explicit VO or SP. Existing
    E3/PE3-only scenarios remain owned by KD-SET-101/102/103, without extra noise.
    """
    sources = list(sources)
    groups, unknown = upload_inventory([name for name,_ in sources])
    present = {(identity.key,identity.kind) for name,_ in sources
               if (identity := _classify_name(name, reference=True)) is not None}
    unresolved = [name for name in unknown if _classify_name(name, reference=True) is None]
    findings = []
    for key, kinds in groups.items():
        if not any(kind in kinds for kind in ('ВО','СП')):
            continue
        names = [name for kind in KINDS for name in kinds.get(kind, [])]
        summary = '; '.join(f'{kind}: {len(kinds[kind])} PDF' for kind in KINDS if kind in kinds)
        findings.append(_finding('KD-SET-201', sorted(names,key=str.casefold)[0], 'Состав загруженного набора',
            f'Обозначение комплекта {key.upper()}: распознано {len(names)} загруженных PDF; {summary}. '
            f'В общей загрузке также {len(unknown)} PDF, не отнесённых к ВО/Э3/ПЭ3/СП. Это учёт загрузки, а не вывод о полноте проекта.'))
        for kind in KINDS:
            copies = kinds.get(kind, [])
            if len(copies)>1:
                findings.append(_finding('KD-SET-202', sorted(copies,key=str.casefold)[0], 'Состав загруженного набора',
                    f'Загружено {len(copies)} PDF типа {kind} с ключом {key.upper()}: '+
                    '; '.join(sorted(copies,key=str.casefold))+'. Возможны повторные копии или разные редакции; версия не установлена.'))
    for name,content in sorted(sources,key=lambda source:source[0].casefold()):
        identity = classify_pdf_name(name)
        if not identity or identity.kind!='СП' or len(groups[identity.key]['СП'])!=1:
            continue
        # Ambiguous/unrecognized names could be the referenced upload: decline missing hints.
        if unresolved or unreadable_pdf_names:
            continue
        try:
            references = specification_references(content)
        except (RuntimeError, ValueError):
            # A readable filename is not evidence of a reliable SP table.
            continue
        for page,row,text,reference in references:
            if (reference.key,reference.kind) not in present:
                # A different document type with the same base designation is not
                # evidence that the exact SP reference was supplied. Report it as
                # context only, without inferring a renamed file or project defect.
                other_kinds = sorted(kind for key,kind in present
                                     if key == reference.key and kind != reference.kind)
                context = (
                    ' В загрузке распознаны PDF с тем же базовым обозначением, '
                    'но другими видами документа: ' + ', '.join(other_kinds) +
                    '. Они не заменяют указанное в СП обозначение.'
                ) if other_kinds else ''
                findings.append(_finding('KD-SET-203',name,f'Лист PDF {page}, строка таблицы {row}, столбец «Обозначение»',
                    f'«{text}» явно перечислен в СП, но не включён в загрузку среди распознанных PDF.'
                    + context + ' Это не утверждение об отсутствии документа в выпуске.'))
    return findings
