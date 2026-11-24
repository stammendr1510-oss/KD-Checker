import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineering_benchmark import evaluate_run, load_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Эталонная проверка извлечения и диагностики КД.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-json", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        with args.run_json.open(encoding="utf-8") as stream:
            result = json.load(stream)
        if not isinstance(result, dict):
            raise ValueError("Результаты должны быть объектом JSON.")
        if "extracted_summary" not in result or "issues" not in result:
            raise ValueError("Требуются разделы extracted_summary и issues.")
        report = evaluate_run(manifest, result["extracted_summary"], result["issues"])
    except (OSError, ValueError, TypeError) as exc:
        print(f"Ошибка эталонной проверки: {exc}", file=sys.stderr)
        return 2

    print(report.render_russian_summary())
    if args.strict and (report.false_positive_count > 0 or report.diagnostic_recall < 1.0):
        print("Строгая проверка не пройдена.")
        return 1
    if args.strict:
        print("Строгая проверка пройдена.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
