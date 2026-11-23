"""Synthetic-only full-set inventory and explicit, grid-backed SP references."""
from contextlib import closing
from io import BytesIO

import fitz
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import app as web
from full_set_completeness import (classify_pdf_name, upload_inventory, full_set_findings,
                                   specification_references)
import storage

BASE = 'ABCD.123456.001'


def pdf(rows=(), grid=True, header=True, pages=1):
    with fitz.open() as doc:
        for _ in range(pages):
            page = doc.new_page(width=600, height=800)
            page.insert_font(fontname='TestUnicode', fontbuffer=fitz.Font('cjk').buffer)
            page.insert_text((350, 40), 'Synthetic test only')
            if grid:
                for y in range(70, 101+len(rows)*30, 30):
                    page.draw_line((50, y), (500, y))
                for x in (50, 310, 500):
                    page.draw_line((x,70),(x,100+len(rows)*30))
            if header:
                page.insert_text((60,90),'Обозначение',fontname='TestUnicode',fontsize=9)
            for index,text in enumerate(rows):
                page.insert_text((60,120+index*30),text,fontname='TestUnicode',fontsize=9)
        doc.subset_fonts()
        return doc.tobytes()


@pytest.mark.parametrize('kind', ['ВО','Э3','ПЭ3','СП'])
@pytest.mark.parametrize('pattern', ['{base}.{kind}.pdf', '  {base} . {kind}   synthetic title.PDF ', 'Prefix {base}.{kind}_drawing.pdf'])
def test_explicit_type_and_key(kind, pattern):
    value = classify_pdf_name(pattern.format(base=BASE.lower(),kind=kind.lower()))
    assert value.kind == kind
    assert value.key == BASE.casefold()
    assert value.designation == BASE+'.'+kind


@pytest.mark.parametrize('name', [
    'drawing.pdf', BASE+'.pdf', 'ВО.pdf', 'drawing ВО.pdf', BASE+'.E3.pdf',
    BASE+'.ВО.xlsx', BASE+'.ВО.pdf.exe', BASE+'.Э3extra.pdf', BASE+'.Э3.1.pdf',
    BASE+'.ВО СП.pdf', BASE+'.Э3_'+BASE+'.ПЭ3.pdf', BASE+'.СП other DEFG.987654.002.pdf',
    BASE+'.РЭ.pdf', BASE+'.ПС.pdf',
])
def test_ambiguity_does_not_guess(name):
    assert classify_pdf_name(name) is None


def test_paths_and_distinct_scopes():
    assert classify_pdf_name('C:\\temp\\'+BASE+'.СП.pdf').kind == 'СП'
    groups, unknown = upload_inventory([BASE+'.ВО.pdf', 'DEFG.987654.002.СП.pdf', 'unknown.pdf'])
    assert len(groups) == 2 and unknown == ['unknown.pdf']
    assert groups[BASE.casefold()] == {'ВО': [BASE+'.ВО.pdf']}


def test_cyrillic_prefix_and_no_homoglyph_substitution():
    assert classify_pdf_name('абвг.123456.001.сп.pdf').key == 'абвг.123456.001'
    assert classify_pdf_name('АBCD.123456.001.СП.pdf').key != classify_pdf_name(BASE+'.СП.pdf').key


def test_failed_sp_extraction_declines_reference_findings():
    findings=full_set_findings([(BASE+'.СП.pdf',b'not a PDF')])
    assert [f['code'] for f in findings]==['KD-SET-201']


def test_all_four_is_upload_inventory_not_project_certification():
    findings = full_set_findings([(BASE+'.'+k+'.pdf',pdf()) for k in ('ВО','Э3','ПЭ3','СП')])
    assert [f['code'] for f in findings] == ['KD-SET-201']
    assert '4 загруженных PDF' in findings[0]['message']
    assert all(k+': 1 PDF' in findings[0]['message'] for k in ('ВО','Э3','ПЭ3','СП'))
    assert findings[0]['severity'] == 'info'
    assert 'не вывод о полноте проекта' in findings[0]['message']


def test_no_expected_file_is_invented_without_sp_evidence():
    findings = full_set_findings([(BASE+'.ВО.pdf',pdf())])
    assert [f['code'] for f in findings] == ['KD-SET-201']
    assert 'не включён' not in findings[0]['message']
    assert full_set_findings([(BASE+'.Э3.pdf',pdf()), (BASE+'.ПЭ3.pdf',pdf())]) == []
    assert full_set_findings([('unknown.pdf',pdf())]) == []


@pytest.mark.parametrize('kind', ['ВО','Э3','ПЭ3','СП'])
def test_duplicate_identity_is_informational_not_revision_inference(kind):
    sources = [(BASE+'.'+kind+'.pdf',pdf()), (BASE+'.'+kind+' copy.pdf',pdf())]
    if kind not in ('ВО','СП'):
        sources.append((BASE+'.ВО.pdf',pdf()))
    findings = full_set_findings(sources)
    duplicate = next(f for f in findings if f['code']=='KD-SET-202')
    assert duplicate['severity'] == 'info'
    assert 'версия не установлена' in duplicate['message']
    assert all(f['code'] not in ('KD-SET-101','KD-SET-102','KD-SET-103') for f in findings)


@pytest.mark.parametrize('kind', ['ВО','Э3','ПЭ3','СП','ПС','РЭ'])
def test_explicit_spec_cell_has_exact_source_and_only_upload_absence(kind):
    text = 'DEFG.987654.002.'+kind
    data = pdf([text])
    refs = specification_references(data)
    assert len(refs) == 1
    assert refs[0][:3] == (1,2,text)
    findings = full_set_findings([(BASE+'.СП.pdf',data)])
    hint = next(f for f in findings if f['code']=='KD-SET-203')
    assert hint['severity'] == 'info'
    assert text in hint['message']
    assert 'не включён в загрузку' in hint['message']
    assert 'Лист PDF 1, строка таблицы 2' in hint['location']


@pytest.mark.parametrize('kind', ['ВО','Э3','ПЭ3','СП','ПС','РЭ'])
def test_present_reference_in_other_scope_is_not_reported_missing(kind):
    target = 'DEFG.987654.002.'+kind
    findings = full_set_findings([(BASE+'.СП.pdf',pdf([target])), (target+'.pdf',pdf())])
    assert not any(f['code']=='KD-SET-203' for f in findings)


@pytest.mark.parametrize('options', [{'grid':False}, {'header':False}])
def test_plain_mentions_or_unconfirmed_table_are_not_evidence(options):
    assert specification_references(pdf([BASE+'.Э3'], **options)) == []


@pytest.mark.parametrize('text', [BASE+'.Э3 commentary', BASE+'.Э3\n'+BASE+'.ВО', BASE+'.Э3;'+BASE+'.ВО', 'Maybe '+BASE+'.Э3'])
def test_ambiguous_spec_cells_are_skipped(text):
    assert specification_references(pdf([text])) == []


def test_unknown_upload_and_multiple_spec_versions_suppress_missing_hints():
    data = pdf(['DEFG.987654.002.Э3'])
    for extra in [('unknown.pdf',pdf()), (BASE+'.СП copy.pdf',data)]:
        findings = full_set_findings([(BASE+'.СП.pdf',data), extra])
        assert not any(f['code']=='KD-SET-203' for f in findings)


@pytest.mark.parametrize(('page_count', 'sheet_label'), [(2, 9), (3, 4)])
def test_page_count_does_not_trigger_sheet_total_heuristics(page_count, sheet_label):
    """Synthetic sheet-like text is not a verified title-block field."""
    label = f'Листов {sheet_label}'
    content = pdf([label], pages=page_count)
    with fitz.open(stream=content, filetype='pdf') as document:
        assert len(document) == page_count
        assert label in document[0].get_text()
    findings = full_set_findings([(BASE+'.ВО.pdf', content)])
    assert [f['code'] for f in findings] == ['KD-SET-201']
    assert all(f['severity'] == 'info' for f in findings)


def test_api_history_excel_csv_and_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'DB',tmp_path/'history.sqlite3')
    monkeypatch.setattr(web,'analyze',lambda docs,cfg:[])
    with TestClient(web.app) as client:
        files=[('files',(BASE+'.ВО.pdf',pdf(),'application/pdf')),
               ('files',(BASE+'.СП.pdf',pdf(['DEFG.987654.002.РЭ']),'application/pdf'))]
        response=client.post('/api/check',files=files)
        assert response.status_code==200
        body=response.json()
        assert [f['code'] for f in body['issues']]==['KD-SET-201','KD-SET-203','KD-EM-108-2']
        assert body['summary']==dict(error=0,warning=1,info=2)
        run_id=body['id'];record=client.get(f'/api/runs/{run_id}').json()
        state=record['finding_states'][1]
        assert client.patch(f'/api/runs/{run_id}/findings/{state["id"]}',json=dict(status='in_progress',comment='=1+1',expected_version=0)).status_code==200
        assert client.get(f'/api/runs/{run_id}').json()['issues']==body['issues']
        with closing(load_workbook(BytesIO(client.get(f'/api/runs/{run_id}/xlsx').content))) as book:
            assert book['Замечания']['C3'].value=='KD-SET-203'
            assert book['Замечания']['I3'].value=='=1+1' and book['Замечания']['I3'].data_type=='s'
        assert b'KD-SET-203' in client.get(f'/api/runs/{run_id}/csv').content
        assert len(client.get(f'/api/runs/{run_id}/findings/{state["id"]}/history').json())==1


def test_invalid_pdf_does_not_enter_inventory(monkeypatch):
    monkeypatch.setattr(web,'save_run',lambda names,issues:42)
    with TestClient(web.app) as client:
        result=client.post('/api/check',files=[('files',(BASE+'.СП.pdf',b'bad','application/pdf'))]).json()
    assert not any(f['code'].startswith('KD-SET-2') for f in result['issues'])


def test_unreadable_target_is_not_called_missing_from_upload(monkeypatch):
    monkeypatch.setattr(web,'save_run',lambda names,issues:42)
    monkeypatch.setattr(web,'analyze',lambda docs,cfg:[])
    with TestClient(web.app) as client:
        result=client.post('/api/check',files=[
            ('files',(BASE+'.СП.pdf',pdf(['DEFG.987654.002.ВО']),'application/pdf')),
            ('files',('DEFG.987654.002.ВО.pdf',b'bad','application/pdf')),
        ]).json()
    assert 'KD-000' in [f['code'] for f in result['issues']]
    assert not any(f['code']=='KD-SET-203' for f in result['issues'])


def test_source_page_and_duplicate_type_do_not_guess_missing_releases():
    data=pdf(['DEFG.987654.002.ПС'],pages=2)
    refs=specification_references(data)
    assert [ref[0] for ref in refs]==[1,2]
    findings=full_set_findings([(BASE+'.СП.pdf',data)])
    assert all(f['severity']=='info' for f in findings)
    assert [f['location'] for f in findings if f['code']=='KD-SET-203']==[
        'Лист PDF 1, строка таблицы 2, столбец «Обозначение»',
        'Лист PDF 2, строка таблицы 2, столбец «Обозначение»']


def test_sp_reference_reports_other_available_kinds_with_same_base():
    requested = 'DEFG.987654.002.Э3'
    other_prefix = 'DEFG.987654.002'
    findings = full_set_findings([
        (BASE+'.СП.pdf', pdf([requested])),
        (other_prefix+'.ВО.pdf', pdf()),
        (other_prefix+'.ПЭ3.pdf', pdf()),
    ])
    missing = [f for f in findings if f['code'] == 'KD-SET-203']
    assert len(missing) == 1
    assert missing[0]['severity'] == 'info'
    assert requested in missing[0]['message']
    assert 'ВО, ПЭ3' in missing[0]['message']
    assert 'не заменяют указанное' in missing[0]['message']
    assert 'отсутствии документа в выпуске' in missing[0]['message']


def test_exact_sp_reference_present_does_not_report_alternate_kinds():
    requested = 'DEFG.987654.002.Э3'
    findings = full_set_findings([
        (BASE+'.СП.pdf', pdf([requested])),
        (requested+'.pdf', pdf()),
        ('DEFG.987654.002.ПЭ3.pdf', pdf()),
    ])
    assert not any(f['code'] == 'KD-SET-203' for f in findings)


def test_sp_reference_different_base_or_ambiguous_upload_does_not_guess():
    requested = 'DEFG.987654.002.Э3'
    findings = full_set_findings([
        (BASE+'.СП.pdf', pdf([requested])),
        ('WXYZ.987654.002.ПЭ3.pdf', pdf()),
    ])
    missing = next(f for f in findings if f['code'] == 'KD-SET-203')
    assert 'другими видами документа' not in missing['message']
    suppressed = full_set_findings([
        (BASE+'.СП.pdf', pdf([requested])),
        ('ambiguous.pdf', pdf()),
        ('DEFG.987654.002.ПЭ3.pdf', pdf()),
    ])
    assert not any(f['code'] == 'KD-SET-203' for f in suppressed)
