#!/usr/bin/env python3
"""Report precision/recall for a sanitized privacy-safe gate adjudication dataset."""

import collections
import json
import pathlib
import sys


def metrics(data):
    flags = set(data["candidate_flags"])
    positive = data["positive_outcome"]
    grouped = collections.defaultdict(list)
    for row in data["rows"]:
        grouped[row["gate_version"]].append(row)
    result = {}
    for version, rows in sorted(grouped.items()):
        tp = sum(row["flag"] in flags and row["human_outcome"] == positive for row in rows)
        fp = sum(row["flag"] in flags and row["human_outcome"] != positive for row in rows)
        fn = sum(row["flag"] not in flags and row["human_outcome"] == positive for row in rows)
        result[version] = {
            "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
        }
    return result


def main(args):
    if len(args) != 1:
        print("usage: gate_benchmark.py DATASET.json", file=sys.stderr)
        return 2
    try:
        data = json.loads(pathlib.Path(args[0]).read_text())
        result = metrics(data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"invalid benchmark dataset: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
