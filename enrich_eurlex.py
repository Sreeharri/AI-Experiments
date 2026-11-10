"""Generate one version-pinned enrichment JSON per requested English act."""

import argparse
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from uuid import UUID

from lxml.etree import XMLSyntaxError

from eurlex_enrichment.enrichment import build, evidence_for
from eurlex_enrichment.inputs import from_json, from_postgres, postgres_dsn, read_json, source_bytes

ROOT = Path(__file__).resolve().parent


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                            dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    run = modes.add_parser("run", help="Read source data and produce enriched JSON")
    run.add_argument("--source", choices=("json", "postgres"), default="json")
    run.add_argument("--env-file", type=Path, help="Use DATABASE_URL from this .env file in PostgreSQL mode")
    run.add_argument("--input-dir", type=Path, default=ROOT / "input")
    run.add_argument("--output-dir", type=Path, default=ROOT / "output")
    run.add_argument("--manifest", type=Path, default=ROOT / "pilot_acts.json")
    run.add_argument("--celex", nargs="+", help="Limit the run to these manifest CELEX IDs")
    run.add_argument("--xhtml-dir", type=Path)
    run.add_argument("--fetch-xhtml", action="store_true", help="Allow official URL fallback")
    show = modes.add_parser("evidence", help="Assemble one reviewable evidence pack")
    show.add_argument("--input", type=Path, required=True, help="An enriched JSON file")
    show.add_argument("--unit", required=True, help="Exact unit ID or label, such as Article 6 or BAT 17")
    show.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "evidence":
            write_json(args.output, evidence_for(read_json(args.input), args.unit))
            print(f"Wrote {args.output}")
            return 0

        if args.env_file is not None and args.source != "postgres":
            raise ValueError("--env-file requires --source postgres")
        dsn = postgres_dsn(args.env_file) if args.source == "postgres" else None
        manifest = read_json(args.manifest)
        celex_ids = args.celex or list(manifest)
        if not celex_ids or len(set(celex_ids)) != len(celex_ids):
            raise ValueError("The CELEX selection must be nonempty and unique")
        for celex in celex_ids:
            if not re.fullmatch(r"[0-9][A-Z0-9()._-]{4,79}", celex) or celex not in manifest:
                raise ValueError(f"Invalid or missing CELEX ID in manifest: {celex!r}")
            UUID(manifest[celex])
        summaries = {}
        for celex in celex_ids:
            version_id = manifest[celex]
            data = (from_json(args.input_dir, celex, version_id) if args.source == "json"
                    else from_postgres(celex, version_id, dsn))
            if data["version"]["language"] != "EN":
                raise ValueError(f"{celex}: this pilot requires the English version")
            raw, origin = source_bytes(data, args.xhtml_dir, args.fetch_xhtml)
            enriched = build(data, raw, origin)
            out = args.output_dir / f"{celex}_enriched.json"
            write_json(out, enriched)
            summaries[celex] = enriched["summary"]
            print(f"{celex}: {json.dumps(enriched['summary'])}\n  {out}", flush=True)
        write_json(args.output_dir / "run_summary.json", summaries)
        return 0
    except (OSError, ValueError, KeyError, XMLSyntaxError) as exc:
        print(f"Enrichment stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if exc.__class__.__module__.startswith("psycopg"):
            print(f"PostgreSQL read failed ({exc.__class__.__name__}): {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
