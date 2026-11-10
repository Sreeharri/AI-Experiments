"""Export final EUR-Lex decisions from an existing corpus screening run."""

import argparse
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


LEVELS = ("DIRECT", "INDIRECT", "IGNORE")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def write_json(path, data):
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export(run):
    selected = (run / "selected_ids.txt").read_text(encoding="utf-8-sig").splitlines()
    ids = [line.strip() for line in selected if line.strip()]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("selected_ids.txt must contain unique CELEX IDs")

    groups = {level: {} for level in LEVELS}
    unresolved = 0
    for celex in ids:
        if not re.fullmatch(r"[0-9][A-Z0-9()._-]{4,79}", celex):
            raise ValueError(f"Invalid CELEX ID in selected_ids.txt: {celex!r}")
        path = run / "predictions" / f"{celex}.json"
        result = read_json(path)
        if not isinstance(result, dict) or result.get("celex") != celex:
            raise ValueError(f"Saved CELEX does not match {path}")
        if result.get("status") == "failed":
            unresolved += 1
            continue
        decision = result.get("decision")
        if result.get("status") != "ok" or not isinstance(decision, dict):
            raise ValueError(f"No final screening decision in {path}")
        level, reason, area = (decision.get(k) for k in ("level", "reason", "anchor_ids"))
        if level not in LEVELS or not isinstance(reason, str) or not isinstance(area, list):
            raise ValueError(f"Invalid decision fields in {path}")
        if any(not isinstance(anchor_id, str) for anchor_id in area):
            raise ValueError(f"Invalid business anchor ID in {path}")
        groups[level][celex] = {"level": level, "reason": reason, "area": area}

    out = run / "relevance_export"
    out.mkdir(exist_ok=True)
    for level, records in groups.items():
        path = out / f"{level.lower()}.json"
        write_json(path, records)
        print(f"{level}: {len(records):,} -> {path}")
    print(f"Unresolved excluded: {unresolved:,}. Selected acts: {len(ids):,}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Corpus screening run folder")
    args = parser.parse_args()
    try:
        export(args.run)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Export stopped: {exc}\n")


if __name__ == "__main__":
    main()
