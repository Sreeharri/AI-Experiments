"""Enrich and verify all pinned acts selected for provision screening."""

import argparse
from collections import Counter
from importlib.util import find_spec
from pathlib import Path
import re
from uuid import UUID

from lxml.etree import XMLSyntaxError

from enrich_eurlex import write_json
from eurlex_enrichment.enrichment import build
from eurlex_enrichment.inputs import from_postgres, postgres_dsn, read_json, source_bytes

TARGET_COUNTS = {"ARTICLE": "articles", "ANNEX": "annexes", "TABLE": "xhtml_data_tables"}


def read_manifest(path):
    manifest = read_json(path)
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Expected a nonempty CELEX-to-version manifest.")
    for celex, version in manifest.items():
        if not re.fullmatch(r"[0-9][A-Z0-9()._-]{4,79}", celex):
            raise ValueError(f"Invalid CELEX ID: {celex!r}")
        if not isinstance(version, str) or str(UUID(version)) != version:
            raise ValueError(f"{celex}: expected a canonical version UUID.")
    return manifest


def verify_enriched(enriched, celex, version):
    document = enriched["document"]
    if enriched["schema_version"] != 1 or (document["celex"], document["version_id"]) != (celex, version):
        raise ValueError("Enriched file does not match the pinned act and version.")
    if document["language"] != "EN":
        raise ValueError("Enrichment currently requires an English source version.")
    if not re.fullmatch(r"[0-9a-f]{64}", document["source_sha256"]):
        raise ValueError("Missing or invalid source SHA-256.")
    units = enriched["units"]
    if len({unit["id"] for unit in units}) != len(units):
        raise ValueError("Duplicate unit IDs.")
    counts = Counter(unit["kind"] for unit in units)
    expected = {field: counts[kind] for kind, field in TARGET_COUNTS.items()}
    if any(enriched["summary"][field] != count for field, count in expected.items()):
        raise ValueError("Declared provision counts disagree with the enriched units.")
    return expected


def run(args):
    manifest = read_manifest(args.manifest)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        return process(args, manifest, None)

    dsn = postgres_dsn(args.env_file)
    lock = output / ".enrichment_running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("Output folder is locked. Check whether another enrichment run is active.") from exc
    try:
        return process(args, manifest, dsn)
    finally:
        lock.unlink()


def process(args, manifest, dsn):
    output = args.output_dir
    valid = 0
    written = 0
    counts = Counter()
    failures = {}
    no_targets = []
    stopped = None
    processed = 0
    try:
        for celex, version in manifest.items():
            path = output / f"{celex}_enriched.json"
            try:
                metrics = verify_enriched(read_json(path), celex, version)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if args.verify_only:
                    failures[celex] = str(exc)
                    processed += 1
                    continue
                try:
                    data = from_postgres(celex, version, dsn)
                    if data["version"]["language"] != "EN":
                        raise ValueError(f"Pinned source language is {data['version']['language']!r}.")
                    raw, origin = source_bytes(data, args.xhtml_dir, args.fetch_xhtml)
                    enriched = build(data, raw, origin)
                    metrics = verify_enriched(enriched, celex, version)
                except (OSError, ValueError, KeyError, TypeError, XMLSyntaxError) as exc:
                    failures[celex] = str(exc)
                    processed += 1
                    print(f"{celex}: {exc}", flush=True)
                    continue
                write_json(path, enriched)
                written += 1
            valid += 1
            processed += 1
            counts.update(metrics)
            if not any(metrics.values()):
                no_targets.append(celex)
            if not args.verify_only and (processed % 100 == 0 or processed == len(manifest)):
                print(f"{processed:,}/{len(manifest):,} checked, {valid:,} valid, {len(failures):,} failed.", flush=True)
    except Exception as exc:
        if exc.__class__.__module__.startswith("psycopg"):
            stopped = f"PostgreSQL read failed ({exc.__class__.__name__}) at {celex}."
        else:
            stopped = f"{exc.__class__.__name__}: {exc}"
            raise
    except KeyboardInterrupt:
        stopped = "Interrupted by user."
    finally:
        report = output / ("enrichment_verification.json" if args.verify_only else "enrichment_run_summary.json")
        write_json(report, {
            "acts_planned": len(manifest), "acts_checked": processed, "acts_valid": valid,
            "acts_written": written, "acts_failed": len(failures), "acts_pending": len(manifest) - processed,
            "provisions": {field: counts[field] for field in TARGET_COUNTS.values()},
            "acts_without_provision_targets": no_targets, "failures": failures, "error": stopped,
        })
        print(f"{valid:,}/{len(manifest):,} acts verified. Report: {report}", flush=True)
        if stopped:
            print(stopped, flush=True)
    return bool(failures or no_targets or stopped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--xhtml-dir", type=Path)
    parser.add_argument("--fetch-xhtml", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="Check saved outputs without connecting to PostgreSQL")
    args = parser.parse_args()
    if not args.verify_only and find_spec("psycopg") is None:
        parser.exit(1, "Install requirements-postgres.txt before running PostgreSQL enrichment.\n")
    try:
        raise SystemExit(int(run(args)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Corpus enrichment stopped: {exc}\n")


if __name__ == "__main__":
    main()
