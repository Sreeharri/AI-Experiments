"""Pin the DIRECT and INDIRECT document decisions to their screened versions."""

import argparse
from pathlib import Path
import re
from uuid import UUID

from enrich_eurlex import write_json
from eurlex_enrichment.inputs import read_json


def selected_versions(run):
    versions = {}
    counts = {}
    for level in ("DIRECT", "INDIRECT"):
        export_path = run / "relevance_export" / f"{level.lower()}.json"
        records = read_json(export_path)
        if not isinstance(records, dict):
            raise ValueError(f"{export_path.name} must map CELEX IDs to document decisions.")
        counts[level] = len(records)
        for celex, decision in records.items():
            if not re.fullmatch(r"[0-9][A-Z0-9()._-]{4,79}", celex) or celex in versions:
                raise ValueError(f"Invalid or duplicate selected CELEX ID: {celex!r}")
            if not isinstance(decision, dict) or decision.get("level") != level:
                raise ValueError(f"{celex}: unexpected decision in {export_path.name}")
            prediction = read_json(run / "predictions" / f"{celex}.json")
            if (prediction.get("celex") != celex or prediction.get("status") != "ok"
                    or (prediction.get("decision") or {}).get("level") != level):
                raise ValueError(f"{celex}: prediction disagrees with the document export.")
            try:
                versions[celex] = str(UUID(str(prediction.get("version_id"))))
            except ValueError as exc:
                raise ValueError(f"{celex}: prediction has no valid version_id.") from exc
    if not versions:
        raise ValueError("The exports contain no selected acts.")
    return versions, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Document relevance run folder")
    parser.add_argument("--out", type=Path, required=True, help="Output CELEX-to-version JSON manifest")
    args = parser.parse_args()
    try:
        versions, counts = selected_versions(args.run)
        if args.out.exists() and read_json(args.out) != versions:
            raise ValueError("The output manifest already contains a different selection. Choose another --out.")
        write_json(args.out, versions)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Manifest preparation stopped: {exc}\n")
    print(f"Pinned {len(versions):,} acts ({counts['DIRECT']:,} DIRECT, {counts['INDIRECT']:,} INDIRECT).")
    print(f"Manifest: {args.out}")


if __name__ == "__main__":
    main()
