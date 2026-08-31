#!/usr/bin/env python
"""Golden Dataset Evaluation CLI.

Usage:
  python cli.py                              # scan live, print report
  python cli.py --from-json path/to/scan.json
                                              # skip scanning, evaluate a
                                              # previously-saved scan
  python cli.py --detail                     # also print the per-document
                                              # detail table
  python cli.py --save-baseline baselines/x.json
                                              # save this run's per-field
                                              # outcomes as a baseline
  python cli.py --from-json a.json --compare-baseline baselines/x.json
                                              # diff this run against a
                                              # previously saved baseline

Requires the real stack running (ai-engine reachable at localhost:8007)
unless --from-json is given. Never modifies production code or data —
read-only against the pipeline and the golden dataset.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A vendor/business name anywhere in the 35-document dataset (or a future
# tenant's own documents) can legitimately contain non-ASCII text; Windows'
# terminal defaults to a codepage (cp1252) that can't encode arbitrary
# Unicode. Force UTF-8 on stdout so a report never crashes mid-print over
# a name it happens to contain, on any platform.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from golden_eval.runner import (
    diff_baselines, format_detail_table, format_diff, format_summary, load_baseline, load_scan_json,
    run_evaluation, save_baseline, scan_documents,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-json", type=Path, help="Evaluate a previously-saved scan instead of scanning live.")
    parser.add_argument("--detail", action="store_true", help="Also print the per-document detail table.")
    parser.add_argument("--save-baseline", type=Path, help="Save this run's outcomes as a baseline snapshot.")
    parser.add_argument("--compare-baseline", type=Path, help="Diff this run against a previously saved baseline.")
    args = parser.parse_args()

    actual = load_scan_json(args.from_json) if args.from_json else scan_documents()
    report = run_evaluation(actual)

    print(format_summary(report))
    if args.detail:
        print(format_detail_table(report))

    if args.save_baseline:
        save_baseline(report, args.save_baseline)
        print(f"Saved baseline to {args.save_baseline}")

    if args.compare_baseline:
        before = load_baseline(args.compare_baseline)
        from golden_eval.runner import report_to_baseline
        after = report_to_baseline(report)
        print()
        print(format_diff(diff_baselines(before, after)))


if __name__ == "__main__":
    main()
