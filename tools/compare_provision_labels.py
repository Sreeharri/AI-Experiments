"""Compare a provision screening run with the labels in the review workbook.

The main figure is what Qwen removed: of the provisions it marked IGNORE, how
many did the reviewer mark as relevant? Run from the AI-Experiments folder:

    .\\.venv\\Scripts\\python.exe .\\tools\\compare_provision_labels.py `
        --labels .\\EUR-Lex_100_Provision_Review.xlsx --run .\\runs\\eurlex_provisions_review100_v2

Add --baseline with the folder of an earlier run to see both runs side by side.
The workbook needs a "Review" sheet with the columns Review ID, Your label,
CELEX and Provision. Labels other than DIRECT, INDIRECT and IGNORE (blank or
UNSURE) are listed and left out of the scores.
"""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

LEVELS = ("DIRECT", "INDIRECT", "IGNORE")
COLUMNS = ("Review ID", "Your label", "CELEX", "Provision")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_labels(path):
    from openpyxl import load_workbook

    sheet = load_workbook(path, data_only=True, read_only=True)["Review"]
    header, rows = None, []
    for row in sheet.iter_rows(values_only=True):
        cells = [str(cell).strip() if cell is not None else "" for cell in row]
        if header is None:
            if all(column in cells for column in COLUMNS):
                header = {column: cells.index(column) for column in COLUMNS}
            continue
        if cells[header["Review ID"]]:
            rows.append({"id": cells[header["Review ID"]], "label": cells[header["Your label"]].upper(),
                         "celex": cells[header["CELEX"]], "target": cells[header["Provision"]]})
    if header is None:
        raise ValueError("The Review sheet needs the columns " + ", ".join(COLUMNS) + ".")
    return rows


def read_run(folder):
    """Return ({(celex, target): decision}, {(celex, target): code}) for one run folder."""
    results = read_json(folder / "relevance_export" / "results.json")
    decisions = {(celex, target): decision for celex, targets in results.items()
                 for target, decision in targets.items()}
    skipped = []
    if (folder / "not_assessable.json").is_file():
        skipped = read_json(folder / "not_assessable.json")
    elif (folder / "audit" / "run_summary.json").is_file():
        skipped = read_json(folder / "audit" / "run_summary.json").get("not_assessable_targets", [])
    return decisions, {(row["celex"], row["target"]): row["code"] for row in skipped}


def compare(labels, decisions, skipped=None, baseline=None):
    """Return (table rows, summary) for labelled provisions against one run."""
    skipped, baseline = skipped or {}, baseline or {}
    table, matrix = [], Counter()
    for row in labels:
        key = (row["celex"], row["target"])
        decision = decisions.get(key)
        level = decision["level"] if decision else None
        label = row["label"]
        if key in skipped:
            outcome = f"not assessable ({skipped[key]})"
        elif level is None:
            outcome = "no result in this run"
        elif label not in LEVELS:
            outcome = "no usable label"
        else:
            matrix[label, level] += 1
            if label == level:
                outcome = "same"
            elif level == "IGNORE":
                outcome = "REMOVED but labelled relevant"
            elif label == "IGNORE":
                outcome = "kept but labelled IGNORE"
            else:
                outcome = "relevant in both, level differs"
        table.append({"Review ID": row["id"], "CELEX": row["celex"], "Provision": row["target"],
                      "Label": label, "Qwen": level or "", "Outcome": outcome,
                      "Earlier run": (baseline.get(key) or {}).get("level", ""),
                      "Qwen reason": decision["reason"] if decision else ""})

    scored = sum(matrix.values())
    removed = sum(count for (label, level), count in matrix.items() if level == "IGNORE")
    kept = scored - removed
    summary = {
        "labelled_rows": len(labels), "scored": scored,
        "not_assessable": sum(row["Outcome"].startswith("not assessable") for row in table),
        "no_result": sum(row["Outcome"] == "no result in this run" for row in table),
        "no_usable_label": sum(row["Outcome"] == "no usable label" for row in table),
        "same_label": sum(count for (label, level), count in matrix.items() if label == level),
        "same_relevant_or_ignore": sum(count for (label, level), count in matrix.items()
                                       if (label == "IGNORE") == (level == "IGNORE")),
        "removed": removed,
        "removed_but_labelled_relevant": sum(count for (label, level), count in matrix.items()
                                             if level == "IGNORE" and label != "IGNORE"),
        "kept": kept,
        "kept_but_labelled_ignore": sum(count for (label, level), count in matrix.items()
                                        if level != "IGNORE" and label == "IGNORE"),
        "matrix": {label: {level: matrix[label, level] for level in LEVELS} for label in LEVELS},
    }
    return table, summary


def share(part, whole):
    return f"{part} of {whole}" + (f" ({100 * part / whole:.1f}%)" if whole else "")


def report(summary):
    lines = [
        f"Labelled rows: {summary['labelled_rows']}. Scored: {summary['scored']}. "
        f"Not assessable: {summary['not_assessable']}. No result: {summary['no_result']}. "
        f"No usable label: {summary['no_usable_label']}.",
        "",
        f"Removed by Qwen but labelled relevant: "
        f"{share(summary['removed_but_labelled_relevant'], summary['removed'])}   <- keep this at or below 5%",
        f"Kept by Qwen but labelled IGNORE:      {share(summary['kept_but_labelled_ignore'], summary['kept'])}",
        f"Same relevant-or-ignore decision:      {share(summary['same_relevant_or_ignore'], summary['scored'])}",
        f"Same exact label:                      {share(summary['same_label'], summary['scored'])}",
        "",
        f"{'Label / Qwen':14}" + "".join(f"{level:>10}" for level in LEVELS),
    ]
    for label in LEVELS:
        lines.append(f"{label:14}" + "".join(f"{summary['matrix'][label][level]:>10}" for level in LEVELS))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=Path, required=True, help="Review workbook (.xlsx)")
    parser.add_argument("--run", type=Path, required=True, help="Run folder to check")
    parser.add_argument("--baseline", type=Path, help="Earlier run folder shown next to the new results")
    parser.add_argument("--out", type=Path, help="CSV file to write. Default: label_comparison.csv in the run folder")
    args = parser.parse_args()
    try:
        labels = read_labels(args.labels)
        decisions, skipped = read_run(args.run)
        baseline = read_run(args.baseline)[0] if args.baseline else None
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"Comparison stopped: {exc}") from exc
    table, summary = compare(labels, decisions, skipped, baseline)
    out = args.out or args.run / "label_comparison.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print(report(summary))
    if args.baseline:
        _, earlier = compare(labels, baseline)
        print("\nEarlier run on the same rows:\n" + report(earlier))
    print(f"\nRow-by-row table: {out}")


if __name__ == "__main__":
    main()
