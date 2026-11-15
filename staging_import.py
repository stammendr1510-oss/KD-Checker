"""Offline, single-batch staging import. Requires PR #45 and scoped repository."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from history_backup import copy_history
from issue_tracking import finding_identity, run_scope, validate_decision
from scoped_history import Repository, Scope

TABLES = ('runs', 'issue_runs', 'finding_states', 'finding_events', 'issue_schema')


def stage_history(source, backup, staging, *, scope: Scope, revision: str, operator: str):
    """Create a verified copy and NEW staging DB. Never switches working storage.

    Scope assignment is an explicit trusted operator decision for the entire
    source. Preserves complete source rows in a legacy archive. No inferred
    tenant assignment, resumable/delta import, or production migration.
    """
    if not isinstance(scope, Scope) or not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
        raise ValueError('Explicit scope and revision required')
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 200:
        raise ValueError('Explicit operator required')
    staging = Path(staging)
    if staging.exists() or staging.is_symlink():
        raise FileExistsError(staging)
    if Path(source).stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Source exceeds staging budget')
    copied = copy_history(source, backup)
    with closing(sqlite3.connect(copied.as_uri() + '?mode=ro', uri=True)) as con:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Optional snapshots are archived, without changing Checklist integration.
        allowed = set(TABLES) | {'sqlite_sequence', 'checklist_run_snapshots'}
        if tables - allowed:
            raise ValueError('Unknown source tables; refusing silent data loss')
        rows = {table: [dict(r) for r in con.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}
    if len(rows['runs']) > 10000 or len(rows['finding_states']) > 100000:
        raise ValueError('Source exceeds staging record budget')
    runs = {r['id']: r for r in rows['runs']}
    scopes = {r['run_id']: r['scope'] for r in rows['issue_runs']}
    states = {r['id']: r for r in rows['finding_states']}
    grouped_states = {}
    for state in states.values():
        grouped_states.setdefault(state['run_id'], []).append(state)
    events = {}
    for event in rows['finding_events']:
        if event['finding_id'] not in states:
            raise ValueError('Orphan audit')
        events.setdefault(event['finding_id'], []).append(event)
    parsed = {}
    for identifier, row in runs.items():
        files, issues = json.loads(row['filenames']), json.loads(row['issues'])
        if not isinstance(files, list) or any(not isinstance(f, str) for f in files) or not isinstance(issues, list):
            raise ValueError('Malformed legacy run')
        if scopes.get(identifier) != run_scope(files):
            raise ValueError('Legacy scope mismatch')
        selected = grouped_states.get(identifier, [])
        if sorted(s['issue_index'] for s in selected) != list(range(len(issues))):
            raise ValueError('Missing or duplicate finding state')
        parsed[identifier] = files, issues
        for state in selected:
            if state['fingerprint'] != finding_identity(issues[state['issue_index']]):
                raise ValueError('Finding identity mismatch')
            status, comment, version, changed_at = 'new', '', 0, None
            for event in sorted(events.get(state['id'], []), key=lambda e: e['id']):
                if (event['old_status'], event['old_comment'], event['old_version']) != (status, comment, version):
                    raise ValueError('Broken audit chain')
                if event['new_version'] != version + 1:
                    raise ValueError('Broken audit version')
                validate_decision(event['new_status'], event['new_comment'])
                status, comment, version, changed_at = event['new_status'], event['new_comment'], event['new_version'], event['changed_at']
            if (state['status'], state['comment'], state['version'], state['updated_at']) != (status, comment, version, changed_at):
                raise ValueError('Decision and audit disagree')
    if set(scopes) != set(runs) or any(s['run_id'] not in runs for s in states.values()):
        raise ValueError('Orphan legacy state')
    # Exclusive creation determines ownership of cleanup. Existing files survive.
    target = Repository.create(staging)
    try:
        target.provision(operator, scope, 'admin')
        with target.transaction(True) as con:
            con.execute('CREATE TABLE import_manifest(source_sha256 TEXT, org TEXT, project TEXT, revision TEXT, operator TEXT)')
            con.execute('INSERT INTO import_manifest VALUES(?,?,?,?,?)',
                        (hashlib.sha256(copied.read_bytes()).hexdigest(), scope.organization, scope.project, revision, operator))
            con.execute('CREATE TABLE legacy_archive(table_name TEXT, ordinal INTEGER, payload TEXT, PRIMARY KEY(table_name,ordinal))')
            for table, records in rows.items():
                con.executemany('INSERT INTO legacy_archive VALUES(?,?,?)',
                    [(table, n, json.dumps(r, ensure_ascii=False, allow_nan=False)) for n, r in enumerate(records)])
            previous = {}
            for identifier in sorted(runs):
                row, (files, issues) = runs[identifier], parsed[identifier]
                key = run_scope(files)
                con.execute('''INSERT INTO runs(id,org,project,actor,created_at,revision,scope,files,issues,previous_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (identifier, scope.organization, scope.project, 'legacy_unknown',
                    row['created_at'], revision, key, row['filenames'], row['issues'], previous.get(key)))
                previous[key] = identifier
            for state in states.values():
                con.execute('INSERT INTO decisions(org,project,run_id,ordinal,status,comment,version,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                            (scope.organization, scope.project, state['run_id'], state['issue_index'], state['status'],
                             state['comment'], state['version'], state['updated_at']))
            for event in sorted(rows['finding_events'], key=lambda e: e['id']):
                state = states[event['finding_id']]
                con.execute('''INSERT INTO events(id,org,project,run_id,ordinal,actor,changed_at,old_status,new_status,
                    old_comment,new_comment,old_version,new_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (event['id'], scope.organization, scope.project, state['run_id'], state['issue_index'], 'legacy_unknown',
                     event['changed_at'], event['old_status'], event['new_status'], event['old_comment'],
                     event['new_comment'], event['old_version'], event['new_version']))
            # Preserve high-water marks, including IDs of previously deleted rows.
            for sequence in rows.get('sqlite_sequence', []):
                mapped = {'runs': 'runs', 'finding_events': 'events'}.get(sequence['name'])
                if mapped:
                    changed = con.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?', (sequence['seq'], mapped))
                    if not changed.rowcount:
                        con.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', (mapped, sequence['seq']))
            for table, count in [('runs', len(runs)), ('decisions', len(states)), ('events', len(rows['finding_events']))]:
                if con.execute(f'SELECT count(*) FROM {table}').fetchone()[0] != count:
                    raise ValueError('Staging count mismatch')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Staging integrity mismatch')
            archive = con.execute('SELECT count(*) FROM legacy_archive').fetchone()[0]
            if archive != sum(len(r) for r in rows.values()):
                raise ValueError('Archive count mismatch')
        return target
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
