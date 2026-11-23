"""Only generated PDFs; no enterprise drawings or expected counts inferred from qty."""
import fitz
import pytest

from kd_crosscheck import expand_notation, get_spec_rows, run, scheme_designations


@pytest.mark.parametrize('notation, expected', [
    ('1K\nM1', {'1KM1'}), ('XT\r\n12', {'XT12'}),
    ('1KM1\n...1KM3', {'1KM1', '1KM2', '1KM3'}),
    ('1KM1 ... 1KM3;\n XT2', {'1KM1', '1KM2', '1KM3', 'XT2'}),
])
def test_only_incomplete_fragments_are_joined(notation, expected):
    assert expand_notation(notation) == (expected, [])


@pytest.mark.parametrize('notation', ['XT1\n2', 'XT1 2', '1KM1...1KM3\n4'])
def test_complete_notations_are_not_extended_by_guessing(notation):
    assert expand_notation(notation) == (set(), [notation])


@pytest.mark.parametrize('notation, expected', [
    ('XT1\nXT2', {'XT1', 'XT2'}),
    ('TA2;TA3', {'TA2', 'TA3'}),
    ('TA2; TA3', {'TA2', 'TA3'}),
    ('TA2,TA3', {'TA2', 'TA3'}),
    ('TA2...TA3', {'TA2', 'TA3'}),
    ('TA2–TA3', {'TA2', 'TA3'}),
    ('TA2\nTA3', {'TA2', 'TA3'}),
])
def test_explicit_group_separators_expand_without_inventing_values(notation, expected):
    assert expand_notation(notation) == (expected, [])


@pytest.mark.parametrize('wrapped', [False, True])
def test_pdf_word_gaps_preserve_ambiguous_source_instead_of_inventing_xt12(wrapped):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for y in (70, 135):
            page.draw_line((50, y), (480, y))
        if wrapped:
            page.insert_text((60, 90), 'XT1', fontsize=6)
            page.insert_text((60, 102), '2;1KM1', fontsize=6)
        else:
            page.insert_text((60, 90), 'XT1   2;1KM1', fontsize=6)
        page.insert_text((438, 95), '2', fontsize=6)
        rows = get_spec_rows(pdf.tobytes())
    assert len(rows) == 1
    assert rows[0]['entries'] == {'1KM1'}
    gap = '\n' if wrapped else ' '
    assert rows[0]['unknown'] == ['XT1'+gap+'2']
    assert rows[0]['source'] == 'XT1'+gap+'2;1KM1'


def scheme_pdf(text, x=60, separate_blocks=False, separator=False, second_page=False):
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        if separate_blocks or second_page:
            first, last = text.split('\n')
            page.insert_text((60, 90), first, fontsize=8)
            if second_page:
                page = pdf.new_page(width=600, height=800)
            page.insert_text((x, 140), last, fontsize=8)
        else:
            page.insert_text((x, 90), text, fontsize=8)
        if separator:
            page.draw_line((50, 93), (180, 93))
        return pdf.tobytes()


@pytest.mark.parametrize('text, expected', [('1K\nM1', '1KM1'), ('XT\n12', 'XT12'), ('2KM\n3', '2KM3')])
def test_scheme_joins_adjacent_single_word_lines_in_one_block(text, expected):
    content = scheme_pdf(text)
    with fitz.open(stream=content, filetype='pdf') as pdf:
        assert set(scheme_designations(pdf[0])) == {expected}


@pytest.mark.parametrize('options', [
    {'separate_blocks': True}, {'second_page': True}, {'separator': True},
])
def test_scheme_never_joins_blocks_pages_or_table_rows(options):
    content = scheme_pdf('1KM\n1', **options)
    with fitz.open(stream=content, filetype='pdf') as pdf:
        assert '1KM1' not in {tag for page in pdf for tag in scheme_designations(page)}


def test_scheme_complete_label_and_three_line_ambiguity_stay_unchanged():
    for text, invented in [('XT1\n2', 'XT12'), ('XT\n1\n2', 'XT1')]:
        with fitz.open(stream=scheme_pdf(text), filetype='pdf') as pdf:
            assert invented not in set(scheme_designations(pdf[0]))


def test_scheme_different_alignment_is_not_a_continuation():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 90), '1KM', fontsize=8)
        page.insert_text((100, 100), '1', fontsize=8)
        assert '1KM1' not in set(scheme_designations(page))


def generated_bundle(wrapped):
    explicit = {f'{g}TA{n}' for g in (1, 2) for n in (1, 2, 3)} | {'3TA1'}
    explicit |= {f'XT{i}' for i in range(1, 58)}
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=1000)
        page = pdf.new_page(width=600, height=1000)
        for y in (70, 130):
            page.draw_line((50, y), (480, y))
        lines = ['1TA1...1TA3;2T', 'A1...2TA3;3TA1'] if wrapped else ['1TA1...1TA3;2TA1...2TA3;3TA1']
        for index, line in enumerate(lines):
            page.insert_text((60, 90+index*12), line, fontsize=3)
        page.insert_text((438, 95), '9', fontsize=4)
        for index in range(57):
            top = 130+index*10
            page.draw_line((50, top+10), (480, top+10))
            page.insert_text((60, top+7), f'XT{index+1}', fontsize=4)
            page.insert_text((438, top+7), '1', fontsize=4)
        pe = pdf.tobytes()
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=1000)
        # Additional scheme tags are not evidence of missing notation in the PE3.
        page.insert_text((60, 90), '\n'.join(sorted(explicit | {'3TA2', '3TA3'})), fontsize=6)
        scheme = pdf.tobytes()
    return scheme, pe, explicit


@pytest.mark.parametrize('wrapped', [False, True])
def test_explicit_seven_of_nine_keep_warning_and_all_64_matches(wrapped):
    scheme, pe, explicit = generated_bundle(wrapped)
    rows = get_spec_rows(pe)
    assert set().union(*(row['entries'] for row in rows)) == explicit
    assert len(explicit) == 64
    transformer = next(row for row in rows if row['quantity'] == 9)
    assert len(transformer['entries']) == 7
    assert not transformer['unknown']
    findings = run('demo.Э3.pdf', scheme, 'demo.ПЭ3.pdf', pe)
    # KD-PE-105 (KD-PE-107 stack): the two designators declared in the PE3 row
    # group but not present on the scheme are reported as missing by the
    # inventory comparison; the quantity warning must be retained.
    assert [(f['code'], f['severity']) for f in findings] == [
        ('KD-PE-102', 'warning'), ('KD-PE-100', 'info'), ('KD-PE-105', 'warning'),
    ]
    assert findings[0]['location'] == f"Лист PDF 2, строка таблицы {transformer['table_row']}"
    assert 'количество 9' in findings[0]['message'] and 'обозначений (7)' in findings[0]['message']
    assert 'уникальных обозначений — 64' in findings[1]['message']
    assert 'совпадений — 64' in findings[1]['message']


def test_recovered_scheme_prefix_matches_without_changing_pe3_quantity():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 90), '1KM1', fontsize=6)
        page.insert_text((438, 90), '1', fontsize=6)
        pe = pdf.tobytes()
    findings = run('demo.Э3.pdf', scheme_pdf('1K\nM1'), 'demo.ПЭ3.pdf', pe)
    assert [f['code'] for f in findings] == ['KD-PE-100']
    assert 'совпадений — 1' in findings[0]['message']
