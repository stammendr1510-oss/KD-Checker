"""Local source-file/dependency evidence, not attestation of loaded code or trust."""
import hashlib
from importlib.metadata import version
from pathlib import Path

DEPENDENCIES = ('fastapi','starlette','uvicorn','pydantic','python-multipart',
                'PyMuPDF','openpyxl','ezdxf')


def dependency_versions(*, with_test_transport=False):
    names = DEPENDENCIES + (('httpx',) if with_test_transport else ())
    return {name: version(name) for name in names}


def source_fingerprint():
    root = Path(__file__).resolve().parent
    paths = list(root.glob('*.py')) + list((root/'design').rglob('*.py'))
    paths += [p for p in (root/'secure_static').iterdir() if p.is_file()]
    paths += [root/'config.json', root/'requirements.txt']
    digest = hashlib.sha256()
    for name in sorted(p.relative_to(root).as_posix() for p in paths):
        content = (root/name).read_bytes()
        digest.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return digest.hexdigest()
