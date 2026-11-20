"""Synthetic tests for the offline dependency manifest policy."""
from pathlib import Path
import tempfile
import unittest

from tools.dependency_policy import check_repository, normalize_name


class DependencyPolicyTests(unittest.TestCase):
    def make_repo(self, runtime="", development="", source=None, tests=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "requirements.txt").write_text(runtime, encoding="utf-8")
        (root / "requirements-dev.txt").write_text(development, encoding="utf-8")
        if source is not None:
            (root / "app.py").write_text(source, encoding="utf-8")
        if tests is not None:
            test_dir = root / "tests"
            test_dir.mkdir()
            (test_dir / "test_sample.py").write_text(tests, encoding="utf-8")
        return tmp, root

    def test_name_normalization_matches_pep_503(self):
        self.assertEqual(normalize_name("My_Package.Name"), "my-package-name")

    def test_bounded_runtime_and_dev_imports_pass(self):
        tmp, root = self.make_repo(
            runtime=("FastAPI>=0.115,<1\nPydantic>=2,<3\nPyMuPDF>=1.24,<2\n"
                     "openpyxl>=3.1,<4\nezdxf>=1.3,<2\nuvicorn>=0.30,<1\n"),
            development="pytest>=8,<10\nplaywright>=1.51,<2\n",
            source=("import fitz\nfrom fastapi import FastAPI\nimport pydantic\n"
                    "import openpyxl\nimport ezdxf\nimport uvicorn\nimport json\n"),
            tests="import pytest\nimport playwright\nimport app\n",
        )
        with tmp:
            self.assertEqual(check_repository(root), [])

    def test_missing_upper_bound_is_rejected(self):
        tmp, root = self.make_repo(runtime="fastapi>=0.115\n")
        with tmp:
            self.assertIn("unbounded:runtime:fastapi", check_repository(root))

    def test_missing_lower_bound_is_rejected(self):
        tmp, root = self.make_repo(runtime="fastapi<1\n")
        with tmp:
            self.assertIn("unbounded:runtime:fastapi", check_repository(root))

    def test_urls_and_pip_options_are_rejected(self):
        tmp, root = self.make_repo(runtime="--index-url https://example.invalid/simple\n"
                                          "https://example.invalid/pkg.whl\n")
        with tmp:
            issues = check_repository(root)
            self.assertIn("unsupported-line:runtime:1", issues)
            self.assertIn("unsupported-line:runtime:2", issues)

    def test_duplicate_normalized_names_are_rejected(self):
        tmp, root = self.make_repo(runtime="my_package>=1,<2\nmy-package>=1,<2\n")
        with tmp:
            self.assertIn("duplicate:runtime:my-package", check_repository(root))

    def test_dependency_cannot_be_declared_in_both_groups(self):
        tmp, root = self.make_repo(runtime="pytest>=8,<10\n", development="PyTest>=8,<10\n")
        with tmp:
            self.assertIn("duplicate-across-groups:pytest", check_repository(root))

    def test_undeclared_runtime_import_is_reported(self):
        tmp, root = self.make_repo(source="import imaginary_package\n")
        with tmp:
            self.assertIn("undeclared-runtime-import:imaginary_package", check_repository(root))

    def test_test_import_may_be_declared_in_dev_group(self):
        tmp, root = self.make_repo(development="pytest>=8,<10\n", tests="import pytest\n")
        with tmp:
            self.assertEqual(check_repository(root), [])

    def test_test_import_without_declaration_is_reported(self):
        tmp, root = self.make_repo(tests="import imaginary_test_package\n")
        with tmp:
            self.assertIn("undeclared-test-import:imaginary_test_package", check_repository(root))

    def test_local_modules_and_standard_library_are_not_dependencies(self):
        tmp, root = self.make_repo(source="import json\nimport local_module\n")
        with tmp:
            (root / "local_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(check_repository(root), [])

    def test_test_helpers_are_local_modules(self):
        tmp, root = self.make_repo(tests="from test_fixture import make_data\n")
        with tmp:
            (root / "tests" / "test_fixture.py").write_text("def make_data(): return {}\n", encoding="utf-8")
            self.assertEqual(check_repository(root), [])

    def test_malformed_python_source_fails_closed(self):
        tmp, root = self.make_repo(source="def broken(:\n")
        with tmp:
            self.assertIn("undeclared-runtime-import:<unreadable-source>", check_repository(root))


if __name__ == "__main__":
    unittest.main()
