"""Screen the three EUR-Lex pilot acts using the existing Qwen configuration."""

from collections import Counter
from datetime import date
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import yaml

from enrich_eurlex import write_json
from eurlex_enrichment.inputs import read_json
from eurlex_provision.evidence import Evidence, TARGET_KINDS, branch_batches, instrument_key, norm, size
from eurlex_provision.screening import ModelError, combine, prepare, score_batch
from lr_ai.qwen_client import QwenClient


ROOT = Path(__file__).resolve().parent
SAMPLES = {
    "32024R3110": ("Article 2", None),
    "32015L2193": ("Article 6", "Article 6(2)"),
    "32013D0163": ("BAT 17", None),
}


def load_exports(folder, selected):
    matches = {}
    for level in ("DIRECT", "INDIRECT"):
        path = folder / f"{level.lower()}.json"
        if not path.is_file():
            raise ValueError(f"Document-level export missing: {path}")
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object keyed by CELEX.")
        for celex in selected.keys() & data.keys():
            record = data[celex]
            if (celex in matches or not isinstance(record, dict) or record.get("level") != level
                    or not isinstance(record.get("reason"), str) or not isinstance(record.get("area"), list)
                    or not all(isinstance(anchor, str) for anchor in record["area"])):
                raise ValueError(f"{path}: inconsistent or duplicate document result for {celex}.")
            matches[celex] = {"level": level, "reason": record["reason"], "area": record["area"]}
    return matches


def load_supplements(path):
    if path is None:
        return {}
    source = read_json(path)
    if not isinstance(source, list):
        raise ValueError("Supplementary evidence must be a JSON array.")
    items = {}
    fields = ("instrument", "locator", "source_version_id", "source_url", "text")
    for item in source:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(k), str) or not item[k].strip() for k in fields)):
            raise ValueError("Supplement entries need instrument, locator, source_version_id, source_url and text.")
        instrument, locator = item["instrument"], item["locator"]
        key = (instrument_key(instrument), norm(locator))
        if key in items:
            raise ValueError(f"Duplicate supplementary excerpt: {instrument} {locator}")
        items[key] = {k: item[k] for k in fields}
        items[key]["locator"] = f"{instrument} — {locator}"
    return items


def sha(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("samples", "pilot"))
    parser.add_argument("--prepare-only", action="store_true",
                        help="Write model inputs without connecting to Qwen")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db")
    parser.add_argument("--manifest", type=Path, default=ROOT / "pilot_acts.json")
    parser.add_argument("--relevance-dir", type=Path,
                        default=ROOT / "runs/eurlex_full_v10/relevance_export")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--scenarios", type=Path, default=ROOT / "config/eurlex_provision_scenarios.json")
    parser.add_argument("--assessment-date", type=date.fromisoformat)
    parser.add_argument("--supplements", type=Path, help="Reviewed external legal passages as JSON")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def configure(args):
    manifest = read_json(args.manifest)
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("The manifest must map CELEX IDs to pinned source version IDs.")
    selected = {celex: manifest[celex] for celex in SAMPLES} if args.mode == "samples" else manifest
    matches = load_exports(args.relevance_dir, selected)
    scenarios = read_json(args.scenarios)
    as_of = (args.assessment_date or date.fromisoformat(scenarios["assessment_date"])).isoformat()
    profile = yaml.safe_load(args.anchors.read_text(encoding="utf-8-sig"))
    anchors = profile["anchors"]
    if not isinstance(anchors, dict) or not anchors or not all(isinstance(key, str) for key in anchors):
        raise ValueError("The anchor catalog must have a nonempty mapping of anchor IDs.")
    supplements = load_supplements(args.supplements)
    sources, tasks = {}, []
    for celex, version in selected.items():
        path = args.enriched_dir / f"{celex}_enriched.json"
        data = read_json(path)
        document = data["document"]
        if data["schema_version"] != 1 or (document["celex"], document["version_id"]) != (celex, version):
            raise ValueError(f"{path}: expected enriched schema 1 and the manifest's exact source version.")
        evidence = Evidence(data, supplements)
        scenario = scenarios["scenarios"].get(celex)
        if (not isinstance(scenario, dict) or not isinstance(scenario.get("id"), str)
                or scenario.get("evidence_status") not in {"SYNTHETIC_TEST_CASE", "SUPPLIED_CASE"}
                or not isinstance(scenario.get("facts"), list) or not scenario["facts"]
                or not all(isinstance(fact, str) and fact.strip() for fact in scenario["facts"])):
            raise ValueError(f"{celex}: supply a scenario ID, evidence status and nonempty facts list.")
        context = {
            "assessment_date": as_of,
            "scenario": scenario,
            "candidate_anchors": anchors,
            "anchor_catalog_version": profile.get("version"),
            "anchor_roles": profile.get("roles", {}),
            "indirect_paths": profile.get("indirect_paths", {}),
            "business_boundaries": profile.get("not_roles", {}),
            "negative_anchors": profile.get("negative_anchors", {}),
            "document_level_relevance": matches.get(celex),
        }
        if args.mode == "samples":
            label, focus_label = SAMPLES[celex]
            units = [evidence.find_unit(label)]
            if focus_label:
                keys = evidence.aliases.get(norm(focus_label), set())
                if len(keys) != 1:
                    raise ValueError(f"{celex}: cannot resolve sample locator {focus_label}.")
                focus = next(iter(keys))
            else:
                focus = None
        else:
            units = [u for u in evidence.units.values() if u["kind"] in TARGET_KINDS]
            expected = {"ARTICLE": data["summary"]["articles"], "ANNEX": data["summary"]["annexes"],
                        "TABLE": data["summary"]["xhtml_data_tables"]}
            if Counter(u["kind"] for u in units) != Counter({k: v for k, v in expected.items() if v}):
                raise ValueError(f"{celex}: target inventory disagrees with enriched summary.")
            focus = None
        sources[celex] = (evidence, context, sha(data))
        for unit in units:
            target = evidence.locator(focus or unit["id"])
            batches = list(branch_batches(evidence.branches(unit, focus)))
            names = [b["locator"] for batch in batches for b in batch]
            if not names or len(names) != len(set(names)):
                raise ValueError(f"{celex} {target}: target branches are empty or ambiguous.")
            tasks.append((celex, unit, target, batches))
    return sources, tasks, matches, supplements


def execute(args):
    sources, tasks, matches, supplements = configure(args)
    prompt = (ROOT / "prompts/eurlex_provision_relevance.md").read_text(encoding="utf-8")
    out = args.output_dir or ROOT / "runs" / (
        f"eurlex_provisions_{args.mode}" + ("_prepared" if args.prepare_only else "")
    )
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"Output directory is locked: {out}. Remove .running only after confirming no run is active.") from exc
    try:
        run(args, sources, tasks, matches, supplements, prompt, out)
    finally:
        lock.unlink()


def run(args, sources, tasks, matches, supplements, prompt, out):
    client = None
    if not args.prepare_only:
        client = QwenClient.from_env(args.env_file, retries=1)
        discovered = client.discover()
        if not discovered["ok"]:
            raise ModelError(discovered["error"])
    model = client.model if client else None
    tool_code = [p.read_text(encoding="utf-8") for p in sorted(
        [Path(__file__), ROOT / "lr_ai/qwen_client.py", *(ROOT / "eurlex_provision").glob("*.py")]
    )]
    fingerprint = sha({
        "model": model, "mode": args.mode, "prepare_only": args.prepare_only,
        "inputs": {celex: (context, hash_) for celex, (_, context, hash_) in sources.items()},
        "supplements": list(supplements.values()), "prompt": prompt, "runner": tool_code,
    })
    run_file = out / "run_config.json"
    if run_file.exists() and read_json(run_file).get("fingerprint") != fingerprint:
        raise ValueError(f"Inputs or code changed for {out}. Set --output-dir to a fresh path.")
    if not run_file.exists():
        write_json(run_file, {"fingerprint": fingerprint, "model": model, "mode": args.mode,
                              "prepare_only": args.prepare_only, "target_count": len(tasks),
                              "assessment_date": next(iter(sources.values()))[1]["assessment_date"],
                              "anchor_count": len(next(iter(sources.values()))[1]["candidate_anchors"]),
                              "anchor_catalog_version": next(iter(sources.values()))[1]["anchor_catalog_version"],
                              "documents": {celex: {
                                  "source_version_id": evidence.document["version_id"],
                                  "scenario": context["scenario"],
                                  "document_level_relevance": matches.get(celex),
                              } for celex, (evidence, context, _) in sources.items()}})
    if missing := sorted(sources.keys() - matches.keys()):
        print("No DIRECT/INDIRECT document result for: " + ", ".join(missing)
              + ". Their document-level context will be null.", flush=True)

    completed = []
    pending_error = None
    try:
        for index, (celex, unit, target, batches) in enumerate(tasks, 1):
            evidence, common, _ = sources[celex]
            key = sha((unit["id"], target))[:18]
            path = out / celex / f"{unit['kind'].lower()}_{key}.json"
            print(f"[{index}/{len(tasks)}] {celex} {target} ({len(batches)} batch(es))", flush=True)
            if path.exists():
                item = read_json(path)
                if (item.get("run_fingerprint"), item.get("target_id"), item.get("target")) != (
                    fingerprint, unit["id"], target
                ):
                    raise ValueError(f"Invalid checkpoint: {path}")
            else:
                item = {
                    "celex": celex, "source_version_id": evidence.document["version_id"],
                    "target_id": unit["id"], "target": target, "target_kind": unit["kind"],
                    "assessment_date": common["assessment_date"], "scenario_id": common["scenario"]["id"],
                    "evidence_status": common["scenario"]["evidence_status"], "model": model,
                    "anchor_catalog_version": common["anchor_catalog_version"],
                    "document_level_relevance": common["document_level_relevance"],
                    "run_fingerprint": fingerprint,
                }
                if args.prepare_only:
                    payloads = [prepare(evidence, unit, target, batch, common) for batch in batches]
                    item.update(payloads=payloads, max_payload_chars=max(map(size, payloads)))
                else:
                    results, retrieval_log, evidence_index = [], [], {}
                    for batch_no, batch in enumerate(batches, 1):
                        payload = prepare(evidence, unit, target, batch, common)
                        decision, trace = score_batch(client, prompt, evidence, payload)
                        results.append(decision)
                        retrieval_log.extend({"batch": batch_no, **entry} for entry in trace)
                        cited = set(decision["evidence_refs"])
                        cited.update(r for b in decision["branch_results"] for r in b["evidence_refs"])
                        records = batch + payload["context"]["passages"] + payload["retrieved_context"] + payload["footnotes"]
                        for record in records:
                            children = record.get("rows", []) + record.get("header_and_spanning_rows", [])
                            for passage in [record, *children]:
                                if passage["locator"] in cited:
                                    evidence_index[passage["locator"]] = {
                                        k: v for k, v in passage.items() if k not in {
                                            "rows", "header_and_spanning_rows", "source_ids"}
                                    }
                    item.update(combine(results, payload["target_heading"]), batch_results=results,
                                retrieval_log=retrieval_log, evidence_index=evidence_index)
                write_json(path, item)
            completed.append({
                "celex": celex, "target_id": unit["id"], "target": target, "kind": unit["kind"],
                "file": path.relative_to(out).as_posix(),
                **({"max_payload_chars": item["max_payload_chars"]} if args.prepare_only else
                   {k: item[k] for k in ("level", "review_status", "area")}),
            })
    except ModelError as exc:
        pending_error = exc
        raise
    finally:
        counts = Counter(("PREPARED" if args.prepare_only else row["level"] or "PENDING")
                         for row in completed)
        write_json(out / "run_summary.json", {
            "run_fingerprint": fingerprint, "targets_planned": len(tasks),
            "targets_saved": len(completed), "targets_remaining": len(tasks) - len(completed),
            "counts": dict(counts), "needs_review": sum(row.get("review_status") == "NEEDS_REVIEW" for row in completed),
            "error": str(pending_error) if pending_error else None, "results": completed,
        })
    print(f"Saved {len(completed)} targets to {out}", flush=True)


if __name__ == "__main__":
    try:
        execute(arguments())
    except (OSError, ValueError, KeyError, TypeError, ModelError, yaml.YAMLError) as exc:
        print(f"Provision screening stopped: {exc}", file=sys.stderr)
        sys.exit(1)
