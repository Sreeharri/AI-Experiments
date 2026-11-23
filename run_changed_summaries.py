"""One command for the AI part of the changes, after the backend export
(python -m app.cli.export_changed_texts, or step 3 of app.cli.run_changes_day).

Runs the existing steps one after the other; each one continues where it stopped, so the same
command can be started again after Ctrl+C or a lost connection:

1. screen_new_articles.py          relevance screening of the articles and annexes inserted later
                                   that no run has screened yet
2. resolve_unsure_provisions.py    the UNSURE ones of step 1 become INDIRECT or IGNORE
3. classify_eurlex_provisions.py   domain and departments of the relevant ones of steps 1-2
4. summarize_changed_provisions.py summary version 2 of every changed relevant article or annex,
                                   including the relevant articles inserted later

Folders: runs/eurlex_new_articles/<export time>/{screening,unsure,classification} and
runs/eurlex_summaries_v2_changes (as summarize_changed_provisions.py writes it).

    python run_changed_summaries.py --changed-dir <backend>/reports/changed-texts/<time> --sample 20
    python run_changed_summaries.py --changed-dir <backend>/reports/changed-texts/<time>

--sample only limits step 4 (the screening of new articles is small and always runs whole).
--only-relevance runs steps 1-3 and stops before the summaries.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
NEW = ROOT / "runs/eurlex_new_articles"
SUMMARIES = ROOT / "runs/eurlex_summaries_v2_changes"
SCREEN_TRIES = 4
WAIT_SECONDS = 120


def call(name, *arguments):
    command = [sys.executable, str(ROOT / name), *map(str, arguments)]
    print(f"\n=== {name} ===\n" + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sample", type=int, help="Step 4 only: summarise only this many articles or annexes.")
    parser.add_argument("--only-latest", action="store_true",
                        help="Step 4 only: articles whose latest change is from 2015 on.")
    parser.add_argument("--only-relevance", action="store_true",
                        help="Steps 1-3 only (screening, UNSURE, classification of the inserted articles).")
    args = parser.parse_args(argv)
    changed_dir = args.changed_dir.resolve()
    if not (changed_dir / "index.json").is_file():
        print(f"Stopped: {changed_dir} holds no index.json. Give the folder of export_changed_texts.",
              file=sys.stderr)
        return 1
    folder = NEW / changed_dir.name
    screening, unsure, classification = folder / "screening", folder / "unsure", folder / "classification"
    workers = ["--workers", args.workers]

    # 1. Screening of the articles inserted later. It stops when Qwen fails; tried again a few times.
    for attempt in range(1, SCREEN_TRIES + 1):
        code = call("screen_new_articles.py", "--changed-dir", changed_dir, "--output-dir", screening, *workers)
        if code == 0:
            break
        if attempt < SCREEN_TRIES:
            print(f"Screening not finished (try {attempt} of {SCREEN_TRIES}). Next try in {WAIT_SECONDS // 60} "
                  "minutes.", flush=True)
            time.sleep(WAIT_SECONDS)
    else:
        print("The screening of the inserted articles is not finished. Start this command again later; the "
              "steps after it continue with what is there.", flush=True)

    # 2-3. Only when step 1 screened something.
    extra = []
    if (screening / "batches").is_dir():
        call("resolve_unsure_provisions.py", "--runs", screening, "--output-dir", unsure, *workers)
        code = call("classify_eurlex_provisions.py", "--runs", screening, "--unsure-dir", unsure,
                    "--output-dir", classification, *workers)
        if (classification / "results.json").is_file():
            extra = ["--extra-classification-dir", classification]
        elif code:
            print("No relevant inserted article was classified (none relevant, or the step stopped).", flush=True)

    # 4. Summary version 2.
    code = 0
    if not args.only_relevance:
        sample = (["--sample", args.sample] if args.sample else []) + (["--only-latest"] if args.only_latest else [])
        code = call("summarize_changed_provisions.py", "--changed-dir", changed_dir, *extra, *workers, *sample)

    lines = ["", "=== Done ==="]
    csv_file = screening / "new_articles.csv"
    if csv_file.is_file():
        lines.append(f"Inserted articles found: {sum(1 for _ in csv_file.open(encoding='utf-8-sig')) - 1} "
                     f"(see {csv_file})")
    run_summary = screening / "batches" / "0001" / "audit" / "run_summary.json"
    if run_summary.is_file():
        lines.append(f"Screened now: {json.loads(run_summary.read_text(encoding='utf-8')).get('counts', {})}")
    if (classification / "results.json").is_file():
        relevant = json.loads((classification / "results.json").read_text(encoding="utf-8"))
        lines.append(f"Relevant inserted articles classified: {sum(len(v) for v in relevant.values())} "
                     f"(see {classification / 'review.csv'})")
    if args.only_relevance:
        lines.append("Summaries not written (--only-relevance). Later: the same command without --only-relevance.")
    elif (SUMMARIES / "summary.json").is_file():
        lines.append(f"Summary version 2: {json.loads((SUMMARIES / 'summary.json').read_text(encoding='utf-8'))}")
    if not args.only_relevance:
        lines += [f"Open in a browser: {SUMMARIES / 'preview' / 'index.html'}",
                  f"To check by hand: {SUMMARIES / 'review.csv'}"]
    print("\n".join(lines), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
