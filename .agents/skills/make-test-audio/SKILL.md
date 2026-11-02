---
name: make-test-audio
description: Use when testing the bounce sorter — how to check the plumbing with synthetic audio and how to measure drum-detection accuracy on the user's labelled real copies. Never test on the real Downloads folder.
---

# Testing the bounce sorter

There are two kinds of test. Fake audio only proves the code *works*. Only real labelled
bounces prove it *sorts correctly*.

## 1. Accuracy (do this after any change to drums.py)
The user copies real bounces into `test_exports/real/beats/` (has drums) and
`test_exports/real/loops/` (no drums). These are read-only.

```bash
.venv/bin/python scripts/check_drums.py            # report + re-learn drum_model.json
.venv/bin/python scripts/check_drums.py --no-save  # report only
```
- The goal is **0 WRONG**. A REVIEW (unsure) is acceptable; WRONG is not.
- For a WRONG file, look at its features (what `drums.analyse` returns) next to the other
  files, find the measurement that misleads, and fix the feature rather than the threshold.
- Report the "Learned model" numbers. Each file there is judged without having been learned from.

## 2. Plumbing (moves, names, collisions, log, window)
```bash
.venv/bin/python scripts/make_bounce_tests.py --clean   # synthetic files -> test_exports/bounces/
.venv/bin/python sorter.py --folder test_exports/bounces  # preview
```
To test real moves, copy that folder to a scratch location and sort the copy. Delete
the synthetic files afterwards (the user asked not to keep them).

`scripts/make_test_files.py` (silent files) belongs to the old tag/length sorter, `organize.py`.
