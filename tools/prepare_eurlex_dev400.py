"""Prepare two new, disjoint 200-act inputs without calling or changing Qwen.

Structural checks concern dataset completeness only. They assign no relevance
label and do not change the AI runner's output or retry rules.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai import document_filter as engine

HERE = Path(__file__).resolve().parent
CELEX = re.compile(r"3\d{4}[RLD]\d{4}(?:\(\d{2}\))?\Z")
DEFAULT_SEED = "eurlex-dev400-2026-11-04-v1"


def stable_hash(seed, value):
    return hashlib.sha256(f"{seed}\0{value}".encode("utf-8")).hexdigest()


def read_exclusions(paths):
    ids = set()
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            if path.suffix.lower() == ".csv":
                reader = csv.DictReader(stream)
                if "celex" not in (reader.fieldnames or []):
                    raise ValueError(f"Exclusion CSV needs a celex column: {path.name}")
                values = [row["celex"].strip() for row in reader]
            else:
                values = [line.strip() for line in stream
                          if line.strip() and not line.lstrip().startswith("#")]
        for value in values:
            if not CELEX.fullmatch(value):
                raise ValueError(f"Invalid CELEX in {path.name}: {value!r}")
            ids.add(value)
    return ids


def canonical_path(path):
    return re.sub(r"~duplicate[^/]*", "", path)


def audit_act(act, as_of, min_annex_chunks=2):
    """Use the unchanged v6.3 parser and selection; require a populated pack."""
    problems = []
    version = act.get("version") or {}
    version_id = str(version.get("id") or "")
    if not version_id:
        problems.append("Missing stored version ID")
    if version.get("language") not in ("EN", "DE"):
        problems.append("No English or German stored version")
    for field in ("version_date",):
        value = version.get(field)
        if value and date.fromisoformat(str(value)[:10]) > as_of:
            problems.append("Version date is after the assessment date")
    effective = version.get("effective_from")
    if (version.get("version_kind") == "CONSOLIDATED" and effective
            and date.fromisoformat(str(effective)[:10]) > as_of):
        problems.append("Future consolidated text")
    document = act.get("document") or {}
    title = (document.get("title_en") or document.get("title_de")
             or (act.get("instrument") or {}).get("title") or "").strip()
    if not title:
        problems.append("Missing title")
    elements = act.get("elements") or []
    if any(str(e.get("version_id")) != version_id for e in elements):
        problems.append("Legal elements belong to another or an unspecified version")
    recital_set = act.get("recital_set")
    if recital_set and str(recital_set.get("version_id")) != version_id:
        problems.append("Recital set belongs to another or an unspecified version")
    chapters = [e for e in elements if e.get("element_type") == "CHAPTER"
                and str(e.get("heading") or e.get("body_text") or "").strip()]
    if not chapters:
        problems.append("No populated chapter heading")
    # prepare() may add data warnings; keep the caller's raw export unchanged.
    scratch = dict(act, input_warnings=list(act.get("input_warnings") or []))
    blocks, outline, parser_problems = engine.prepare(scratch, as_of)
    problems.extend(parser_problems)
    articles = {canonical_path(b["path"]) for b in blocks if b["type"] == "ARTICLE"}
    recitals = {canonical_path(b["path"]) for b in blocks if b["type"] == "RECITAL"}
    annex_chunks = [b for b in blocks if b["type"] == "ANNEX"]
    if len(articles) < 9:
        problems.append("Fewer than 9 distinct articles with usable text")
    if len(recitals) < 3:
        problems.append("Fewer than 3 distinct recitals with usable text")
    if len(annex_chunks) < min_annex_chunks:
        problems.append(f"Fewer than {min_annex_chunks} usable annex chunks")
    return {
        "title": title, "chapters": len(chapters), "articles": len(articles),
        "recitals": len(recitals), "annex_chunks": len(annex_chunks),
        "source_chunks": len(blocks), "outline_entries": len(outline),
        "warnings": scratch["input_warnings"], "problems": list(dict.fromkeys(problems)),
    }, blocks


def verify_selected_pack(blocks, business, min_annex_chunks=2):
    selected_ids = engine.initial_selection(blocks, business)
    chosen = [b for b in blocks if b["source_id"] in selected_ids]
    counts = Counter(b["type"] for b in chosen)
    problems = []
    for kind, minimum in (("ARTICLE", 9), ("RECITAL", 3), ("ANNEX", min_annex_chunks)):
        if counts[kind] < minimum:
            problems.append(f"Current evidence selector returned fewer than {minimum} {kind} chunks")
    if len({canonical_path(b["path"]) for b in chosen if b["type"] == "ARTICLE"}) < 9:
        problems.append("Current selected articles include too few distinct canonical paths")
    if len({b["source_id"] for b in chosen}) != len(chosen):
        problems.append("Selected source IDs are not unique")
    return selected_ids, problems


def split_records(records, per_set, seed):
    """Match rough act-type/year composition without guessing relevance labels."""
    if len(records) != 2 * per_set or len({r["celex"] for r in records}) != len(records):
        raise ValueError("Need exactly two sets of unique acts")
    strata = defaultdict(list)
    for record in records:
        celex = record["celex"]
        year_band = str(int(celex[1:5]) // 10 * 10)
        strata[(celex[5], year_band)].append(record)
    first, second = [], []
    for key in sorted(strata, key=lambda k: stable_hash(seed, repr(k))):
        group = sorted(strata[key], key=lambda r: stable_hash(seed + ":split", r["celex"]))
        for record in group:
            # Alternate within each stratum and never exceed the requested size.
            destination = first if len(first) <= len(second) else second
            if len(first) == per_set:
                destination = second
            elif len(second) == per_set:
                destination = first
            destination.append(record)
    assert len(first) == len(second) == per_set
    return (sorted(first, key=lambda r: r["celex"]), sorted(second, key=lambda r: r["celex"]))


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_set(stage, name, records):
    # Only one act's bytes are held at a time. Full legal text is not truncated.
    output = stage / f"{name}.json"
    with output.open("wb") as target:
        target.write(b"[\n")
        for index, record in enumerate(records):
            if index:
                target.write(b",\n")
            with record["path"].open("rb") as source:
                shutil.copyfileobj(source, target)
        target.write(b"\n]\n")
    write_csv(stage / f"{name}_labels.csv",
              ["celex", "label", "anchor_ids", "notes", "title", "version_id", "language"], records)
    (stage / f"{name}_celex.txt").write_text(
        "\n".join(r["celex"] for r in records) + "\n", encoding="utf-8")


class DatabaseSource:
    def __init__(self, connection):
        self.connection = connection
        self.candidates_sql = (HERE / "eurlex_dev_candidates.sql").read_text(encoding="utf-8")
        self.act_sql = (HERE / "eurlex_dev_act.sql").read_text(encoding="utf-8")

    def candidates(self, excluded, as_of):
        with self.connection.cursor() as cursor:
            cursor.execute(self.candidates_sql, {"excluded": sorted(excluded), "as_of": as_of})
            fields = [column.name for column in cursor.description]
            return [dict(zip(fields, row)) for row in cursor.fetchall()]

    def act(self, candidate):
        with self.connection.cursor() as cursor:
            cursor.execute(self.act_sql, {"version_id": candidate["version_id"]})
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Version disappeared from snapshot: {candidate['celex']}")
            return row[0]


def prepare_sets(source, out, excluded, as_of, business, seed=DEFAULT_SEED, per_set=200,
                 min_annex_chunks=2):
    out = out.resolve()
    if out.exists():
        raise ValueError("Output already exists; choose a fresh directory to keep the sets frozen")
    if per_set < 1:
        raise ValueError("per_set must be positive")
    if min_annex_chunks not in (1, 2):
        raise ValueError("min_annex_chunks must be 1 or 2")
    out.parent.mkdir(parents=True, exist_ok=True)
    print("Searching stored EN/DE versions with chapter/article/recital/annex structure...", flush=True)
    candidates = source.candidates(excluded, as_of)
    by_celex = defaultdict(list)
    for row in candidates:
        celex = row["celex"]
        if CELEX.fullmatch(celex) and celex not in excluded:
            by_celex[celex].append(row)
    needed = 2 * per_set
    if len(by_celex) < needed:
        raise ValueError(f"Only {len(by_celex)} shortlisted acts; need {needed}. "
                         "No output written. Evidence requirements were not relaxed.")
    accepted, rejected = [], []
    with TemporaryDirectory(prefix="eurlex-dev-", dir=out.parent) as temporary:
        working = Path(temporary)
        spool, stage = working / "acts", working / "output"
        spool.mkdir()
        stage.mkdir()
        order = sorted(by_celex, key=lambda celex: stable_hash(seed + ":sample", celex))
        examined_acts = 0
        for celex in order:
            examined_acts += 1
            # SQL orders each CELEX's versions: EN then DE; preferred/current/latest.
            for candidate in by_celex[celex]:
                act = source.act(candidate)
                if act.get("celex") != celex or str(act.get("version", {}).get("id")) != str(candidate["version_id"]):
                    raise ValueError(f"Export identity mismatch for {celex}")
                audit, blocks = audit_act(act, as_of, min_annex_chunks)
                selected, selection_problems = verify_selected_pack(blocks, business, min_annex_chunks)
                audit["problems"].extend(selection_problems)
                if audit["problems"]:
                    rejected.append({"celex": celex, "version_id": candidate["version_id"],
                                     "problems": " | ".join(audit["problems"]),
                                     "annex_chunks": audit["annex_chunks"],
                                     "articles": audit["articles"], "recitals": audit["recitals"]})
                    continue
                path = spool / f"{celex}.json"
                path.write_text(json.dumps(act, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                accepted.append({"celex": celex, "path": path,
                                 "version_id": str(act["version"]["id"]),
                                 "version_key": act["version"].get("version_key"),
                                 "version_kind": act["version"].get("version_kind"),
                                 "version_date": act["version"].get("version_date"),
                                 "language": act["version"]["language"],
                                 "audit": audit, "title": audit["title"],
                                 "selected_source_ids": selected,
                                 "act_sha256": hash_file(path)})
                break
            if examined_acts % 10 == 0 or len(accepted) == needed:
                print(f"Examined {examined_acts} acts; accepted {len(accepted)}/{needed}; "
                      f"rejected versions {len(rejected)}", flush=True)
            if len(accepted) == needed:
                break
        if len(accepted) < needed:
            # Preserve all usable source text; never pretend this is a full pair.
            write_set(stage, "eligible_acts", accepted)
            cache = out.with_name(out.name + "_eligible_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            (stage / "eligible_manifest.json").write_text(json.dumps({
                "status": "insufficient", "required": needed, "eligible": len(accepted),
                "as_of": as_of.isoformat(), "seed": seed, "min_annex_chunks": min_annex_chunks,
                "excluded_celex": sorted(excluded),
                "acts": [{k: v for k, v in r.items() if k != "path"} for r in accepted],
                "input_sha256": hash_file(stage / "eligible_acts.json")
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            stage.rename(cache)
            report = out.with_name(out.name + "_insufficient.json")
            report.write_text(json.dumps({"required": needed, "eligible": len(accepted),
                                          "min_annex_chunks": min_annex_chunks,
                                          "eligible_cache": str(cache),
                                          "rejected_versions": rejected}, indent=2), encoding="utf-8")
            raise ValueError(f"Only {len(accepted)} acts passed the evidence requirements; need {needed}. "
                             f"No full pair written. Usable acts saved in {cache}. Diagnostics: {report.name}")
        sets = split_records(accepted, per_set, seed)
        all_records = []
        for name, records in zip(("dev_A_200", "dev_B_200"), sets):
            # Names retain the requested 200; override is for small verification runs.
            name = name.replace("200", str(per_set))
            write_set(stage, name, records)
            for record in records:
                all_records.append({k: v for k, v in dict(record, set=name).items() if k != "path"})
        (stage / "selected_acts.json").write_text(
            json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv(stage / "rejected_versions.csv", ["celex", "version_id", "problems"], rejected)
        files = {path.name: {"sha256": hash_file(path), "bytes": path.stat().st_size}
                 for path in sorted(stage.iterdir())}
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(), "as_of": as_of.isoformat(),
            "seed": seed, "acts_per_set": per_set, "unique_acts": len(accepted),
            "shortlisted_unique_acts": len(by_celex), "examined_unique_acts": examined_acts,
            "rejected_versions": len(rejected), "excluded_celex": sorted(excluded),
            "input_rules": {"title": True, "populated_chapters_min": 1,
                            "distinct_articles_min": 9, "distinct_recitals_min": 3,
                            "annex_chunks_min": min_annex_chunks, "same_stored_version": True},
            "selection": "Seeded CELEX sample from structurally eligible stored versions; "
                         "EN preferred, DE fallback; split balanced by act type and decade",
            "labels": "Blank; independent human labels required. No AI calls were made.",
            "limits": "Structural presence does not certify parsing accuracy or current legal effect. "
                      "The sets describe the structurally complete subset, not the full population. "
                      "Related amending/base acts are not grouped; B is a second dev set, not a leakage-proof holdout.",
            "language_counts": dict(Counter(r["language"] for r in accepted)),
            "ai_baseline_sha256": {"lr_ai/document_filter.py": hash_file(ROOT / "lr_ai/document_filter.py"),
                                   "tools/document_relevance.py": hash_file(HERE / "document_relevance.py"),
                                   "prompts/document_relevance.txt": hash_file(ROOT / "prompts/document_relevance.txt"),
                                   "prompts/second_pass.txt": hash_file(ROOT / "prompts/second_pass.txt"),
                                   "config/business_anchors_en.yaml": hash_file(ROOT / "config/business_anchors_en.yaml"),
                                   "config/domains.yaml": hash_file(ROOT / "config/domains.yaml")},
            "preparation_code_sha256": {p.name: hash_file(p) for p in (
                Path(__file__), HERE / "eurlex_dev_candidates.sql", HERE / "eurlex_dev_act.sql")},
            "files": files,
        }
        (stage / "selection_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Never publish half a pair. Temporary files are removed on any failure.
        stage.rename(out)
    print(f"Prepared {per_set} + {per_set} new acts in {out}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ROOT / ".env", help="File containing DATABASE_URL")
    parser.add_argument("--out", type=Path, default=ROOT / "data/dev400_new")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--per-set", type=int, default=200)
    parser.add_argument("--min-annex-chunks", type=int, choices=(1, 2), default=2,
                        help="Default 2; use 1 to allow an act with one usable short annex chunk")
    parser.add_argument("--exclude-file", type=Path, action="append", default=[],
                        help="Additional CSV with celex column or text file with one CELEX per line")
    args = parser.parse_args()
    if args.per_set < 1:
        parser.error("--per-set must be positive")
    # Missing dependency or SQL is a clear setup error, before connecting.
    for path in (HERE / "eurlex_dev_candidates.sql", HERE / "eurlex_dev_act.sql"):
        if not path.is_file():
            parser.error(f"Missing SQL file: {path}. Extract all package files into the project root.")
    try:
        from export_eurlex import database_url, connect, psycopg
    except ImportError as exc:
        parser.exit(1, f"Missing project dependency: {exc}. Install requirements-dev400.txt.\n")
    try:
        if args.out.exists():
            raise ValueError("Output already exists; use a fresh --out directory")
        excluded = read_exclusions([ROOT / "config/excluded_previous_celex.txt", *args.exclude_file])
        from document_relevance import profile
        business, _ = profile(ROOT / "config/business_anchors_en.yaml", ROOT / "config/domains.yaml")
        url, _ = database_url(args.env)
        with connect(url) as connection:
            prepare_sets(DatabaseSource(connection), args.out, excluded, args.as_of,
                         business, args.seed, args.per_set, args.min_annex_chunks)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f"Development input preparation failed: {exc}\n")
    except psycopg.Error as exc:
        parser.exit(1, f"Read-only database export failed ({type(exc).__name__}). "
                       "Check the local PostgreSQL service and database schema.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
