"""Compare change-explanation pilot runs side by side (for example v5, v6 and the v1.7 replay).

    python tools/compare_change_pilots.py v5=runs/eurlex_changes_pilot_v5 v6=runs/eurlex_changes_pilot_v6 ^
        v7=runs/eurlex_changes_pilot_v7 --ids config/eurlex_changes_pilot_v4_ids.txt

Writes compare.csv next to the last run (one row per item: act, place, and per run the outcome, headline and
error) and prints the outcome counts per run. Read-only.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import sys

OUTCOMES = ("EXPLAINED", "PARTIAL", "FAILED", "MISSING_EVIDENCE", "SCOPE_REVIEW", "NOT_RUN")


def outcome(record: dict | None) -> str:
    """The outcome of one saved item, for every format since v1.4."""
    if not record:
        return "NOT_RUN"
    if record.get("outcome"):
        return record["outcome"]
    if record.get("scope_review"):
        return "SCOPE_REVIEW"
    if record.get("no_text"):
        return "MISSING_EVIDENCE"
    if "failure" in record:
        return "FAILED"
    explanation = record.get("explanation") or {}
    if explanation:
        return explanation.get("outcome") or ("PARTIAL" if explanation.get("missing_changes") else "EXPLAINED")
    return "NOT_RUN"


def load(folder: Path) -> dict[str, dict]:
    items = {}
    for path in sorted((folder / "items").glob("*.json")):
        try:
            items[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="name=folder, in the order to show")
    parser.add_argument("--ids", type=Path, help="Only these item IDs (one per line), in this order.")
    parser.add_argument("--out", type=Path, help="CSV file (default: compare.csv in the last run folder).")
    args = parser.parse_args(argv)
    runs = []
    for value in args.runs:
        name, _, folder = value.partition("=")
        if not folder:
            parser.error(f"Give runs as name=folder, not {value!r}")
        if not (Path(folder) / "items").is_dir():
            parser.error(f"{folder} has no items folder")
        runs.append((name, load(Path(folder))))
    ids = args.ids.read_text(encoding="utf-8-sig").split() if args.ids else \
        sorted({item for _, items in runs for item in items})
    out = args.out or Path(args.runs[-1].partition("=")[2]) / "compare.csv"
    rows, counts = [], {name: Counter() for name, _ in runs}
    for item_id in ids:
        known = next((items[item_id] for _, items in runs if item_id in items), {})
        facts = known.get("facts") or {}
        row = [item_id, (facts.get("changed") or {}).get("celex", ""), facts.get("provision") or facts.get("relation", ""),
               (facts.get("changing") or {}).get("celex", "")]
        for name, items in runs:
            record = items.get(item_id)
            result = outcome(record)
            counts[name][result] += 1
            explanation = (record or {}).get("explanation") or {}
            row += [result, explanation.get("headline", ""), ((record or {}).get("failure") or "")[:300]]
        rows.append(row)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["item_id", "changed_act", "provision", "changing_act"] +
                        [f"{name} {column}" for name, _ in runs for column in ("outcome", "headline", "error")])
        writer.writerows(rows)
    width = max(len(name) for name, _ in runs) + 2
    print(f"{len(ids)} items\n" + "outcome".ljust(18) + "".join(name.rjust(width) for name, _ in runs))
    for result in OUTCOMES:
        print(result.ljust(18) + "".join(str(counts[name][result]).rjust(width) for name, _ in runs))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
