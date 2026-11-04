"""Run document relevance on the complete-act JSON export or a ZIP containing it."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai import document_filter as engine
from lr_ai.qwen_client import QwenClient
from lr_ai.catalogue_input import Catalogue, validate_celex
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_input(path):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            entries = [f for f in archive.infolist()
                       if f.filename.lower().endswith(".json") and not f.is_dir()]
            if len(entries) != 1:
                raise ValueError("ZIP must contain exactly one complete-act JSON file")
            if entries[0].file_size > 500_000_000:
                raise ValueError("JSON exceeds the 500 MB input safety limit")
            raw = archive.read(entries[0])
    else:
        raw = path.read_bytes()
    acts = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(acts, list) or not acts:
        raise ValueError("Expected nonempty array from export_eurlex_structured_10.py")
    ids = []
    for act in acts:
        if not isinstance(act, dict) or not all(k in act for k in ("celex", "version", "document", "instrument", "elements")):
            raise ValueError("Input must be the complete-act export, not the recitals-only export")
        ids.append(act["celex"])
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate CELEX records")
    return acts, digest(raw)


def profile(path, domains_path):
    raw = path.read_bytes()
    parsed = yaml.safe_load(raw)
    domains = yaml.safe_load(domains_path.read_text(encoding="utf-8"))
    for field in ("roles", "anchors", "indirect_paths", "negative_anchors", "levels"):
        if not isinstance(parsed.get(field), dict):
            raise ValueError(f"Missing profile mapping: {field}")
    for key, anchor in parsed["anchors"].items():
        if anchor.get("domain") not in domains["domains"]:
            raise ValueError(f"Unknown domain for {key}")
        if set(anchor.get("roles", [])) - set(parsed["roles"]):
            raise ValueError(f"Unknown role for {key}")
    # Compact main-call profile keeps all scopes and roles. Preserve all special
    # notes and exclusions as a separate caveat map, provided alongside those IDs.
    compact = {
        "version": parsed.get("version"),
        "roles": parsed["roles"], "indirect_paths": parsed["indirect_paths"],
        "anchors": {key: {k: value[k] for k in ("label", "scope", "roles") if k in value}
                    for key, value in parsed["anchors"].items()},
        "negative_anchors": parsed["negative_anchors"],
        "anchor_caveats": {key: {k: value[k] for k in ("note", "not_covered") if k in value}
                           for key, value in parsed["anchors"].items()
                           if value.get("note") or value.get("not_covered")},
    }
    comments = raw.decode("utf-8-sig").split("# ASSUMPTIONS", 1)
    compact["unconfirmed_assumptions"] = comments[-1].split("version:", 1)[0] if len(comments) > 1 else ""
    # Full caveats are retained: an 8k-token target may not fit every profile.
    compact["fact_policy"] = "Explicit unconfirmed assumptions override general role descriptions; roles apply only within supplied scopes."
    return compact, digest(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--domains", type=Path, default=ROOT / "config/domains.yaml")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, required=True, help="Dedicated run directory")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--context-limit", type=int, help="Confirmed context limit, if discovery omits it")
    parser.add_argument("--max-rounds", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--max-chunks", type=int, default=24, choices=range(12, 33))
    parser.add_argument("--input-budget", type=int, default=34000,
                        help="Total input token cap including profile, outline and excerpts. "
                             "The profile alone is about 11,000 tokens.")
    parser.add_argument("--critique", action="store_true", help="Critique only on second-look rounds")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9),
                        help="Parallel requests (default 4, measure endpoint throughput before large runs)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--celex", help="Comma-separated CELEX IDs")
    parser.add_argument("--prepare-only", action="store_true", help="Inspect source selection without Qwen")
    args = parser.parse_args()
    if args.input_budget < 2048:
        parser.error("--input-budget must be at least 2048")
    if args.context_limit and args.input_budget + engine.RETRY_OUTPUT_TOKENS + 512 > args.context_limit:
        parser.error("--input-budget leaves no room for the answer inside --context-limit")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.context_limit is not None and args.context_limit < 4096:
        parser.error("--context-limit must be at least 4096")
    args.out.mkdir(parents=True, exist_ok=True)
    lock = args.out / ".writer.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.exit(1, "Run directory is locked. If no process is running, remove .writer.lock and retry.\n")
    os.close(handle)
    try:
        catalogue = None
        if args.input.is_dir() or args.input.name.lower() in {"text_en.jsonl", "text_de.jsonl", "acts.jsonl"}:
            catalogue = Catalogue(args.input if args.input.is_dir() else args.input.parent)
            ids = catalogue.ids()
            fingerprint = hashlib.sha256()
            for path in catalogue.paths:
                fingerprint.update(path.name.encode())
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        fingerprint.update(chunk)
            source_hash = fingerprint.hexdigest()
            get_act = catalogue.get
        else:
            acts, source_hash = read_input(args.input)
            by_id = {a["celex"]: a for a in acts}
            ids = list(by_id)
            get_act = by_id.__getitem__
        for celex in ids:
            validate_celex(celex)
        business, anchor_hash = profile(args.anchors, args.domains)
        if args.celex:
            requested = {validate_celex(c.strip()) for c in args.celex.split(",")}
            missing = requested - set(ids)
            if missing:
                raise ValueError(f"Requested acts absent from input: {sorted(missing)}")
            ids = [c for c in ids if c in requested]
        if args.limit:
            ids = ids[:args.limit]
        if not ids:
            raise ValueError("Selection is empty")
        client, context = None, None
        if not args.prepare_only:
            client = QwenClient.from_env(args.env, retries=2)
            discovery = client.discover()
            if not discovery["ok"]:
                raise RuntimeError("Qwen discovery failed; check office network and endpoint")
            advertised = client.max_model_len
            context = min(advertised, args.context_limit) if advertised and args.context_limit else advertised or args.context_limit
            if not context:
                raise ValueError("No advertised context size; provide confirmed --context-limit")
        signature = {
            "input_sha256": source_hash, "anchor_sha256": anchor_hash,
            "domain_sha256": digest(args.domains.read_bytes()),
            "as_of": args.as_of.isoformat(), "selected_celex": ids,
            "input_budget": args.input_budget, "critique": args.critique,
            "max_rounds": args.max_rounds, "max_chunks": args.max_chunks,
            "prepare_only": args.prepare_only, "context_limit": context,
            "model": client.model if client else None,
            "endpoint_sha256": digest(client.api_base.encode()) if client else None,
            "code": {p.name: digest(p.read_bytes()) for p in (
                Path(__file__), Path(engine.__file__), ROOT / "lr_ai/qwen_client.py", ROOT / "lr_ai/catalogue_input.py")},
        }
        manifest = args.out / "manifest.json"
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if previous != signature:
                raise ValueError("Run settings changed. Use a new --out directory")
        else:
            save(manifest, signature)
        printing = threading.Lock()
        total = len(ids)

        def process(index, celex):
            act = get_act(celex)
            destination = args.out / f"{act['celex']}.json"
            if "/" in act["celex"] or "\\" in act["celex"] or ".." in act["celex"]:
                raise ValueError("Invalid CELEX ID")
            if destination.exists():
                previous = json.loads(destination.read_text(encoding="utf-8"))
                if previous.get("status") != "failed":
                    with printing:
                        print(f"[{index}/{total}] {act['celex']}: resumed", flush=True)
                    return {"status": previous["status"], "decision": {"level": previous.get("decision", {}).get("level")}}
            clock_start = time.perf_counter()
            started = datetime.now(timezone.utc).isoformat()
            try:
                if args.prepare_only:
                    blocks, outline, problems = engine.prepare(act, args.as_of)
                    selected = engine.initial_selection(blocks, business)[:args.max_chunks]
                    result = {
                        "status": "prepared", "data_issues": problems,
                        "data_warnings": act.get("input_warnings", []), "outline": outline,
                        "evidence": [b for b in blocks if b["source_id"] in selected],
                        "coverage": engine.coverage(blocks, selected),
                        "source_chunks_total": len(blocks),
                    }
                else:
                    result = engine.run_act(client, act, business, args.as_of, context,
                                            args.max_rounds, args.max_chunks,
                                            args.input_budget, args.critique)
            except Exception as exc:
                result = {"status": "failed", "error": client.clean(str(exc)) if client else str(exc)}
            result.update(elapsed_seconds=round(time.perf_counter() - clock_start, 3), celex=act["celex"],
                          version_id=act["version"]["id"], started_at=started,
                          finished_at=datetime.now(timezone.utc).isoformat())
            save(destination, result)
            level = result.get("decision", {}).get("level")
            with printing:
                print(f"[{index}/{total}] {act['celex']}: {level or result['status']} "
                      f"({result['elapsed_seconds']:.1f} s)", flush=True)
            return {"status": result["status"], "decision": {"level": level}}

        wall_start = time.perf_counter()
        # Requests run in parallel; every act still has its own result file, so order and
        # resume behave as before. The endpoint check measured the best rate at 4.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(process, index, celex) for index, celex in enumerate(ids, 1)]
            results = [future.result() for future in futures]
        wall = time.perf_counter() - wall_start
        counts = {}
        for result in results:
            label = result.get("decision", {}).get("level") or result["status"]
            counts[label] = counts.get(label, 0) + 1
        save(args.out / "summary.json", {"counts": counts, "acts": len(results),
                                         "workers": args.workers, "wall_seconds": round(wall, 1)})
        print(json.dumps(counts))
        print(f"{len(results)} acts in {wall / 60:.1f} min with {args.workers} parallel requests")
        return int(any(r["status"] == "failed" for r in results))
    except (OSError, ValueError, RuntimeError) as exc:
        message = client.clean(str(exc)) if "client" in locals() and client else str(exc)
        parser.exit(1, f"Run stopped: {message}\n")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
