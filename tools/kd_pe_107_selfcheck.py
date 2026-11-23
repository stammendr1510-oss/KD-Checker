"""Offline KD-PE-107 reference self-check.

Runs five artificial scenarios through the real cross-check pipeline and prints
a Markdown effectiveness table.  Exit code 0 only when every scenario passes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kd_pe_107_reference import all_passed, render_table, run_scenarios  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="KD-PE-107 synthetic reference self-check")
    parser.add_argument("--json", type=Path, help="Write a machine-readable report to this path.")
    args = parser.parse_args(argv)

    results = run_scenarios()
    print(render_table(results))
    if args.json:
        payload = {
            "kind": "kd-pe-107-reference-selfcheck-v1",
            "synthetic_only": True,
            "scenarios": results,
            "verdict": "PASS" if all_passed(results) else "FAIL",
        }
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if all_passed(results) else 1


if __name__ == "__main__":
    sys.exit(main())
