"""Real synthetic scoped backend; never calls local app endpoints or Checklist."""
import csv
import io
import json
import asyncio
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from design.access_policy import Role
from scoped_history import Scope, Denied, Missing, Conflict
from extractors import extract, SUPPORTED
from engine import analyze
from full_set_completeness import full_set_findings
from set_completeness import unpaired_document_findings
from document_identity import pdf_identity_findings, unique_pdf_analysis_sources
from pair_analysis import pair_analysis_plan
from kd_crosscheck import designation_type, pair_key, check_spec_duplicates, run as crosscheck
from adapters import apply_engineering_findings
from excel_reports import build_excel_report
from connection_import import parse_connections
from project_configuration import ProjectConfiguration
from parser_process import run_check, Limits, ProcessingUnavailable
from run_provenance import capture
from source_identity import source_fingerprint, dependency_versions
from parser_capacity import prepare as prepare_capacity


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str
    comment: str = Field(max_length=4000)
    expected_version: int = Field(ge=0, strict=True)


def check_documents(files, cfg, *, include_evidence=False):
    docs, failures, sources, pairs = [], [], [], []
    for name, content in files:
        try:
            docs.append(extract(name, content))
            if name.lower().endswith('.pdf'):
                sources.append((name, content))
                if designation_type(name):
                    pairs.append((name, designation_type(name), pair_key(name), content))
        except Exception:
            failures.append(dict(severity='error', code='KD-000', file=name, location='Загрузка',
                                 message='Не удалось прочитать файл', recommendation='Проверьте формат и повреждение'))
    issues = analyze(docs, cfg) + failures
    issues += full_set_findings(sources, [i['file'] for i in failures if i['file'].lower().endswith('.pdf')])
    issues += unpaired_document_findings([s[0] for s in pairs]) + pdf_identity_findings(pairs)
    unique = unique_pdf_analysis_sources(pairs)
    for name, kind, _, content in unique:
        if kind == 'ПЭ3':
            issues += check_spec_duplicates(name, content)
    plan, notices = pair_analysis_plan(unique)
    issues += notices
    for scheme, spec in plan:
        issues += crosscheck(scheme[0], scheme[3], spec[0], spec[3], include_evidence=include_evidence)
    issues = apply_engineering_findings(issues, pairs, cfg)
    return issues


class ScopedBackend:
    def __init__(self, repository, *, parser_limits=None):
        self.repo = repository
        self.source_fingerprint = source_fingerprint()
        self.runtime_dependencies = dependency_versions()
        self.parser_limits = parser_limits or Limits()
        if not isinstance(self.parser_limits, Limits):
            raise ValueError('Explicit parser Limits required')
        self.parser_capacity = prepare_capacity(repository.path)
        with repository.transaction(True) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS http_findings(id INTEGER PRIMARY KEY AUTOINCREMENT,
                org TEXT, project TEXT, run_id INTEGER, ordinal INTEGER, UNIQUE(org,project,run_id,ordinal),
                FOREIGN KEY(org,project,run_id,ordinal) REFERENCES decisions(org,project,run_id,ordinal))''')
            con.execute('''CREATE TABLE IF NOT EXISTS project_config(org TEXT, project TEXT, payload TEXT,
                PRIMARY KEY(org,project), FOREIGN KEY(org,project) REFERENCES projects(org,project))''')
        self.project_configuration = ProjectConfiguration(repository)

    def role(self, identity):
        with self.repo.transaction() as con:
            row = con.execute('SELECT role FROM memberships WHERE subject=? AND org=? AND project=?',
                              (identity.subject, identity.organization, identity.project)).fetchone()
            return Role(row[0]) if row else None

    def run_scope(self, run_id):
        with self.repo.transaction() as con:
            row = con.execute('SELECT org,project FROM runs WHERE id=?', (run_id,)).fetchone()
            return tuple(row) if row else None

    def finding_run(self, finding_id):
        with self.repo.transaction() as con:
            row = con.execute('SELECT run_id FROM http_findings WHERE id=?', (finding_id,)).fetchone()
            return row[0] if row else None

    def config(self, identity):
        return self.project_configuration.read(identity)[0]

    def record(self, identity, run_id):
        scope = Scope(identity.organization, identity.project)
        with self.repo.transaction(True) as con:
            self.repo._member(con, identity.subject, scope)
            record = self.repo._get(con, scope, run_id)
            for state in record['decisions']:
                con.execute('INSERT OR IGNORE INTO http_findings(org,project,run_id,ordinal) VALUES(?,?,?,?)',
                            (scope.organization, scope.project, run_id, state['ordinal']))
                state['id'] = con.execute('SELECT id FROM http_findings WHERE org=? AND project=? AND run_id=? AND ordinal=?',
                                         (scope.organization, scope.project, run_id, state['ordinal'])).fetchone()[0]
                state['issue_index'] = state['ordinal']
            record['finding_states'] = record['decisions']
            return record

    async def dispatch(self, method, path, identity, request):
        try:
            return await self._dispatch(method, path, identity, request)
        except Denied:
            raise HTTPException(403, 'Request denied') from None
        except Missing:
            raise HTTPException(404, 'Not found') from None
        except Conflict:
            raise HTTPException(409, 'Version conflict') from None
        except ProcessingUnavailable:
            raise HTTPException(503, 'Document processing unavailable')
        except ValueError:
            raise HTTPException(422, 'Invalid request') from None

    async def _dispatch(self, method, path, identity, request):
        scope, actor = Scope(identity.organization, identity.project), identity.subject
        if path == '/api/connections/check':
            evidence = capture('connections')
            if (evidence['source_fingerprint'] != self.source_fingerprint
                    or evidence['dependencies'] != self.runtime_dependencies):
                raise ProcessingUnavailable()
            payload, files, issues = parse_connections(request.state.connection_input)
            if payload.project != identity.project:
                raise HTTPException(404, 'Not found')
            if (source_fingerprint() != evidence['source_fingerprint']
                    or dependency_versions() != evidence['dependencies']):
                raise ProcessingUnavailable()
            identifier = self.repo.save(actor, scope, files, issues, revision=payload.revision, provenance=evidence)
            return self.record(identity, identifier)
        if path == '/api/config':
            if method == 'GET':
                settings, version = self.project_configuration.read(identity)
                return JSONResponse(settings,headers={'X-Config-Version':str(version)})
            data = await request.json()
            if not isinstance(data,dict) or 'expected_version' not in data:
                raise HTTPException(409,'Expected configuration version required')
            settings, version = self.project_configuration.update(identity,data)
            return {'settings':settings,'version':version}
        if path == '/api/config/history':
            return self.project_configuration.history(identity)
        if path == '/api/check':
            async with request.form(max_files=100, max_fields=2) as form:
                uploads = form.getlist('files')
                revision = form.get('revision')
                if (set(form.keys()) - {'files', 'revision'} or len(form.getlist('revision')) != 1
                        or not uploads or len(uploads) > 100 or not isinstance(revision, str) or not revision.strip()):
                    raise HTTPException(422, 'Files and explicit revision required')
                files, total = [], 0
                for upload in uploads:
                    if not hasattr(upload, 'read') or not isinstance(upload.filename, str) or not upload.filename:
                        raise HTTPException(422, 'Invalid upload')
                    name = Path(upload.filename.replace('\\', '/')).name
                    if Path(name).suffix.lower() not in SUPPORTED:
                        raise HTTPException(422, 'Unsupported file')
                    content = await upload.read(20*1024*1024 + 1)
                    total += len(content)
                    if len(content) > 20*1024*1024 or total > 100*1024*1024:
                        raise HTTPException(413, 'Upload budget exceeded')
                    files.append((name, content))
                settings, version = self.project_configuration.read(identity)
                evidence = capture('documents', configuration=settings, configuration_version=version,
                                   parser_limits=self.parser_limits)
                if (evidence['source_fingerprint'] != self.source_fingerprint
                        or evidence['dependencies'] != self.runtime_dependencies):
                    raise ProcessingUnavailable()
                cancelled = getattr(request.state, 'parser_cancelled', None)
                if cancelled is None or cancelled.is_set():
                    raise ProcessingUnavailable()
                work = asyncio.create_task(asyncio.to_thread(run_check, files, evidence['configuration'], self.parser_limits,
                                                            capacity=self.parser_capacity, cancelled=cancelled))
                # A second transport/task cancellation can interrupt the cleanup
                # await. Still observe completion of the independent worker task.
                work.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                try:
                    issues = await asyncio.shield(work)
                except asyncio.CancelledError:
                    cancelled.set()
                    # The thread observes the event and kills/reaps the child.
                    # Shield keeps task cancellation from discarding its cleanup.
                    try:
                        await asyncio.shield(work)
                    except ProcessingUnavailable:
                        pass
                    raise
                if cancelled.is_set():
                    raise ProcessingUnavailable()
                if (source_fingerprint() != evidence['source_fingerprint']
                        or dependency_versions() != evidence['dependencies']):
                    raise ProcessingUnavailable()
                # save() rechecks live membership INSIDE its write transaction.
                identifier = self.repo.save(actor, scope, [f[0] for f in files], issues, revision=revision, provenance=evidence)
                return self.record(identity, identifier)
        if path == '/api/runs':
            return [self.record(identity, identifier) for identifier in self.repo.list(actor, scope)]
        run_id = int(request.path_params['run_id'])
        if 'checklist-compare' in path:
            raise HTTPException(409, 'Historical snapshots are not integrated')
        if 'previous_run_id' in request.path_params:
            return self.repo.compare(actor, scope, int(request.path_params['previous_run_id']), run_id)
        record = self.record(identity, run_id)
        if 'finding_id' in request.path_params:
            finding = int(request.path_params['finding_id'])
            state = next((s for s in record['finding_states'] if s['id'] == finding), None)
            if state is None:
                raise Missing()
            if method == 'PATCH':
                data = Decision.model_validate(await request.json())
                self.repo.update(actor, scope, run_id, state['ordinal'], data.status, data.comment, data.expected_version)
                return next(s for s in self.record(identity, run_id)['finding_states'] if s['id'] == finding)
            return self.repo.history(actor, scope, run_id, state['ordinal'])
        if path.endswith('/xlsx'):
            return Response(build_excel_report(record), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        if path.endswith('/csv'):
            out = io.StringIO()
            writer = csv.writer(out, delimiter=';')
            keys = ('severity', 'code', 'file', 'location', 'message', 'recommendation')
            writer.writerow(keys)
            for issue in record['issues']:
                writer.writerow(["'"+str(issue[k]) if str(issue[k]).lstrip().startswith(('=', '+', '-', '@')) else issue[k] for k in keys])
            return Response('\ufeff'+out.getvalue(), media_type='text/csv; charset=utf-8')
        return record
