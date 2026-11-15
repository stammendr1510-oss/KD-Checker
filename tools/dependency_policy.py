"""Offline checks for bounded, declared Python dependencies.

This is intentionally a manifest/source audit, not a resolver or lockfile tool.
It never contacts package indexes and uses only the Python standard library.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*((?:===|==|~=|>=|<=|!=|>|<)\s*"
    r"[A-Za-z0-9][A-Za-z0-9.*+!_-]*(?:\s*,\s*(?:===|==|~=|>=|<=|!=|>|<)\s*"
    r"[A-Za-z0-9][A-Za-z0-9.*+!_-]*)*)\s*$"
)
_BOUND = re.compile(r"(>=|>|~=|===|==|<=|<|!=)\s*([A-Za-z0-9][A-Za-z0-9.*+!_-]*)")

# Import names that intentionally differ from their distribution names.
_IMPORT_DISTRIBUTION = {"fitz": "pymupdf"}


@dataclass(frozen=True)
class Requirement:
    name: str
    normalized_name: str
    line: int


def normalize_name(name: str) -> str:
    """Normalize a distribution name using the PEP 503 separator rule."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_requirements(path: Path, group: str, issues: list[str]) -> dict[str, Requirement]:
    found: dict[str, Requirement] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        issues.append(f"missing-manifest:{group}:{path.name}")
        return found

    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT.fullmatch(line)
        if not match:
            issues.append(f"unsupported-line:{group}:{line_number}")
            continue
        name, specifier = match.groups()
        if not _NAME.fullmatch(name):  # defensive; the requirement regex already narrows this
            issues.append(f"invalid-name:{group}:{line_number}")
            continue
        normalized = normalize_name(name)
        if normalized in found:
            issues.append(f"duplicate:{group}:{normalized}")
            continue
        bounds = _BOUND.findall(specifier)
        has_lower = any(operator in (">=", ">", "~=", "==", "===") for operator, _ in bounds)
        has_upper = any(operator == "<" for operator, _ in bounds)
        if not has_lower or not has_upper:
            issues.append(f"unbounded:{group}:{normalized}")
        found[normalized] = Requirement(name, normalized, line_number)
    return found


def _source_imports(root: Path, folder: Path | None = None) -> set[str]:
    """Collect top-level imports while leaving relative and local imports alone."""
    base = root if folder is None else folder
    if not base.exists():
        return set()
    local_modules = {path.stem for path in root.glob("*.py")}
    if folder is not None:
        local_modules.update(path.stem for path in base.rglob("*.py"))
    local_packages = {
        path.name for path in root.iterdir()
        if path.is_dir() and any(path.rglob("*.py"))
    }
    imported: set[str] = set()
    sources = base.rglob("*.py") if folder is not None else base.glob("*.py")
    for path in sources:
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            imported.add("<unreadable-source>")
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])
    return imported - local_modules - local_packages


def check_repository(root: str | Path) -> list[str]:
    """Return stable policy issue codes for a repository root."""
    root = Path(root)
    issues: list[str] = []
    runtime = _read_requirements(root / "requirements.txt", "runtime", issues)
    development = _read_requirements(root / "requirements-dev.txt", "development", issues)
    for name in sorted(runtime.keys() & development.keys()):
        issues.append(f"duplicate-across-groups:{name}")

    declarations = runtime | development
    known_stdlib = sys.stdlib_module_names
    runtime_imports = _source_imports(root)
    for module in sorted(runtime_imports):
        if module.startswith("_") or module in known_stdlib:
            continue
        distribution = normalize_name(_IMPORT_DISTRIBUTION.get(module, module))
        if distribution not in runtime:
            issues.append(f"undeclared-runtime-import:{module}")

    tests = root / "tests"
    test_imports = _source_imports(root, tests)
    for module in sorted(test_imports):
        if module.startswith("_") or module in known_stdlib:
            continue
        distribution = normalize_name(_IMPORT_DISTRIBUTION.get(module, module))
        if distribution not in declarations:
            issues.append(f"undeclared-test-import:{module}")
    return sorted(set(issues))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    issues = check_repository(root)
    if issues:
        print("Dependency policy failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Dependency policy passed (bounded manifests and declared imports).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
