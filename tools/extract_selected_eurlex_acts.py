"""Extract the two EUR-Lex experiment sets from the existing recital export."""

import argparse
import csv
import io
import json
import random
from pathlib import Path
from zipfile import BadZipFile, ZipFile

SET_NAMES = ("set_1_development", "set_2_holdout")
EXPORT_MEMBER = "acts_with_recitals.json"


def read_export(path):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive, archive.open(EXPORT_MEMBER) as handle:
            return json.load(handle)
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_selection(archive, set_name):
    with archive.open(f"{set_name}.csv") as member:
        with io.TextIOWrapper(member, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if "celex" not in (reader.fieldnames or []):
                raise ValueError(f"{set_name}.csv needs a celex column")
            ids = [(row["celex"] or "").strip().upper() for row in reader]
    if not ids or "" in ids or len(ids) != len(set(ids)):
        raise ValueError(f"{set_name}.csv must contain unique, nonempty CELEX IDs")
    return ids


def extract(export_path, selection_path, output_dir):
    with ZipFile(selection_path) as selection:
        selected = {name: read_selection(selection, name) for name in SET_NAMES}
    wanted = set().union(*selected.values())
    if len(wanted) != sum(map(len, selected.values())):
        raise ValueError("The two sets contain overlapping CELEX IDs")

    by_celex = {}
    for act in read_export(export_path):
        celex = act["celex"]
        if celex not in wanted:
            continue
        if celex in by_celex:
            raise ValueError(f"Duplicate CELEX ID in the recital export: {celex}")
        by_celex[celex] = {
            "id": celex,
            "title_en": act["title_en"],
            "recitals": {"en": act["recitals_en"], "de": act["recitals_de"]},
        }

    missing = sorted(wanted - by_celex.keys())
    if missing:
        raise ValueError(f"{len(missing)} selected IDs missing from the export: {', '.join(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for seed, (name, ids) in enumerate(selected.items(), start=1):
        records = [by_celex[celex] for celex in ids]
        # The selection CSV is ordered by candidate class. Mix rows for blind review.
        random.Random(seed).shuffle(records)
        path = output_dir / f"{name}.json"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(records, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        no_recitals = sum(not any(row["recitals"].values()) for row in records)
        print(f"{path}: {len(records)} acts, {no_recitals} without stored recitals")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path, help="acts_with_recitals.json or .zip")
    parser.add_argument("--selection", required=True, type=Path, help="eurlex_relevance_400.zip")
    parser.add_argument("--out-dir", type=Path, default=Path("selected_acts"))
    args = parser.parse_args()
    try:
        extract(args.export, args.selection, args.out_dir)
    except (OSError, ValueError, KeyError, BadZipFile, csv.Error) as exc:
        parser.exit(1, f"Extraction failed: {exc}\n")


if __name__ == "__main__":
    main()
