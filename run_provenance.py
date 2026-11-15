"""Bounded, strict run evidence. Never infer missing historical settings."""
import json
import re
import math
import sys
from source_identity import DEPENDENCIES, dependency_versions, source_fingerprint

UNKNOWN = {'status':'unknown', 'reason':'not_recorded'}
KEYS = {'format_version','pipeline','configuration','configuration_version',
        'source_fingerprint','dependencies','parser_limits','python_version'}


def normalize(value):
    if not isinstance(value,dict) or set(value) != KEYS or type(value['format_version']) is not int or value['format_version'] != 1:
        raise ValueError('Invalid provenance schema')
    if value['pipeline'] not in ('documents','connections'):
        raise ValueError('Unknown provenance pipeline')
    if not isinstance(value['source_fingerprint'],str) or re.fullmatch('[0-9a-f]{64}',value['source_fingerprint']) is None:
        raise ValueError('Invalid source fingerprint')
    if not isinstance(value['python_version'],str) or re.fullmatch(r'[0-9A-Za-z.+_-]{1,100}',value['python_version']) is None:
        raise ValueError('Invalid Python version')
    dependencies = value['dependencies']
    if (not isinstance(dependencies,dict) or set(dependencies) != set(DEPENDENCIES)
            or any(not isinstance(v,str) or not 1 <= len(v) <= 100 or any(ord(c)<32 or ord(c)==127 for c in v)
                   for v in dependencies.values())):
        raise ValueError('Invalid dependency evidence')
    result = json.loads(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False))
    if value['pipeline'] == 'documents':
        if type(value['configuration_version']) is not int or value['configuration_version'] < 0:
            raise ValueError('Explicit configuration version required')
        # Frozen structural v1 contract, NOT today's config policy. Old snapshots
        # are inert data: never compile their regex or reinterpret their values.
        cfg = value['configuration']
        booleans = {'require_designation_in_filename','detect_duplicate_designations','require_pdf_text',
                    'require_excel_headers','check_specification_references','check_duplicate_positions','warn_empty_dxf'}
        if (not isinstance(cfg,dict) or set(cfg) != booleans | {'designation_pattern','min_pdf_text_chars','specification_headers'}
                or any(type(cfg[k]) is not bool for k in booleans)
                or type(cfg['min_pdf_text_chars']) is not int or not 0 <= cfg['min_pdf_text_chars'] <= 10000
                or not isinstance(cfg['designation_pattern'],str) or not 1 <= len(cfg['designation_pattern']) <= 160
                or not isinstance(cfg['specification_headers'],list) or not 1 <= len(cfg['specification_headers']) <= 12
                or any(not isinstance(h,str) or not h.strip() or len(h)>60 for h in cfg['specification_headers'])):
            raise ValueError('Invalid stored configuration snapshot')
        limits = value['parser_limits']
        if (not isinstance(limits,dict) or set(limits) != {'seconds','memory_mb','cpu_seconds','output_bytes'}
                or type(limits['seconds']) not in (int,float) or not math.isfinite(limits['seconds'])
                or not 0 < limits['seconds'] <= 120
                or type(limits['memory_mb']) is not int or not 256 <= limits['memory_mb'] <= 4096
                or type(limits['cpu_seconds']) is not int or not 1 <= limits['cpu_seconds'] <= 120
                or type(limits['output_bytes']) is not int or not 1024 <= limits['output_bytes'] <= 2*1024*1024):
            raise ValueError('Explicit parser limits required')
    elif any(value[k] is not None for k in ('configuration','configuration_version','parser_limits')):
        raise ValueError('Document settings do not apply to structured connections')
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')) > 16384:
        raise ValueError('Provenance exceeds budget')
    return result


def capture(pipeline, *, configuration=None, configuration_version=None, parser_limits=None):
    if pipeline == 'documents':
        from project_configuration import Settings
        configuration = Settings.model_validate(configuration).model_dump()
    return normalize({'format_version':1, 'pipeline':pipeline, 'configuration':configuration,
                      'configuration_version':configuration_version,
                      'source_fingerprint':source_fingerprint(), 'dependencies':dependency_versions(),
                      'parser_limits':vars(parser_limits) if parser_limits is not None else None,
                      'python_version':sys.version.split()[0]})


def comparison(previous, current):
    if previous.get('status') != 'recorded' or current.get('status') != 'recorded':
        return {'same_configuration':None, 'same_source':None, 'same_runtime':None, 'reason':'missing_historical_evidence'}
    a,b = previous['evidence'],current['evidence']
    same_config = a['configuration']==b['configuration'] if a['pipeline']==b['pipeline']=='documents' else None
    return {'same_configuration':same_config,
            'same_source':a['source_fingerprint']==b['source_fingerprint'],
            'same_runtime':a['dependencies']==b['dependencies'] and a['python_version']==b['python_version'],
            'reason':'snapshot_comparison_not_engineering_verdict'}
