"""Run the two-set EUR-Lex relevance experiment from the project root."""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from urllib.parse import quote

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
import yaml
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai.eurlex_data import TextStore, load_acts, read_acts
from lr_ai.qwen_client import QwenClient
from lr_ai.relevance_model import LEVELS, decide, summarize

CATALOGUE = ROOT / "data/catalogue"
TITLES = CATALOGUE / "titles.json"
STRATA = {"DIRECT", "INDIRECT", "IRRELEVANT", "AMBIGUOUS", "RANDOM"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def export(out, env, *flags):
    cmd = [sys.executable, str(ROOT / "tools/export_eurlex.py"), "--kept-only", "--out", str(out)]
    if env:
        cmd.extend(["--env", env])
    subprocess.run([*cmd, *flags], check=True, cwd=ROOT)


def titles(args):
    export(CATALOGUE, args.db_env, "--no-text", "--no-notices")
    rows = [
        {"id": act["id"], "celex": act["celex"], "title_en": act["title"]["en"],
         "title_de": act["title"]["de"]}
        for act in read_acts(CATALOGUE)
    ]
    write(TITLES, rows)
    print(f"{len(rows)} titles saved to {TITLES}")


def split_groups(rows, acts):
    """Keep known linked acts together and seek a 200/200 split near each stratum's midpoint."""
    parents = {row["celex"]: row["celex"] for row in rows}

    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key

    def join(a, b):
        parents[root(a)] = root(b)

    explicit = {}
    for row in rows:
        key = row["celex"]
        if row["group"]:
            join(key, explicit.setdefault(row["group"], key))
        family = acts[key].get("family", {})
        for link in family.get("changes", []) + family.get("changed_by", []):
            other = link["celex"].upper()
            if other in parents:
                join(key, other)

    groups = {}
    for row in rows:
        groups.setdefault(root(row["celex"]), []).append(row)
    groups = list(groups.values())
    random.Random(42).shuffle(groups)
    totals = Counter(row["stratum"] for row in rows)
    states = {0: ([], Counter())}
    for index, group in enumerate(groups):
        counts = Counter(row["stratum"] for row in group)
        for size, (chosen, current) in list(states.items()):
            new_size = size + len(group)
            if new_size > 200:
                continue
            new_counts = current + counts

            def distance(value):
                return sum(abs(value[k] - totals[k] * new_size / 400) for k in totals)

            if new_size not in states or distance(new_counts) < distance(states[new_size][1]):
                states[new_size] = ([*chosen, index], new_counts)
    if 200 not in states:
        raise ValueError("Cannot split these groups exactly 200/200. Adjust the shortlist.")
    selected = set(states[200][0])
    return {
        name: sorted(
            (row for i, group in enumerate(groups) if (i in selected) == dev for row in group),
            key=lambda row: row["celex"],
        )
        for name, dev in (("dev", True), ("holdout", False))
    }


def prepare(args):
    target = args.run / "sample.json"
    if target.exists():
        raise ValueError("This run already has a sample. Use a new --run directory to change it.")
    acts = load_acts(CATALOGUE)
    with args.shortlist.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            {"celex": r["celex"].strip().upper(),
             "stratum": (r.get("stratum") or "RANDOM").strip().upper(),
             "group": (r.get("group") or "").strip()}
            for r in csv.DictReader(handle)
        ]
    ids = {r["celex"] for r in rows}
    if len(rows) != 400 or len(ids) != 400:
        raise ValueError("Supply exactly 400 distinct CELEX IDs.")
    if ids - acts.keys():
        raise ValueError(f"IDs outside the exported catalogue: {sorted(ids - acts.keys())[:10]}")
    if {r["stratum"] for r in rows} - STRATA:
        raise ValueError(f"Unknown stratum. Use {sorted(STRATA)}")
    sets = split_groups(rows, acts)
    data = args.run / "data"
    export(data, args.db_env, "--celex", ",".join(sorted(ids)))
    current = load_acts(data)
    if set(current) != ids or any(current[key]["id"] != acts[key]["id"] for key in ids):
        raise ValueError("Selected records changed since title export. Review the catalogue and retry.")
    write(target, sets)
    for name, items in sets.items():
        print(name, dict(Counter(row["stratum"] for row in items)))


def profile(language):
    path = ROOT / "config" / f"business_anchors_{language.lower()}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def fingerprint(folder):
    paths = [
        Path(__file__), ROOT / "lr_ai/relevance_model.py", ROOT / "lr_ai/qwen_client.py",
        ROOT / "lr_ai/eurlex_data.py", folder / "sample.json",
        *sorted((ROOT / "config").glob("business_anchors_*.yaml")),
        *(folder / "data" / name for name in ("acts.jsonl", "text_en.jsonl", "text_de.jsonl")),
    ]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def client_for(args):
    client = QwenClient.from_env(ROOT / ".env", retries=2)
    found = client.discover()
    if not found["ok"]:
        raise ValueError(found["error"])
    settings = {
        "model": client.model, "context": client.max_model_len,
        "endpoint_hash": hashlib.sha256(client.api_base.encode()).hexdigest(),
        "files": fingerprint(args.run),
    }
    path = args.run / "settings.json"
    if path.exists() and read(path) != settings:
        raise ValueError("Model, input or configuration changed. Use a fresh run for the new version.")
    write(path, settings)
    return client


def evidence(key, act, stores):
    available = [(lang, store.get(key) or {}) for lang, store in stores.items()]
    lang, source = max(
        available,
        key=lambda pair: (
            len([r for r in pair[1].get("recitals", []) if (r.get("text") or "").strip()]),
            bool(act["title"].get(pair[0].lower())),
            pair[0] == "DE",
        ),
    )
    recitals = [
        {"ref": f"R{i}", "number": r.get("n"), "text": r["text"]}
        for i, r in enumerate(source.get("recitals", []), 1)
        if (r.get("text") or "").strip()
    ]
    title = act["title"].get(lang.lower()) or act["title"].get("en") or act["title"].get("de")
    return {
        "celex": key, "title": title or key, "language": lang,
        "version": source.get("recitals_version") or (source.get("version") or {}).get("key"),
        "recitals": recitals, "articles": source.get("articles", []),
        "source_url": f"https://eur-lex.europa.eu/legal-content/{lang}/TXT/?uri=CELEX:{quote(key)}",
    }


def summarize_all(args):
    sample = read(args.run / "sample.json")
    acts = load_acts(args.run / "data")
    stores = {lang: TextStore(lang, args.run / "data") for lang in ("EN", "DE")}
    client = client_for(args)
    for index, row in enumerate(sample["dev"] + sample["holdout"], 1):
        key = row["celex"]
        path = args.run / "summaries" / f"{key}.json"
        if path.exists():
            continue
        item = evidence(key, acts[key], stores)
        print(f"[{index}/400] Summarizing {key}", flush=True)
        item["points"] = (
            summarize(client, item["title"], item["language"], item["recitals"])
            if item["recitals"] else []
        )
        write(path, item)


def summaries_digest(folder):
    sample = read(folder / "sample.json")
    digest = hashlib.sha256()
    for row in sample["dev"] + sample["holdout"]:
        path = folder / "summaries" / f"{row['celex']}.json"
        if not path.exists():
            raise ValueError(f"Run summarize first. Missing summary file for {row['celex']}.")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def classify(args):
    rows = read(args.run / "sample.json")[args.split]
    summaries_hash = summaries_digest(args.run)
    frozen = args.run / "freeze.json"
    if args.split == "holdout" and not frozen.exists():
        raise ValueError("Score dev and freeze this run before classifying holdout.")
    client = client_for(args)
    if args.split == "holdout":
        lock = read(frozen)
        if lock["settings"] != read(args.run / "settings.json") or lock["summaries"] != summaries_hash:
            raise ValueError("Frozen settings or summaries changed.")
    profiles = {lang: profile(lang) for lang in ("EN", "DE")}
    for index, row in enumerate(rows, 1):
        key = row["celex"]
        path = args.run / "predictions" / args.split / f"{key}.json"
        if path.exists():
            continue
        item = read(args.run / "summaries" / f"{key}.json")
        print(f"[{index}/200] Classifying {key}", flush=True)
        if item["points"]:
            value = decide(client, item["title"], item["points"], profiles[item["language"]])
        else:
            value = {
                "level": "NEEDS_REVIEW", "reason": "No usable recitals in the selected export.",
                "affected_party": None, "anchor_ids": [], "indirect_path": None, "references": [],
            }
        write(path, {"celex": key, **value})


def workbook(args):
    rows = read(args.run / "sample.json")[args.split]
    base = args.run / "blind" / args.split
    target = base / "labels.xlsx"
    if target.exists():
        raise ValueError(f"Workbook already exists: {target}. Keep your existing labels.")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Label"
    sheet.append(["CELEX", "Title", "Language", "Summary", "Official text", "Label", "Notes"])
    label_list = DataValidation(type="list", formula1='"' + ",".join(LEVELS) + '"')
    label_list.errorTitle = "Unknown label"
    label_list.error = "Choose a label from the list."
    label_list.showErrorMessage = True
    sheet.add_data_validation(label_list)
    label_list.add("F2:F201")
    for row in rows:
        key = row["celex"]
        item = read(args.run / "summaries" / f"{key}.json")
        summary = "\n".join(
            f"{', '.join(p['references'])}: {p['statement']}" for p in item["points"]
        ) or "No recitals available. Inspect the source and mark for review if evidence is insufficient."
        sheet.append([key, item["title"], item["language"], summary, item["source_url"]])
        for cell in sheet[sheet.max_row][:5]:
            cell.data_type = "s"
        sheet.cell(sheet.max_row, 5).hyperlink = item["source_url"]
        write(base / "evidence" / f"{key}.json", item)
    for column, width in zip("ABCDEFG", (20, 60, 12, 100, 22, 22, 40)):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in (row[1], row[3], row[6]):
            cell.alignment = openpyxl.styles.Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = "A1:G201"
    temp = target.with_name("labels.part.xlsx")
    book.save(temp)
    temp.replace(target)
    print(target)


def score(args):
    rows = read(args.run / "sample.json")[args.split]
    expected = {r["celex"] for r in rows}
    path = args.labels or args.run / "blind" / args.split / "labels.xlsx"
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    actual = {}
    try:
        for row in book["Label"].iter_rows(min_row=2, values_only=True):
            key, label = row[0], str(row[5] or "").strip().upper()
            if key not in expected or key in actual or label not in LEVELS:
                raise ValueError(f"Missing or invalid label, unexpected ID, or duplicate: {key}")
            actual[key] = label
    finally:
        book.close()
    if actual.keys() != expected:
        raise ValueError("Label all 200 expected acts before scoring.")
    matrix = {a: {b: 0 for b in LEVELS} for a in LEVELS}
    errors = []
    for key, truth in sorted(actual.items()):
        predicted = read(args.run / "predictions" / args.split / f"{key}.json")["level"]
        matrix[truth][predicted] += 1
        if truth != predicted:
            errors.append({"celex": key, "human": truth, "model": predicted})

    def ratio(a, b):
        return a / b if b else None

    actionable = LEVELS[:3]
    count = sum(sum(matrix[level].values()) for level in actionable)
    report = {
        "split": args.split, "labelled": len(actual), "human_decided": count,
        "accuracy_on_human_decided": ratio(sum(matrix[k][k] for k in actionable), count),
        "direct_recall": ratio(matrix["DIRECT"]["DIRECT"], sum(matrix["DIRECT"].values())),
        "direct_missed": sum(matrix["DIRECT"].values()) - matrix["DIRECT"]["DIRECT"],
        "indirect_called_direct": matrix["INDIRECT"]["DIRECT"],
        "model_review_rate": ratio(sum(row["NEEDS_REVIEW"] for row in matrix.values()), len(actual)),
        "confusion_matrix_human_rows": matrix, "disagreements": errors,
    }
    write(args.run / f"score_{args.split}.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "disagreements"}, indent=2))


def freeze(args):
    read(args.run / "score_dev.json")
    settings = read(args.run / "settings.json")
    if settings["files"] != fingerprint(args.run):
        raise ValueError("Inputs changed since the run started.")
    path = args.run / "freeze.json"
    if path.exists():
        raise ValueError("This run is already frozen.")
    write(path, {"settings": settings, "summaries": summaries_digest(args.run),
                 "acceptance_criteria": args.criteria})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "runs/round1")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("titles", titles), ("prepare", prepare), ("summarize", summarize_all),
                          ("classify", classify), ("workbook", workbook), ("score", score),
                          ("freeze", freeze)):
        cmd = sub.add_parser(name)
        cmd.set_defaults(handler=handler)
        if name in {"titles", "prepare"}:
            cmd.add_argument("--db-env", help="Backend .env containing DATABASE_URL")
        if name == "prepare":
            cmd.add_argument("shortlist", type=Path)
        if name in {"classify", "workbook", "score"}:
            cmd.add_argument("split", choices=["dev", "holdout"])
        if name == "score":
            cmd.add_argument("--labels", type=Path)
        if name == "freeze":
            cmd.add_argument("--criteria", required=True)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, KeyError, ValidationError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
