"""Strict synthetic interchange, not a native EPLAN exporter contract."""
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from connection_checks import Connection, Endpoint, check_connections

Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class End(StrictModel):
    device: Text
    terminal: Text


class Record(StrictModel):
    wire: Text
    cable: Annotated[str, StringConstraints(strict=True, max_length=500)]
    source: End
    target: End
    document: Text
    location: Text


class Payload(StrictModel):
    schema_version: Literal[1]
    project: Text
    revision: Text
    records: list[Record] = Field(min_length=1, max_length=1000)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_connections(raw):
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        payload = Payload.model_validate(data)
        if type(data.get('schema_version')) is not int:
            raise ValueError('Invalid schema version')
        records = [Connection(project=payload.project, revision=payload.revision,
                              source=Endpoint(**row.source.model_dump()), target=Endpoint(**row.target.model_dump()),
                              **row.model_dump(exclude={'source', 'target'})) for row in payload.records]
        files = list(dict.fromkeys(row.document for row in records))
        if len(files) > 100:
            raise ValueError('Too many source documents')
        return payload, files, check_connections(records)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid structured input') from None
