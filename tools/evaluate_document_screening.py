"""Evaluate document retention: DIRECT and INDIRECT are both RELEVANT.

Reads the authoritative Label column, joins predictions by ID, and never changes
Qwen decisions. Only openpyxl is needed for XLSX input; CSV input uses stdlib.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LEVELS = {"DIRECT", "INDIRECT", "IGNORE"}
DETAIL_FIELDS = ["id", "title", "label", "true_class", "predicted_level",
                 "predicted_class", "outcome", "reason", "anchor_ids",
                 "prediction_status", "issue", "label_row", "prediction_file"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def level(value):
    value = str(value or "").strip().upper()
    if value not in LEVELS:
        raise ValueError(f"Expected DIRECT, INDIRECT or IGNORE; got {value!r}")
    return value


def binary(value):
    return "IGNORE" if value == "IGNORE" else "RELEVANT"


def read_labels(path, sheet="Labels", id_column="ID", label_column="Label"):
    """Read only the nominated truth column. Never use LLM/Astro columns."""
    path = Path(path)
    book = None
    if path.suffix.lower() == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("Install XLSX reader: python -m pip install openpyxl") from exc
        book = load_workbook(path, read_only=True, data_only=True)
        if sheet not in book.sheetnames:
            book.close()
            raise ValueError(f"Sheet {sheet!r} not found")
        rows = book[sheet].values
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        rows = iter(rows)
    else:
        raise ValueError("Labels must be .xlsx or UTF-8 .csv")
    try:
        header = [str(x or "").strip().casefold() for x in next(rows, [])]
        def column(name, required=True):
            positions = [i for i, value in enumerate(header) if value == name.casefold()]
            if len(positions) > 1 or (required and not positions):
                raise ValueError(f"Expected exactly one {name!r} column")
            return positions[0] if positions else None
        id_index = column(id_column)
        label_index = column(label_column)
        title_index = column("Title", required=False)
        result = {}
        for number, row in enumerate(rows, 2):
            def cell(index):
                return row[index] if index is not None and index < len(row) else None
            identifier = str(cell(id_index) or "").strip().upper()
            raw_label = cell(label_index)
            if not identifier:
                # Evidence continuation rows have merged/blank ID and Label cells.
                if raw_label is not None and str(raw_label).strip():
                    raise ValueError(f"Row {number}: label without an ID")
                continue
            if identifier in result:
                raise ValueError(f"Duplicate label ID {identifier} at row {number}")
            try:
                truth = level(raw_label)
            except ValueError as exc:
                raise ValueError(f"Row {number}, {identifier}: {exc}") from exc
            result[identifier] = {"id": identifier, "title": str(cell(title_index) or ""),
                                  "label": truth, "label_row": number}
        if not result:
            raise ValueError("No labelled acts found")
        return result
    finally:
        if book is not None:
            book.close()


def fraction(numerator, denominator):
    return numerator / denominator if denominator else None


def wilson(successes, total):
    """Two-sided 95% Wilson interval; does not establish population representativeness."""
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / divisor
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return {"lower": max(0, centre - half), "upper": min(1, centre + half)}


def calculate_metrics(tp, fp, fn, tn):
    precision = fraction(tp, tp + fp)
    recall = fraction(tp, tp + fn)
    specificity = fraction(tn, tn + fp)
    return {
        "precision": precision, "recall": recall,
        "accuracy": fraction(tp + tn, tp + fp + fn + tn),
        "f1": fraction(2 * tp, 2 * tp + fp + fn),
        "specificity": specificity, "negative_predictive_value": fraction(tn, tn + fn),
        "false_negative_rate": fraction(fn, tp + fn),
        "false_positive_rate": fraction(fp, tn + fp),
        "balanced_accuracy": ((recall + specificity) / 2
                              if recall is not None and specificity is not None else None),
        "retained_rate": fraction(tp + fp, tp + fp + fn + tn),
        "discarded_rate": fraction(tn + fn, tp + fp + fn + tn),
        "precision_ci95": wilson(tp, tp + fp),
        "recall_ci95": wilson(tp, tp + fn),
        "accuracy_ci95": wilson(tp + tn, tp + fp + fn + tn),
    }


def evaluate(labels, run):
    run = Path(run)
    if not run.is_dir():
        raise ValueError(f"Run folder not found: {run}")
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.exists() else {}
    if not isinstance(manifest, dict):
        raise ValueError("Run manifest must be a JSON object")
    selected = manifest.get("selected_celex")
    if selected is not None:
        if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected):
            raise ValueError("Invalid selected_celex in manifest")
        selected = [x.strip().upper() for x in selected]
        if len(selected) != len(set(selected)):
            raise ValueError("Duplicate IDs in run manifest")
    files = {}
    # Filename is the join key used by the runner; metadata files are not predictions.
    for path in run.glob("*.json"):
        identifier = path.stem.upper()
        if identifier not in labels:
            continue
        if identifier in files:
            raise ValueError(f"Duplicate prediction filenames for {identifier}")
        files[identifier] = path
    counts = Counter()
    rows = []
    fingerprints = hashlib.sha256()
    raw_predictions = Counter()
    for identifier, truth in labels.items():
        row = dict(truth, true_class=binary(truth["label"]), predicted_level="",
                   predicted_class="", outcome="UNRESOLVED", reason="", anchor_ids="",
                   prediction_status="", issue="", prediction_file="")
        path = files.get(identifier)
        if selected is not None and identifier not in selected:
            row["issue"] = "ID absent from run manifest selection"
        elif path is None:
            row["issue"] = "Prediction file missing"
        else:
            row["prediction_file"] = str(path.resolve())
            fingerprints.update(identifier.encode())
            fingerprints.update(bytes.fromhex(sha256(path)))
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("Prediction must be a JSON object")
                row["prediction_status"] = str(value.get("status", "missing"))
                if value.get("celex") is not None and str(value["celex"]).strip().upper() != identifier:
                    raise ValueError("Prediction celex disagrees with filename")
                if value.get("status") != "ok":
                    raise ValueError(f"Prediction status is {row['prediction_status']!r}")
                decision = value.get("decision")
                if not isinstance(decision, dict):
                    raise ValueError("Missing decision object")
                predicted = level(decision.get("level"))
                row.update(predicted_level=predicted, predicted_class=binary(predicted),
                           reason=str(decision.get("reason", "")),
                           anchor_ids=json.dumps(decision.get("anchor_ids", []), ensure_ascii=False))
                relevant = row["true_class"] == "RELEVANT"
                retained = row["predicted_class"] == "RELEVANT"
                row["outcome"] = ("TP" if retained else "FN") if relevant else ("FP" if retained else "TN")
                raw_predictions[predicted] += 1
            except (ValueError, TypeError, OSError) as exc:
                row["issue"] = str(exc)
        counts[row["outcome"]] += 1
        rows.append(row)
    total = len(labels)
    unresolved = counts["UNRESOLVED"]
    classified = total - unresolved
    matrix = {key: counts[key] for key in ("TP", "FP", "FN", "TN")}
    metrics = calculate_metrics(counts["TP"], counts["FP"], counts["FN"], counts["TN"])
    truth_counts = Counter(row["label"] for row in rows)
    relevant_total = sum(row["true_class"] == "RELEVANT" for row in rows)
    ignored_total = total - relevant_total
    extra = sorted(set(selected or []) - set(labels))
    extra_files = sorted(path.stem for path in run.glob("*.json")
                         if path.stem.upper() not in labels and path.stem.upper().startswith("3"))
    report = {
        "evaluation": "binary document retention", "positive_class": "RELEVANT",
        "mapping": {"DIRECT": "RELEVANT", "INDIRECT": "RELEVANT", "IGNORE": "IGNORE"},
        "complete": unresolved == 0, "labelled_acts": total,
        "classified_acts": classified, "unresolved_acts": unresolved,
        "prediction_coverage": classified / total,
        "truth_counts": {key: truth_counts[key] for key in sorted(LEVELS)},
        "truth_binary_counts": {"RELEVANT": relevant_total, "IGNORE": ignored_total},
        "prediction_counts": {key: raw_predictions[key] for key in sorted(LEVELS)},
        "confusion_matrix": matrix,
        # Incomplete runs cannot silently get headline scores over an easier subset.
        "metrics": metrics if not unresolved else None,
        "classified_only_metrics": metrics if unresolved else None,
        "end_to_end_relevant_retention": fraction(counts["TP"], relevant_total),
        "unresolved_relevant_acts": sum(r["outcome"] == "UNRESOLVED" and r["true_class"] == "RELEVANT" for r in rows),
        "baseline_always_ignore_accuracy": ignored_total / total,
        "majority_class_accuracy": max(relevant_total, ignored_total) / total,
        "extra_run_selection_ids": extra, "extra_run_prediction_ids": extra_files,
        "provenance": {"prediction_files_sha256": fingerprints.hexdigest(),
                       "manifest_sha256": sha256(manifest_path) if manifest_path.exists() else None,
                       "run_settings": {key: manifest.get(key) for key in (
                           "mode", "model", "as_of", "input_sha256", "anchor_sha256",
                           "prompt_sha256", "input_budget", "max_chunks", "context_limit")}},
        "limitations": [
            "Scores use only the nominated ground-truth Label column; other review columns are not overrides.",
            "DIRECT/INDIRECT differences are not errors. Reasons and anchor quality are not scored.",
            "Technical failures remain unresolved; they are never converted to IGNORE.",
            "This development sample was filtered for evidence availability; it does not represent all acts.",
            "Wilson intervals describe sample uncertainty, not guarantees or annotation correctness.",
            "Changes tuned on these labels require a separate held-out evaluation.",
        ],
    }
    return report, rows


def csv_write(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # CSVs are opened in Excel; prevent text being executed as formulas.
            safe = {key: ("'" + value if isinstance(value, str) and
                          value.lstrip().startswith(("=", "+", "-", "@")) else value)
                    for key, value in row.items()}
            writer.writerow(safe)


def percent(value):
    return "N/A" if value is None else f"{value:.2%}"


def markdown(report, rows):
    matrix = report["confusion_matrix"]
    lines = ["# Document relevance evaluation", "",
             "DIRECT + INDIRECT = RELEVANT. The Label column is ground truth.", "",
             f"Coverage: {report['classified_acts']}/{report['labelled_acts']} acts; unresolved: {report['unresolved_acts']}.", "",
             "| Ground truth | Retained | Ignored |", "|---|---:|---:|",
             f"| RELEVANT | {matrix['TP']} (TP) | {matrix['FN']} (FN) |",
             f"| IGNORE | {matrix['FP']} (FP) | {matrix['TN']} (TN) |", ""]
    metrics = report["metrics"]
    if metrics is None:
        lines += ["**Incomplete run. Headline scores are withheld; classified-only scores are in metrics.json.**", ""]
    else:
        lines += ["| Metric | Result |", "|---|---:|"]
        for key in ("precision", "recall", "accuracy", "f1", "specificity",
                    "false_negative_rate", "false_positive_rate", "balanced_accuracy",
                    "negative_predictive_value", "retained_rate", "discarded_rate"):
            lines.append(f"| {key.replace('_', ' ').capitalize()} | {percent(metrics[key])} |")
        interval = metrics["recall_ci95"]
        if interval:
            lines += ["", f"Recall 95% Wilson interval: {percent(interval['lower'])}–{percent(interval['upper'])}."]
    lines += ["", f"Always-IGNORE accuracy baseline: {percent(report['baseline_always_ignore_accuracy'])} (recall 0% when relevant acts exist).", "",
              "## Missed relevant acts", ""]
    missed = [row for row in rows if row["outcome"] == "FN"]
    lines += [f"- {row['id']}: {row['title']}" for row in missed] or ["None among classified acts."]
    lines += ["", "## Interpretation", "",
              "Precision = TP/(TP+FP). Recall = TP/(TP+FN). Accuracy = (TP+TN)/N.",
              "F1 = 2TP/(2TP+FP+FN). Zero denominators are reported as null/N/A.",
              "Prioritize recall for missed acts, and precision for unnecessary downstream work.", ""]
    lines += [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="Folder containing one CELEX.json prediction per act")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sheet", default="Labels")
    parser.add_argument("--id-column", default="ID")
    parser.add_argument("--label-column", default="Label")
    args = parser.parse_args(argv)
    try:
        labels = read_labels(args.labels, args.sheet, args.id_column, args.label_column)
        report, rows = evaluate(labels, args.run)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["provenance"].update(labels_file=str(args.labels.resolve()),
                                    labels_file_sha256=sha256(args.labels),
                                    sheet=args.sheet, label_column=args.label_column,
                                    run_folder=str(args.run.resolve()))
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                         allow_nan=False) + "\n", encoding="utf-8")
        (args.out / "report.md").write_text(markdown(report, rows), encoding="utf-8")
        csv_write(args.out / "per_act.csv", rows, DETAIL_FIELDS)
        for name, outcome in (("false_negatives", "FN"), ("false_positives", "FP"), ("unresolved", "UNRESOLVED")):
            csv_write(args.out / f"{name}.csv", [row for row in rows if row["outcome"] == outcome], DETAIL_FIELDS)
        csv_write(args.out / "ground_truth_snapshot.csv", rows, ["id", "title", "label", "true_class", "label_row"])
        print(f"Evaluated {report['classified_acts']}/{len(labels)} acts. {report['confusion_matrix']}")
        if report["complete"]:
            print(" | ".join(f"{name}: {percent(report['metrics'][name])}" for name in ("precision", "recall", "accuracy", "f1")))
        else:
            print("Incomplete evaluation: inspect unresolved.csv; headline metrics withheld.")
        print(f"Reports: {args.out.resolve()}")
        return 0 if report["complete"] else 2
    except (ValueError, OSError, KeyError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
