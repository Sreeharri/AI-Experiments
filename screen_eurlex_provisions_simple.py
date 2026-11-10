"""Classify each complete provision in one model request."""

import argparse
from collections import Counter
from pathlib import Path

import yaml

from enrich_eurlex import write_json
from eurlex_enrichment.inputs import read_json
from eurlex_provision.evidence import Evidence, TARGET_KINDS
from eurlex_provision.simple import ModelError, build_pack, score
from lr_ai.qwen_client import QwenClient
from screen_eurlex_provisions import ROOT, check_scenario, load_exports, load_supplements, sha, write_relevance_export

SAMPLES = {"32024R3110": "Article 2", "32015L2193": "Article 6", "32013D0163": "BAT 17"}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("samples", "pilot"))
    parser.add_argument("--prepare-only", action="store_true", help="Save evidence without calling the model")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db")
    parser.add_argument("--manifest", type=Path, default=ROOT / "pilot_acts.json")
    parser.add_argument("--relevance-dir", type=Path,
                        default=ROOT / "runs/eurlex_full_v10/relevance_export")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--scenarios", type=Path, default=ROOT / "config/eurlex_provision_scenarios_simple.json")
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def load_document_results(folder, selected):
    matches = load_exports(folder, selected)
    ignored_file = folder / "ignore.json"
    if ignored_file.is_file():
        ignored = read_json(ignored_file)
        if not isinstance(ignored, dict):
            raise ValueError(f"{ignored_file} must be keyed by CELEX.")
        for celex in selected.keys() & ignored.keys():
            row = ignored[celex]
            if (celex in matches or not isinstance(row, dict) or row.get("level") != "IGNORE"
                    or not isinstance(row.get("reason"), str) or row.get("area") != []):
                raise ValueError(f"{ignored_file}: inconsistent or duplicate document result for {celex}.")
            matches[celex] = {"level": "IGNORE", "reason": row["reason"], "area": []}
    return matches


def configure(args):
    manifest = read_json(args.manifest)
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("The manifest must map CELEX IDs to pinned version IDs.")
    selected = {celex: manifest[celex] for celex in SAMPLES} if args.mode == "samples" else manifest
    matches = load_document_results(args.relevance_dir, selected)
    scenarios = read_json(args.scenarios)["scenarios"]
    anchors = yaml.safe_load(args.anchors.read_text(encoding="utf-8-sig"))["anchors"]
    if not isinstance(anchors, dict) or not anchors or not all(isinstance(k, str) for k in anchors):
        raise ValueError("The business anchor catalog must map anchor IDs to definitions.")
    supplements = load_supplements(args.supplements)
    packs = []
    for celex, version in selected.items():
        data = read_json(args.enriched_dir / f"{celex}_enriched.json")
        document = data["document"]
        if data["schema_version"] != 1 or (document["celex"], document["version_id"]) != (celex, version):
            raise ValueError(f"{celex}: enriched text does not match the pinned source version.")
        evidence = Evidence(data, supplements)
        scenario = scenarios.get(celex)
        if scenario is not None:
            check_scenario(celex, scenario)
        if args.mode == "samples":
            units = [evidence.find_unit(SAMPLES[celex])]
        else:
            units = [unit for unit in evidence.units.values() if unit["kind"] in TARGET_KINDS]
            summary = data["summary"]
            counts = {"ARTICLE": summary["articles"], "ANNEX": summary["annexes"],
                      "TABLE": summary["xhtml_data_tables"]}
            if Counter(unit["kind"] for unit in units) != Counter({k: v for k, v in counts.items() if v}):
                raise ValueError(f"{celex}: target inventory disagrees with enriched summary.")
        locators = set()
        for unit in units:
            pack = build_pack(evidence, unit, matches.get(celex), scenario, anchors)
            locator = pack["provision"]["locator"]
            if locator in locators:
                raise ValueError(f"{celex}: duplicate provision locator {locator}.")
            locators.add(locator)
            packs.append((celex, unit["id"], unit["kind"], pack))
    return packs


def run(args):
    packs = configure(args)
    prompt = (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8")
    out = args.output_dir or ROOT / "runs" / (
        f"eurlex_provisions_{args.mode}_simple" + ("_prepared" if args.prepare_only else "")
    )
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"Output directory is locked: {out}. Confirm no run is active before removing .running.") from exc
    try:
        run_packs(args, out, packs, prompt)
    finally:
        lock.unlink()


def run_packs(args, out, packs, prompt):
    client = None
    if not args.prepare_only:
        client = QwenClient.from_env(args.env_file, retries=0)
        discovered = client.discover()
        if not discovered["ok"]:
            raise ModelError(discovered["error"])

    audit = out / "audit"
    model = client.model if client else None
    scorer = ROOT / "eurlex_provision/simple.py"
    fingerprint = sha({"packs": packs, "model": model, "prepare_only": args.prepare_only,
                       "prompt": prompt, "code": scorer.read_text(encoding="utf-8"),
                       "runner": Path(__file__).read_text(encoding="utf-8")})
    run_file = audit / "run_config.json"
    if run_file.exists() and read_json(run_file)["fingerprint"] != fingerprint:
        raise ValueError("Run inputs changed. Choose a new --output-dir.")
    write_json(run_file, {"fingerprint": fingerprint, "model": model, "system_prompt": prompt})
    completed = []
    error = None
    try:
        for index, (celex, unit_id, kind, pack) in enumerate(packs, 1):
            target = pack["provision"]["locator"]
            path = audit / celex / f"{kind.lower()}_{sha((unit_id, target))[:18]}.json"
            print(f"[{index}/{len(packs)}] {celex} {target}", flush=True)
            item = {"celex": celex, "target": target, "kind": kind, "target_id": unit_id,
                    "file": path.relative_to(out).as_posix()}
            if path.exists():
                saved = read_json(path)
                if saved.get("fingerprint") != fingerprint:
                    raise ValueError(f"Checkpoint inputs changed for {target}. Choose a new --output-dir.")
            else:
                saved = {**item, "fingerprint": fingerprint, "evidence_pack": pack}
                if client:
                    decision, attempt = score(client, prompt, pack)
                    held = decision["level"] == "NEEDS_REVIEW"
                    saved.update(**decision, review_status="NEEDS_REVIEW" if held else "COMPLETE",
                                 model_attempt=attempt)
                write_json(path, saved)
            if client:
                item.update({k: saved[k] for k in ("level", "reason", "area", "review_status")})
            completed.append(item)
    except ModelError as exc:
        error = str(exc)
        raise
    finally:
        counts = Counter(row.get("level", "PREPARED") for row in completed)
        if client:
            write_relevance_export(out / "relevance_export", completed)
        write_json(audit / "run_summary.json", {
            "targets_planned": len(packs), "targets_saved": len(completed),
            "targets_exported": sum(counts[level] for level in ("DIRECT", "INDIRECT", "IGNORE")),
            "needs_review": counts["NEEDS_REVIEW"], "counts": dict(counts), "error": error,
            "results": completed,
        })
    print(f"Saved {len(completed)} targets to {out}", flush=True)


if __name__ == "__main__":
    try:
        run(arguments())
    except (OSError, ValueError, KeyError, TypeError, ModelError, yaml.YAMLError) as exc:
        raise SystemExit(f"Provision screening stopped: {exc}") from exc
