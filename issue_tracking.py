"""Conservative finding identities; no automatic transfer of engineering decisions."""
import hashlib
import json
import unicodedata

STATUS_LABELS = {'new': 'Новое', 'in_progress': 'В работе', 'resolved': 'Устранено',
                 'rejected': 'Отклонено с обоснованием'}
MAX_COMMENT = 4000


def _filename(value):
    return unicodedata.normalize('NFC', str(value).replace('\\', '/').rsplit('/', 1)[-1]).casefold()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def run_scope(files):
    return _digest(['files-v1', sorted(_filename(name) for name in files)])


def finding_identity(issue):
    values = [' '.join(unicodedata.normalize('NFC', str(issue.get(key, ''))).split())
              for key in ('severity', 'code', 'location', 'message', 'recommendation')]
    if 'details' in issue:
        # Additive only for findings that carry structured evidence. Findings
        # without details keep the exact legacy fingerprint.
        values.append(json.dumps(issue['details'], ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False))
    return _digest(['finding-v1', _filename(issue.get('file', '')), *values])


def validate_decision(status, comment):
    if status not in STATUS_LABELS:
        raise ValueError('Неизвестный статус замечания')
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
        raise ValueError(f'Комментарий должен содержать не более {MAX_COMMENT} символов')
    if status == 'rejected' and not comment.strip():
        raise ValueError('Для отклонения замечания укажите обоснование')
