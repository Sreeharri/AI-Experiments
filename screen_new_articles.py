"""Relevance screening of the articles and annexes that amendments added to relevant acts later.

The provision screening read the text of each act that was stored at the time, for many acts
the original act. Articles and annexes inserted later by an amending act ("Article 15a is
inserted") were not in that text, so they have no relevance decision. This step screens them
with the same screening code, prompt and company profile as all other provisions (nothing of
the screening is changed), from the text in force today.

Input: the backend export (python -m app.cli.export_changed_texts), folder
reports/changed-texts/<time>/. An article or annex counts as added later when a change inserted
it as a whole, it exists in today's text, and no screening run holds a decision for it.
"Article 10BIS" and "Article 10a" are the same article.

What Qwen sees: the act's title, the document-level decision of the act (as in the other
runs), a note that the provision was inserted later and only it is shown, and its text in force
today. Surrounding articles and references are not part of this export.

Output (the same shape as the other screening runs, so the UNSURE pass and the classification
read it as they read the others):
    <output-dir>/batches/0001/audit/<CELEX>/*.json     one record per provision
    <output-dir>/batches/0001/audit/run_summary.json
    <output-dir>/batches/0001/relevance_export/results.json
    <output-dir>/new_articles.csv                      what was found and why it is screened

    python screen_new_articles.py --changed-dir <backend>/reports/changed-texts/<time> --list-only
    python screen_new_articles.py --changed-dir <backend>/reports/changed-texts/<time>

The same command continues after a stop; finished provisions are not asked again.
"""

import argparse
from argparse import Namespace
import csv
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from classify_eurlex_provisions import DEFAULT_RUNS, batch_folders, read_json  # noqa: E402
from eurlex_provision.simple import target_text  # noqa: E402
import screen_eurlex_provisions_simple as screening  # noqa: E402
import summarize_changed_provisions as changed  # noqa: E402

DEFAULT_OUTPUT = ROOT / "runs/eurlex_provisions_new_articles_v1"
DEFAULT_DOCUMENT_RUN = ROOT / "runs/eurlex_full_v10"
ADDS = {"ADD", "INSERT"}
# Latin suffixes of EUR-Lex: Article 10BIS is Article 10a.
SUFFIXES = {"bis": "a", "bi": "a", "ter": "b", "quater": "c", "quinquies": "d", "sexies": "e", "septies": "f",
            "octies": "g", "nonies": "h", "decies": "i"}
SUFFIX = re.compile(r"^(\d+)\s*(" + "|".join(sorted(SUFFIXES, key=len, reverse=True)) + r")$")


def normal(kind, number):
    """('article', '10bis') -> ('article', '10a'); Arabic annex numbers -> Roman."""
    number = (number or "").lower().replace(" ", "")
    if match := SUFFIX.match(number):
        number = match.group(1) + SUFFIXES[match.group(2)]
    if kind == "annex" and number.isdigit():
        number = changed.roman(int(number))
    return kind, number


def screened_roots(runs):
    """{celex: {(kind, number)}} of every article and annex that a screening run decided on (any level,
    also not assessable or failed: these were seen)."""
    seen = {}
    for run in runs:
        for folder in batch_folders(run):
            summary = read_json(folder / "audit" / "run_summary.json")
            for item in [*summary.get("results", []), *summary.get("not_assessable_targets", []),
                         *summary.get("failures", [])]:
                root = changed.root_of_locator(item.get("target", ""))
                if root:
                    seen.setdefault(item["celex"], set()).add(normal(*root))
    return seen


def new_articles(changed_dir, seen):
    """[(celex, record, key, root, inserted)] of the articles and annexes added later that no run screened,
    and [rows] for new_articles.csv."""
    found, rows = [], []
    index = read_json(changed_dir / "index.json")
    for celex in sorted(index):
        record = read_json(changed_dir / "acts" / f"{celex}.json")
        for key, root in sorted(record["roots"].items()):
            if root["kind"] not in ("article", "annex"):
                continue
            inserted = [line for line in root["changes"] if line["operation"] in ADDS and not line.get("depth")]
            if not inserted:
                continue
            row = {"celex": celex, "provision": root["name"], "inserted": inserted[-1].get("date") or "",
                   "by": inserted[-1]["changing_act"]}
            if celex not in seen:
                row["result"] = "act not screened at provision level: left out"
            elif normal(root["kind"], root["number"]) in seen[celex]:
                row["result"] = "already screened"
            elif root.get("deleted"):
                row["result"] = "deleted again"
            elif not root.get("today"):
                row["result"] = "not found in today's text"
            else:
                row["result"] = "screened now"
                found.append((celex, record, key, root, inserted[-1]))
            rows.append(row)
    return found, rows


def pack_of(celex, record, root, inserted):
    today = root["today"]
    provision = {"locator": root["name"], "text": today["text"]}
    if today.get("heading"):
        provision["heading"] = today["heading"]
    note = (f"This provision was inserted into the act later, by {inserted['changing_act']}"
            + (f" from {inserted['date']}" if inserted.get("date") else "")
            + ". Its text is taken from the consolidated version in force today. Other provisions of the act are "
              "not shown.")
    document = {"celex": celex, "version_id": record.get("today_version_celex") or celex,
                "title": record.get("title") or celex, "source_coverage": [note]}
    return {"document": document, "provision": provision, "direct_references": [], "essential_context": [],
            "footnotes": [], "unavailable_references": []}


def entries(found, decisions):
    result = []
    for celex, record, key, root, inserted in found:
        pack = pack_of(celex, record, root, inserted)
        kind = root["kind"].upper()
        skip, warnings = screening.assess(kind, root["name"], list(target_text(pack["provision"])),
                                          pack["provision"].get("heading"), None, single_text=True)
        result.append({"celex": celex, "unit_id": f"{celex}:{key}:today", "kind": kind, "pack": pack,
                       "not_assessable": skip, "input_warnings": warnings,
                       "document_screening": decisions.get(celex)})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed-dir", type=Path, required=True,
                        help="Folder written by the backend: reports/changed-texts/<time>")
    parser.add_argument("--runs", type=Path, nargs="+", default=DEFAULT_RUNS,
                        help="The screening runs whose decisions already exist")
    parser.add_argument("--document-run", type=Path, default=DEFAULT_DOCUMENT_RUN)
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-only", action="store_true", help="Write new_articles.csv and stop.")
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    try:
        seen = screened_roots([run.resolve() for run in args.runs])
        found, rows = new_articles(args.changed_dir, seen)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    with (out / "new_articles.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["celex", "provision", "inserted", "by", "result"], delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    print(f"Articles and annexes inserted later: {len(rows):,}. "
          + ", ".join(f"{key}: {value:,}" for key, value in sorted(counts.items())), flush=True)
    if args.list_only or not found:
        if not found:
            print("Nothing to screen.", flush=True)
        return 0
    try:
        decisions = screening.document_decisions(args.document_run if args.document_run.is_dir() else None)
        profile = screening.load_profile(args.anchors)
        system = screening.system_prompt(
            (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8"),
            (ROOT / "prompts/eurlex_provision_ignore_review.md").read_text(encoding="utf-8"),
            profile)
        packs = entries(found, decisions)
        options = Namespace(env_file=args.env_file, prepare_only=False, thinking="off", confirm_thinking="off",
                            workers=args.workers)
        retry = screening.run_packs(options, out / "batches" / "0001", packs, system, profile, [])
    except (OSError, ValueError, KeyError, TypeError, screening.ModelError) as exc:
        print(f"Screening stopped: {exc}. Run the same command again to continue.", file=sys.stderr)
        return 1
    return 1 if retry else 0


if __name__ == "__main__":
    raise SystemExit(main())
