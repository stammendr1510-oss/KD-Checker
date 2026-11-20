# Offline dependency policy

`tools/dependency_policy.py` is a standard-library-only check run before pytest in
CI. It validates both requirements manifests and statically scans project and test
imports. Runtime imports must be covered by `requirements.txt`; test-only imports
may be covered by either manifest. Distribution names are normalized using the
PEP 503 separator rule, and `fitz` is mapped to its `PyMuPDF` distribution.

Each direct requirement must use the repository's simple, bounded form with a
lower bound and an exclusive upper bound (for example `package>=1.2,<2`). Duplicate
normalized names, duplicated runtime/development declarations, pip options,
editable installs, and index/VCS/direct-URL references are rejected. This keeps
the build on the configured package index and prevents unbounded major-version
drift. The existing Pydantic v2 import is now explicitly declared as a runtime
dependency instead of relying only on FastAPI's transitive dependency.

This is not a lockfile, resolver, vulnerability scanner, license review, or proof
of hashes. It does not download packages, inspect local/production data, or make
network requests. The constraints remain ranges so both supported operating
systems use the same declared compatibility policy without introducing a new
dependency-management service.

Run locally with:

```text
python tools/dependency_policy.py
python -m unittest tests.test_dependency_policy -v
```
