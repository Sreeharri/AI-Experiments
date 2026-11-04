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
    # Preserve the source comments containing assumptions. They carry material
    # information not available in yaml.safe_load()'s parsed mapping.
    comments = "\n".join(line[1:].strip() for line in raw.decode("utf-8-sig").splitlines()
                         if line.startswith("#"))
    parsed["unconfirmed_company_assumptions_and_profile_notes"] = comments
    return parsed, digest(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--domains", type=Path, default=ROOT / "config/domains.yaml")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, required=True, help="Dedicated run directory")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--context-limit", type=int, help="Confirmed context limit, if discovery omits it")
    parser.add_argument("--max-rounds", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--max-chunks", type=int, default=24, choices=range(12, 33))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--celex", help="Comma-separated CELEX IDs")
    parser.add_argument("--prepare-only", action="store_true", help="Inspect source selection without Qwen")
    args = parser.parse_args()
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
        acts, source_hash = read_input(args.input)
        business, anchor_hash = profile(args.anchors, args.domains)
        if args.celex:
            requested = set(args.celex.split(","))
            acts = [a for a in acts if a["celex"] in requested]
            missing = requested - {a["celex"] for a in acts}
            if missing:
                raise ValueError(f"Requested acts absent from input: {sorted(missing)}")
        if args.limit:
            acts = acts[:args.limit]
        if not acts:
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
            "as_of": args.as_of.isoformat(), "selected_celex": [a["celex"] for a in acts],
            "max_rounds": args.max_rounds, "max_chunks": args.max_chunks,
            "prepare_only": args.prepare_only, "context_limit": context,
            "model": client.model if client else None,
            "endpoint_sha256": digest(client.api_base.encode()) if client else None,
            "code": {p.name: digest(p.read_bytes()) for p in (
                Path(__file__), Path(engine.__file__), ROOT / "lr_ai/qwen_client.py")},
        }
        manifest = args.out / "manifest.json"
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if previous != signature:
                raise ValueError("Run settings changed. Use a new --out directory")
        else:
            save(manifest, signature)
        results = []
        for index, act in enumerate(acts, 1):
            destination = args.out / f"{act['celex']}.json"
            if "/" in act["celex"] or "\\" in act["celex"] or ".." in act["celex"]:
                raise ValueError("Invalid CELEX ID")
            if destination.exists():
                previous = json.loads(destination.read_text(encoding="utf-8"))
                if previous.get("status") != "failed":
                    results.append(previous)
                    print(f"[{index}/{len(acts)}] {act['celex']}: resumed", flush=True)
                    continue
            started = datetime.now(timezone.utc).isoformat()
            try:
                if args.prepare_only:
                    blocks, outline, problems = engine.prepare(act, args.as_of)
                    selected = engine.initial_selection(blocks, business)[:args.max_chunks]
                    result = {
                        "status": "prepared", "data_issues": problems, "outline": outline,
                        "evidence": [b for b in blocks if b["source_id"] in selected],
                        "coverage": engine.coverage(blocks, selected),
                        "source_chunks_total": len(blocks),
                    }
                else:
                    result = engine.run_act(client, act, business, args.as_of, context,
                                            args.max_rounds, args.max_chunks)
            except RuntimeError as exc:
                result = {"status": "failed", "error": client.clean(str(exc))}
            result.update(celex=act["celex"], version_id=act["version"]["id"],
                          started_at=started, finished_at=datetime.now(timezone.utc).isoformat())
            save(destination, result)
            results.append(result)
            print(f"[{index}/{len(acts)}] {act['celex']}: {result['status']}", flush=True)
        counts = {}
        for result in results:
            label = result.get("decision", {}).get("level", result["status"])
            counts[label] = counts.get(label, 0) + 1
        save(args.out / "summary.json", {"counts": counts, "acts": len(results)})
        print(json.dumps(counts))
        return int(any(r["status"] == "failed" for r in results))
    except (OSError, ValueError, RuntimeError) as exc:
        message = client.clean(str(exc)) if "client" in locals() and client else str(exc)
        parser.exit(1, f"Run stopped: {message}\n")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
