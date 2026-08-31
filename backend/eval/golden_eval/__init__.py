"""Golden Dataset Evaluation / Benchmark framework for the June 2026 petty
cash dataset (see ../README.md and test-data/june-2026-petty-cash/README.md).

This package is a QA/benchmark harness, not a production dependency — no
service imports it, and it is never installed into a service's Docker
image. It exists to *measure* the existing OCR/extraction/classification
pipeline against manually-verified ground truth, not to change it.
"""
