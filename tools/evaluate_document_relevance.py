"""Compare saved decisions with independent human labels in a CSV."""
import argparse
import csv
import json
from pathlib import Path

LEVELS = ("DIRECT", "INDIRECT", "IGNORE")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True, help="CSV columns celex,label")
    args = parser.parse_args()
    confusion = {label: {key: 0 for key in (*LEVELS, "NEEDS_REVIEW", "FAILED", "MISSING")}
                 for label in LEVELS}
    seen = set()
    with args.labels.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"celex", "label"} <= set(reader.fieldnames or []):
            parser.error("CSV requires celex,label headers")
        for row in reader:
            celex, gold = row["celex"].strip(), row["label"].strip().upper()
            gold = "IGNORE" if gold == "IRRELEVANT" else gold
            if gold not in LEVELS or celex in seen or not celex.isalnum():
                parser.error("Invalid label/CELEX or duplicate CELEX")
            seen.add(celex)
            file = args.run / f"{celex}.json"
            if not file.exists():
                prediction = "MISSING"
            else:
                result = json.loads(file.read_text(encoding="utf-8"))
                prediction = "FAILED" if result["status"] == "failed" else result.get("decision", {}).get("level")
                if prediction not in confusion[gold]:
                    parser.error(f"No classification available for {celex}")
            confusion[gold][prediction] += 1
    total = len(seen)
    if not total:
        parser.error("No labels found")
    relevant = sum(sum(confusion[label].values()) for label in ("DIRECT", "INDIRECT"))
    missed = sum(confusion[label]["IGNORE"] for label in ("DIRECT", "INDIRECT"))
    irrelevant = sum(confusion["IGNORE"].values())
    reviews = sum(row["NEEDS_REVIEW"] for row in confusion.values())
    failures = sum(row["FAILED"] + row["MISSING"] for row in confusion.values())
    print(json.dumps({
        "labelled": total,
        "confusion_matrix_human_rows": confusion,
        "relevant_wrongly_ignored": missed,
        "relevant_wrongly_ignored_rate": missed / relevant if relevant else None,
        "irrelevant_correctly_removed_rate": confusion["IGNORE"]["IGNORE"] / irrelevant if irrelevant else None,
        "exact_label_accuracy_counting_reviews_as_unresolved": sum(confusion[k][k] for k in LEVELS) / total,
        "review_rate": reviews / total,
        "failed_or_missing": failures,
        "note": "Results describe the labelled sample. They do not estimate population performance without a suitable sampling design."
    }, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
