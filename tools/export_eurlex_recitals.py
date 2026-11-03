"""Export the retained EUR-Lex catalogue as one JSON array of titles and recitals."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from export_eurlex import (
    ROOT,
    choose_versions,
    chunks,
    connect,
    database_url,
    dict_rows,
    iso,
    load_population,
    psycopg,
    recitals_from,
    rows,
)

SELECT_VERSIONS = """
    SELECT iv.instrument_id::text AS instrument_id, iv.id::text AS id, iv.language,
           iv.version_key, iv.version_kind, iv.version_date, iv.is_current,
           iv.is_preferred, iv.quality_status, iv.fetched_at,
           coalesce(rs.recital_count, 0) AS recital_count
    FROM instrument_versions iv
    LEFT JOIN recital_sets rs ON rs.version_id = iv.id
    WHERE iv.instrument_id = ANY(%s::uuid[]) AND iv.language IN ('EN', 'DE')
    ORDER BY iv.version_key
"""

SELECT_RECITALS = """
    SELECT version_id::text, recitals_json, combined_text
    FROM recital_sets
    WHERE version_id = ANY(%s::uuid[])
"""


def batch_records(conn, acts):
    instrument_ids = [act["instrument_id"] for act in acts if act["instrument_id"]]
    versions = defaultdict(list)
    if instrument_ids:
        for version in dict_rows(conn, SELECT_VERSIONS, (instrument_ids,)):
            versions[version["instrument_id"]].append(version)
    selected = {
        instrument_id: {
            lang: recital_version
            for lang, (_, recital_version) in choose_versions(items).items()
            if recital_version is not None
        }
        for instrument_id, items in versions.items()
    }
    version_ids = [version["id"] for by_lang in selected.values() for version in by_lang.values()]
    by_version = {}
    if version_ids:
        for version_id, structured, combined in rows(conn, SELECT_RECITALS, (version_ids,)):
            by_version[version_id] = [
                recital for recital in recitals_from(structured, combined) if recital["text"].strip()
            ]

    for act in acts:
        titles = act["meta_titles"] if isinstance(act["meta_titles"], dict) else {}
        record = {
            "id": act["id"],
            "celex": act["celex"],
            "instrument_id": act["instrument_id"],
            "title_en": act["title_en"] or titles.get("EN"),
            "title_de": act["title_de"] or titles.get("DE"),
            "canonical_title": act["canonical_title"],
            "recitals_en": [],
            "recitals_de": [],
            "recital_source": {"en": None, "de": None},
        }
        for lang, version in selected.get(act["instrument_id"], {}).items():
            recitals = by_version.get(version["id"], [])
            if recitals:
                language = lang.lower()
                record[f"recitals_{language}"] = recitals
                record["recital_source"][language] = {
                    "version_key": version["version_key"],
                    "version_kind": version["version_kind"],
                    "version_date": iso(version["version_date"]),
                }
        yield record


def write_export(conn, acts, out):
    counts = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n",
        dir=out.parent, prefix=f"{out.name}.", suffix=".part", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write("[\n")
            for batch in chunks(acts, 200):
                for record in batch_records(conn, batch):
                    if counts["acts"]:
                        handle.write(",\n")
                    handle.write("  " + json.dumps(record, ensure_ascii=False))
                    counts["acts"] += 1
                    en = bool(record["recitals_en"])
                    de = bool(record["recitals_de"])
                    counts["recitals_en"] += en
                    counts["recitals_de"] += de
                    counts["neither"] += not (en or de)
                    counts["no_title"] += not any(
                        record[key] for key in ("title_en", "title_de", "canonical_title")
                    )
            handle.write("\n]\n")
            handle.close()
            temporary.replace(out)
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", help="Path to the .env containing DATABASE_URL")
    parser.add_argument("--out", type=Path, help="Output JSON file")
    sample = parser.add_mutually_exclusive_group()
    sample.add_argument("--limit", type=int, default=0, help="Export a sample spread across the catalogue")
    sample.add_argument("--celex", help="Export only these comma-separated CELEX IDs")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be positive when supplied")
    args.kept_only = True
    filename = "acts_with_recitals_sample.json" if args.limit or args.celex else "acts_with_recitals.json"
    out = args.out or ROOT / "data" / "catalogue" / filename

    url, _ = database_url(args.env)
    try:
        with connect(url) as conn:
            acts, _ = load_population(conn, args, {})
            if not acts:
                parser.exit(1, "No retained EUR-Lex acts matched. Check the database and CELEX selection.\n")
            print(f"Exporting {len(acts):,} retained EUR-Lex acts ...", flush=True)
            counts = write_export(conn, acts, out)
    except psycopg.Error as exc:
        parser.exit(1, f"Database error: {exc}\n")

    print(f"Saved {counts['acts']:,} acts to {out.resolve()}")
    print(f"With recitals: EN {counts['recitals_en']:,}, DE {counts['recitals_de']:,}")
    print(f"No stored EN or DE recitals: {counts['neither']:,}")
    print(f"No stored title in any title field: {counts['no_title']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
