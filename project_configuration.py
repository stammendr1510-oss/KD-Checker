"""Versioned project settings with transactional admin checks and audit."""
from datetime import datetime, timezone
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scoped_history import Scope, Denied, Conflict

DEFAULT = json.loads((Path(__file__).parent/'config.json').read_text(encoding='utf-8'))

class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    require_designation_in_filename: bool
    designation_pattern: str = Field(max_length=160)
    detect_duplicate_designations: bool
    require_pdf_text: bool
    min_pdf_text_chars: int = Field(ge=0,le=10000)
    require_excel_headers: bool
    specification_headers: list[str] = Field(min_length=1,max_length=12)
    check_specification_references: bool
    check_duplicate_positions: bool
    warn_empty_dxf: bool

    @field_validator('designation_pattern')
    @classmethod
    def safe_pattern(cls,value):
        if value != DEFAULT['designation_pattern']:
            raise ValueError('Custom regex requires a bounded regex implementation')
        return value

    @field_validator('specification_headers')
    @classmethod
    def headers(cls,value):
        if any(not text.strip() or len(text)>60 for text in value) or len({s.strip().casefold() for s in value}) != len(value):
            raise ValueError('Invalid or ambiguous headers')
        return [s.strip() for s in value]

class Update(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    expected_version: int = Field(ge=0)
    settings: Settings

class ProjectConfiguration:
    def __init__(self,repo):
        self.repo = repo
        with repo.transaction(True) as con:
            columns = {r[1] for r in con.execute('PRAGMA table_info(project_config)')}
            if 'version' not in columns:
                con.execute('ALTER TABLE project_config ADD COLUMN version INTEGER NOT NULL DEFAULT 0')
            con.execute('''CREATE TABLE IF NOT EXISTS project_config_events(
                org TEXT,project TEXT,actor TEXT NOT NULL,changed_at TEXT NOT NULL,
                old_version INTEGER,new_version INTEGER,old_payload TEXT,new_payload TEXT,
                PRIMARY KEY(org,project,new_version), CHECK(new_version=old_version+1),
                FOREIGN KEY(org,project) REFERENCES projects(org,project))''')

    def read(self,identity):
        scope = Scope(identity.organization,identity.project)
        with self.repo.transaction() as con:
            self.repo._member(con,identity.subject,scope)
            row = con.execute('SELECT payload,version FROM project_config WHERE org=? AND project=?',
                              (scope.organization,scope.project)).fetchone()
            return (json.loads(row[0]),row[1]) if row else (dict(DEFAULT),0)

    def update(self,identity,data):
        update = Update.model_validate(data)
        scope = Scope(identity.organization,identity.project)
        with self.repo.transaction(True) as con:
            row = con.execute('SELECT role FROM memberships WHERE subject=? AND org=? AND project=?',
                              (identity.subject,scope.organization,scope.project)).fetchone()
            if row is None or row[0] != 'admin':
                raise Denied()
            row = con.execute('SELECT payload,version FROM project_config WHERE org=? AND project=?',
                              (scope.organization,scope.project)).fetchone()
            old, version = (json.loads(row[0]),row[1]) if row else (dict(DEFAULT),0)
            if version != update.expected_version:
                raise Conflict()
            new = update.settings.model_dump()
            if new == old:
                return new,version
            version += 1
            con.execute('''INSERT INTO project_config_events VALUES(?,?,?,?,?,?,?,?)''',
                        (scope.organization,scope.project,identity.subject,datetime.now(timezone.utc).isoformat(),
                         version-1,version,json.dumps(old,ensure_ascii=False),json.dumps(new,ensure_ascii=False)))
            con.execute('INSERT INTO project_config(org,project,payload,version) VALUES(?,?,?,?) ON CONFLICT(org,project) DO UPDATE SET payload=excluded.payload,version=excluded.version',
                        (scope.organization,scope.project,json.dumps(new,ensure_ascii=False),version))
            return new,version

    def history(self,identity):
        scope = Scope(identity.organization,identity.project)
        with self.repo.transaction() as con:
            self.repo._member(con,identity.subject,scope)
            return [dict(r) for r in con.execute('SELECT * FROM project_config_events WHERE org=? AND project=? ORDER BY new_version',
                                                (scope.organization,scope.project))]
