# Golden Dataset Evaluation / Benchmark Framework

A QA/benchmark harness for the real 35-document June 2026 petty-cash
dataset (`test-data/june-2026-petty-cash/receipts/`) and its manually
verified ledger (`June Cash book..xlsx`, repo root). It measures the
existing OCR → extraction → classification pipeline against independently
established ground truth — it does not change, patch, or influence that
pipeline in any way. See `docs/invoice-ocr-plan.md §15` for the full audit
this framework was built under, and `golden_eval/golden_dataset.py`'s own
module docstring for exactly how each document's ground truth was
established (and where it deliberately couldn't be).

## Why this exists, not another one-off scan script

Earlier phases of this project's own audit history (`docs/invoice-ocr-plan.md`
§11-§14) each wrote a throwaway scan script to check the pipeline against
this same dataset — useful in the moment, but nothing was kept, so every
future change had to re-derive ground truth and re-write the comparison
logic from scratch. This package is that infrastructure, built once,
versioned, and reusable: the golden dataset, the comparison rules, and the
report format all live here permanently.

## Setup

```bash
cd backend/eval
pip install -e . -e ../libs/ocr -e ../libs/invoice_extraction
```

(`ocr`/`invoice_extraction` are only needed if you rebuild the golden
dataset's own ground truth from a fresh OCR pass — the evaluator itself
only needs `httpx` and `openpyxl`, both declared in `pyproject.toml`.)

## Running it

Requires the real stack up (`ai-engine` reachable at `localhost:8007`) —
same docker compose stack every other part of this project uses.

```bash
# Scan all 35 documents live through the real pipeline, print the report
python cli.py

# Also print the full per-document detail table
python cli.py --detail

# Save this run as a named baseline for future regression comparison
python cli.py --save-baseline baselines/2026-09-03-baseline.json

# Re-evaluate a previously-saved raw scan instead of re-scanning live
# (fast iteration — the scan JSON is any flat list of records each
# carrying its own "_file" key, the shape this project's earlier ad hoc
# scan scripts already used)
python cli.py --from-json /path/to/scan.json

# Diff a fresh run against a saved baseline — improvements, regressions,
# newly-missing fields, newly-incorrect fields, new false positives
python cli.py --compare-baseline baselines/2026-09-03-baseline.json
```

## What it measures, and what it deliberately does not

- **Vendor / Invoice Date / Total / Category / Document Type / Transactional**
  — the six fields `docs/invoice-ocr-plan.md`'s own audits have tracked
  across every prior phase.
- **Never invents a "correct" value.** A field the golden dataset marks
  `UNKNOWN` is excluded from accuracy in both directions — it never counts
  as a hit or a miss. A field marked `ABSENT` (a real, verified expectation
  that no value should be present, e.g. `total` on a confirmed
  non-transactional document) is different: the pipeline reporting a value
  there is a `FALSE_POSITIVE`, the single most dangerous outcome this
  benchmark tracks separately from an ordinary wrong answer.
- **Never conflates MISSING with INCORRECT.** Per the spec this framework
  was built to: `Expected 500, Actual UNKNOWN` is `MISSING`;
  `Expected 500, Actual 50` is `INCORRECT` — a materially different,
  more dangerous failure that must never be scored the same as no answer
  at all.

## Package layout

```
backend/eval/
  golden_eval/
    compare.py       — field-level comparison (CORRECT/INCORRECT/MISSING/
                        UNVERIFIABLE/FALSE_POSITIVE), numeric- and
                        date-aware, pure stdlib
    evaluate.py       — pure aggregation logic: (golden, actual) -> report;
                        zero I/O, independently unit-testable
    golden_dataset.py — the 35 documents' verified ground truth + how it
                        was established
    runner.py         — I/O layer: scans the real pipeline, adapts its
                        response shape, formats reports, saves/loads/diffs
                        baselines
  tests/              — synthetic-data tests for compare.py and evaluate.py,
                        plus sanity checks on the golden dataset itself
  baselines/          — versioned JSON snapshots for regression tracking
  cli.py              — the `python cli.py ...` entry point
```

## Golden-dataset safety

This package is never imported by, and never imports from, a production
service (`backend/services/*`). `runner.py` only *calls* the running
`ai-engine` over HTTP — the exact same request shape a real Scanner upload
makes — and imports `category_classifier.classify_category` directly from
`invoice-service`'s source (the same technique this project's earlier ad
hoc scan scripts already used), never modifying either. The golden dataset
itself contains no logic at all, only data describing what these 35
specific documents say — nothing here is a rule the pipeline follows.
