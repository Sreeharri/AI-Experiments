"""Find the acts whose enriched file needs the repair, and check the result.

Step 1, find the acts (reads the existing enriched files, no database, no model):

    python tools/repair_enrichment_gaps.py find

Step 2, enrich these acts again into a new folder (uses the database):

    python enrich_eurlex_corpus.py --manifest runs/eurlex_repair_v1/repair_manifest.json
        --output-dir output_db_corpus_repair

Step 3, check what the repair achieved and write the manifest for screening:

    python tools/repair_enrichment_gaps.py check

The check prints the screening command. Acts that still have no articles are
listed in repair_report.json; acts with very little stored text have to be
fetched again from EUR-Lex.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from enrich_eurlex_corpus import PROVISION_METRICS, process, read_manifest, verify_enriched  # noqa: E402
from eurlex_enrichment.repair import GLYPH_CODE, merge_false_headings  # noqa: E402
from run_eurlex_provision_corpus import SMOKE_ACTS  # noqa: E402

SHORT_TEXT = 2000   # characters; less than this in an act without articles means only a page was stored


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stored_rows(enriched):
    """The enriched elements in the shape of the database rows they were built from."""
    return [{"id": e["id"], "element_type": e["kind"], "identifier": e["label"], "ordinal": e["ordinal"],
             "parent_id": e["parent_id"], "heading": e.get("heading"), "body_text": e.get("text") or ""}
            for e in enriched["elements"].values() if "recovery_source" not in e]


def inspect(enriched):
    """Return the reasons why an enriched act needs the repair, with the facts behind them."""
    kinds = Counter(unit["kind"] for unit in enriched["units"])
    provisions = kinds["ARTICLE"] + kinds["ANNEX"] + kinds["TABLE"]
    texts = [e.get("text") or "" for e in enriched["elements"].values()]
    longest = max(map(len, texts), default=0)
    _, merged = merge_false_headings(stored_rows(enriched))
    reasons = {}
    if merged:
        reasons["FALSE_HEADINGS"] = sum(map(len, merged.values()))
    if not provisions:
        reasons["NO_PROVISIONS"] = longest
    if any(len(GLYPH_CODE.findall(text[:4000])) >= 20 for text in texts):
        reasons["GLYPH_CODES"] = True
    return reasons, provisions, longest


def find(args):
    manifest = read_manifest(args.manifest)
    affected, reasons_count, unreadable = {}, Counter(), []
    for number, (celex, version) in enumerate(manifest.items(), 1):
        try:
            reasons, _, _ = inspect(read_json(args.enriched_dir / f"{celex}_enriched.json"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            unreadable.append(celex)
            reasons = {"UNREADABLE_FILE": str(exc)[:200]}
        if reasons:
            affected[celex] = reasons
            reasons_count.update(reasons.keys())
        if number % 500 == 0 or number == len(manifest):
            print(f"{number:,}/{len(manifest):,} acts checked, {len(affected):,} need the repair.", flush=True)
    missing_smoke = [celex for celex in SMOKE_ACTS if celex not in manifest]
    if missing_smoke:
        raise ValueError(f"The manifest lacks the smoke-test acts {missing_smoke}.")
    # The screening runner needs its two smoke-test acts in every run.
    repair_manifest = {celex: manifest[celex] for celex in manifest if celex in affected or celex in SMOKE_ACTS}
    write_json(args.output_dir / "repair_manifest.json", repair_manifest)
    write_json(args.output_dir / "find_report.json", {
        "acts_checked": len(manifest), "acts_to_repair": len(affected), "reasons": dict(reasons_count),
        "unreadable_files": unreadable, "acts": affected,
    })
    print(f"\n{len(affected):,} of {len(manifest):,} acts need the repair: "
          + ", ".join(f"{name} {count:,}" for name, count in reasons_count.most_common()))
    print(f"Manifest: {args.output_dir / 'repair_manifest.json'} ({len(repair_manifest):,} acts, "
          "with the two smoke-test acts)")
    print("\nNext, enrich these acts again into a new folder:\n"
          f"  python enrich_eurlex_corpus.py --manifest {args.output_dir / 'repair_manifest.json'} "
          f"--output-dir {args.repaired_dir}")
    return 0


def check(args):
    manifest = read_manifest(args.output_dir / "repair_manifest.json")
    before = read_json(args.output_dir / "find_report.json")["acts"]
    screen, report = {}, {"repaired": [], "unchanged": [], "still_without_provisions": [],
                          "fetch_again_from_eurlex": [], "failed_or_missing": {}, "false_headings_left": {}}
    targets = Counter()
    for celex, version in manifest.items():
        try:
            enriched = read_json(args.repaired_dir / f"{celex}_enriched.json")
            metrics = verify_enriched(enriched, celex, version)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["failed_or_missing"][celex] = str(exc)[:200]
            continue
        screen[celex] = version
        reasons, provisions, longest = inspect(enriched)
        targets.update({field: metrics[field] for field in PROVISION_METRICS})
        if "FALSE_HEADINGS" in reasons:
            report["false_headings_left"][celex] = reasons["FALSE_HEADINGS"]
        if not provisions:
            report["still_without_provisions"].append(celex)
            if longest < SHORT_TEXT:
                report["fetch_again_from_eurlex"].append(celex)
        elif celex in before:
            repaired = enriched["document"].get("provision_repair") or "NO_PROVISIONS" in before[celex]
            report["repaired" if repaired else "unchanged"].append(celex)
    missing_smoke = [celex for celex in SMOKE_ACTS if celex not in screen]
    if missing_smoke:
        raise ValueError(f"The smoke-test acts {missing_smoke} were not enriched. Run the enrichment again.")
    write_json(args.output_dir / "screen_manifest.json", screen)
    # Write the enrichment report for exactly these acts; the screening runner compares against it.
    process(SimpleNamespace(output_dir=args.repaired_dir, verify_only=False, xhtml_dir=None, fetch_xhtml=False),
            screen, None)
    report = {"acts_in_repair_manifest": len(manifest), "acts_ready_for_screening": len(screen),
              "provision_targets": dict(targets),
              **{key: (len(value) if key in {"repaired", "unchanged"} else value) for key, value in report.items()},
              "repaired_acts": report["repaired"]}
    write_json(args.output_dir / "repair_report.json", report)
    print(f"\n{len(report['repaired_acts']):,} acts repaired, {report['unchanged']:,} unchanged, "
          f"{len(report['still_without_provisions']):,} still without articles "
          f"({len(report['fetch_again_from_eurlex']):,} of them hold under {SHORT_TEXT:,} characters: "
          f"fetch again from EUR-Lex), {len(report['failed_or_missing']):,} failed or missing.")
    print(f"{sum(targets.values()):,} provision targets to screen. Report: {args.output_dir / 'repair_report.json'}")
    print("\nNext, screen the repaired acts into a new run folder:\n"
          "  python run_provisions_overnight.py "
          f"--manifest {args.output_dir / 'screen_manifest.json'} --enriched-dir {args.repaired_dir} "
          "--no-document-hint --workers 4 --output-dir runs/eurlex_provisions_repair_v1")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("find", "check"))
    parser.add_argument("--manifest", type=Path, default=ROOT / "runs/eurlex_full_v10/provision_manifest.json",
                        help="The manifest of the full run (find).")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db_corpus",
                        help="The existing enriched files (find).")
    parser.add_argument("--repaired-dir", type=Path, default=ROOT / "output_db_corpus_repair",
                        help="The new folder for the repaired enriched files.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/eurlex_repair_v1",
                        help="Folder for the repair manifests and reports.")
    args = parser.parse_args()
    try:
        return find(args) if args.step == "find" else check(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
