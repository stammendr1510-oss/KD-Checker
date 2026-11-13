import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import analyze
from extractors import Document
from app import load_cfg

def test_duplicate_positions_and_missing_reference():
    d=Document('АБВ.001.xlsx','.xlsx',sheets=[{'name':'Спец','rows':[['Позиция','Обозначение','Наименование'],['1','АБВ.002','Деталь'],['1','АБВ.003','Сборка']]}])
    issues=analyze([d],load_cfg())
    codes=[x['code'] for x in issues]
    assert 'KD-011' in codes and codes.count('KD-012')==2

def test_pdf_without_text():
    d=Document('АБВ.001.pdf','.pdf',text='')
    assert 'KD-002' in [x['code'] for x in analyze([d],load_cfg())]

def test_cross_format_not_duplicate():
    docs=[Document('АБВ.001.pdf','.pdf',text='some text '*10),Document('АБВ.001.dxf','.dxf',entities=3)]
    assert 'KD-020' not in [x['code'] for x in analyze(docs,load_cfg())]
