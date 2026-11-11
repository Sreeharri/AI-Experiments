"""Classify each complete provision in one model request."""

import argparse
from collections import Counter
from pathlib import Path

import yaml

from enrich_eurlex import write_json
from eurlex_enrichment.inputs import read_json
from eurlex_provision.evidence import Evidence, TARGET_KINDS
from eurlex_provision.screening import LEVELS, ModelError
from eurlex_provision.simple import DECISION_FIELDS, TargetScreeningError, build_pack, score
from lr_ai.qwen_client import QwenClient
from screen_eurlex_provisions import ROOT, load_supplements, sha

SAMPLES = {"32024R3110": "Article 2", "32015L2193": "Article 6", "32013D0163": "BAT 17"}
SUPPORTING_LOCATORS = {
    ("32015L2193", "Article 6"): ("Article 2", "Article 3"),
    ("32013D0163", "BAT 17"): ("1.5.1 Dust emissions",),
}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("samples", "holdout50", "pilot"))
    parser.add_argument("--celex", nargs="+", help="Limit pilot mode to these manifest IDs")
    parser.add_argument("--target", nargs="+", help="Exact target locators for one selected act")
    parser.add_argument("--prepare-only", action="store_true", help="Save evidence without calling the model")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db")
    parser.add_argument("--manifest", type=Path, default=ROOT / "pilot_acts.json")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def configure(args):
    manifest = read_json(args.manifest)
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("The manifest must map CELEX IDs to pinned version IDs.")
    selected = manifest if args.mode == "pilot" else {celex: manifest[celex] for celex in SAMPLES}
    requested = getattr(args, "celex", None)
    targets = getattr(args, "target", None)
    if requested:
        if args.mode != "pilot" or len(set(requested)) != len(requested) or not set(requested) <= manifest.keys():
            raise ValueError("--celex requires pilot mode and unique IDs from the manifest.")
        selected = {celex: manifest[celex] for celex in requested}
    if targets and (not requested or len(requested) != 1 or len(set(targets)) != len(targets)):
        raise ValueError("--target requires one --celex and unique target locators.")
    holdout = None
    if args.mode == "holdout50":
        holdout = read_json(ROOT / "config/eurlex_provision_holdout50.json")
        if set(holdout) != set(selected) or sum(map(len, holdout.values())) != 50:
            raise ValueError("The holdout must specify 50 targets from the three pilot acts.")
    anchors = yaml.safe_load(args.anchors.read_text(encoding="utf-8-sig"))["anchors"]
    if not isinstance(anchors, dict) or not anchors or not all(isinstance(k, str) for k in anchors):
        raise ValueError("The business anchor catalog must map anchor IDs to definitions.")
    if "industrial_emissions" in anchors:
        facts = read_json(ROOT / "config/eurlex_provision_anchor_facts.json")
        anchor = anchors["industrial_emissions"]
        anchors["industrial_emissions"] = {**anchor, "company_facts": facts["industrial_emissions"]}
    supplements = load_supplements(args.supplements)
    packs = []
    for celex, version in selected.items():
        data = read_json(args.enriched_dir / f"{celex}_enriched.json")
        document = data["document"]
        if data["schema_version"] != 1 or (document["celex"], document["version_id"]) != (celex, version):
            raise ValueError(f"{celex}: enriched text does not match the pinned source version.")
        evidence = Evidence(data, supplements)
        if args.mode == "samples":
            units = [evidence.find_unit(SAMPLES[celex])]
        elif holdout is not None:
            by_locator = {evidence.locator(unit["id"]): unit for unit in evidence.units.values()
                          if unit["kind"] in TARGET_KINDS | {"BAT"}}
            units = [by_locator[locator] for locator in holdout[celex]]
            sample_id = evidence.find_unit(SAMPLES[celex])["id"]
            if any(unit["id"] == sample_id for unit in units):
                raise ValueError(f"{celex}: the holdout includes an original sample.")
        else:
            units = [unit for unit in evidence.units.values() if unit["kind"] in TARGET_KINDS]
            summary = data["summary"]
            counts = {"ARTICLE": summary["articles"], "ANNEX": summary["annexes"],
                      "TABLE": summary["xhtml_data_tables"]}
            if Counter(unit["kind"] for unit in units) != Counter({k: v for k, v in counts.items() if v}):
                raise ValueError(f"{celex}: target inventory disagrees with enriched summary.")
        if targets:
            by_locator = {evidence.locator(unit["id"]): unit for unit in units}
            if not set(targets) <= by_locator.keys():
                raise ValueError(f"{celex}: target locator not found: {sorted(set(targets) - by_locator.keys())}")
            units = [by_locator[locator] for locator in targets]
        locators = set()
        for unit in units:
            locators_for_context = SUPPORTING_LOCATORS.get((celex, unit["label"]), ())
            supporting_ids = [evidence.find_unit(locator)["id"] for locator in locators_for_context]
            pack = build_pack(evidence, unit, anchors, supporting_ids)
            locator = pack["provision"]["locator"]
            if locator in locators:
                raise ValueError(f"{celex}: duplicate provision locator {locator}.")
            locators.add(locator)
            packs.append((celex, unit["id"], unit["kind"], pack))
    return packs


def run(args):
    packs = configure(args)
    prompt = (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8")
    review_prompt = (ROOT / "prompts/eurlex_provision_ignore_review.md").read_text(encoding="utf-8")
    if getattr(args, "celex", None) and args.output_dir is None:
        raise ValueError("Set --output-dir for a subset run.")
    out = args.output_dir or ROOT / "runs" / (
        f"eurlex_provisions_{args.mode}_context_safe" + ("_prepared" if args.prepare_only else "")
    )
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"Output directory is locked: {out}. Confirm no run is active before removing .running.") from exc
    try:
        return run_packs(args, out, packs, prompt, review_prompt)
    finally:
        lock.unlink()


def run_packs(args, out, packs, prompt, review_prompt):
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
                       "prompt": prompt, "review_prompt": review_prompt, "code": scorer.read_text(encoding="utf-8"),
                       "runner": Path(__file__).read_text(encoding="utf-8")})
    run_file = audit / "run_config.json"
    if run_file.exists() and read_json(run_file)["fingerprint"] != fingerprint:
        raise ValueError("Run inputs changed. Choose a new --output-dir.")
    write_json(run_file, {"fingerprint": fingerprint, "model": model, "system_prompt": prompt,
                          "ignore_review_prompt": review_prompt})
    targets = []
    checkpoints = {}
    for celex, unit_id, kind, pack in packs:
        target = pack["provision"]["locator"]
        path = audit / celex / f"{kind.lower()}_{sha((unit_id, target))[:18]}.json"
        item = {"celex": celex, "target": target, "kind": kind, "target_id": unit_id,
                "file": path.relative_to(out).as_posix()}
        targets.append((item, path, pack))
        if path.exists():
            saved = read_json(path)
            if saved.get("fingerprint") != fingerprint:
                raise ValueError(f"Checkpoint inputs changed for {target}. Choose a new --output-dir.")
            checkpoints[path] = saved

    error = None
    try:
        for index, (item, path, pack) in enumerate(targets, 1):
            label = f"{item['celex']} {item['target']}"
            previous = checkpoints.get(path, {})
            if previous and "failure" not in previous and (not client or "level" in previous):
                continue
            print(f"[{index}/{len(targets)}] {label}", flush=True)
            saved = {**item, "fingerprint": fingerprint, "evidence_pack": pack}
            if client:
                try:
                    if "ignore_review" in previous:
                        saved["ignore_review"] = previous["ignore_review"]
                    else:
                        decision, attempt = score(client, prompt, pack)
                        if decision["level"] == "IGNORE":
                            saved["ignore_review"] = {
                                "status": "pending", "initial_decision": decision, "initial_attempt": attempt,
                            }
                            write_json(path, saved)
                            checkpoints[path] = saved
                    if "ignore_review" in saved:
                        review = saved["ignore_review"]
                        review_pack = {**pack, "initial_ignore": {
                            "reason": review["initial_decision"]["reason"],
                            "target_quote": review["initial_attempt"]["evidence"]["target_quote"],
                        }}
                        decision, attempt = score(client, f"{prompt}\n\n{review_prompt}", review_pack)
                        saved["ignore_review"] = {
                            **review, "status": "confirmed" if decision["level"] == "IGNORE" else "changed",
                        }
                except TargetScreeningError as exc:
                    saved["failure"] = {"error": str(exc), "attempts": exc.attempts,
                                        "stage": "ignore_review" if "ignore_review" in saved else "initial"}
                    print(f"Could not classify {label}: {exc}", flush=True)
                except ModelError as exc:
                    raise ModelError(f"{label}: {exc}") from exc
                else:
                    saved.update(decision, model_attempt=attempt)
            write_json(path, saved)
            checkpoints[path] = saved
    except ModelError as exc:
        error = str(exc)
        raise
    finally:
        completed = []
        failures = []
        reviews = Counter()
        for item, path, _ in targets:
            if path not in checkpoints:
                continue
            saved = checkpoints[path]
            review = saved.get("ignore_review")
            if review:
                reviews[review["status"]] += 1
            if "failure" in saved:
                failures.append({**item, "error": saved["failure"]["error"],
                                 "stage": saved["failure"]["stage"]})
            elif client:
                if "level" in saved:
                    completed.append({**item, **{field: saved[field] for field in DECISION_FIELDS}})
            else:
                completed.append(item)
        counts = Counter(row.get("level", "PREPARED") for row in completed)
        if client:
            results = {}
            for item in completed:
                results.setdefault(item["celex"], {})[item["target"]] = {
                    field: item[field] for field in DECISION_FIELDS
                }
            write_json(out / "relevance_export/results.json", results)
        write_json(audit / "run_summary.json", {
            "targets_planned": len(packs), "targets_saved": len(completed),
            "targets_exported": sum(counts[level] for level in LEVELS),
            "targets_failed": len(failures), "counts": dict(counts), "error": error,
            "ignore_reviews": {status: reviews[status] for status in ("confirmed", "changed", "pending")},
            "results": completed, "failures": failures,
        })
    print(f"Saved {len(completed)} targets to {out}", flush=True)
    if failures:
        print(f"{len(failures)} targets need a retry. Rerun the same command to retry them.", flush=True)
    return bool(failures)


if __name__ == "__main__":
    try:
        raise SystemExit(1 if run(arguments()) else 0)
    except (OSError, ValueError, KeyError, TypeError, ModelError, yaml.YAMLError) as exc:
        raise SystemExit(f"Provision screening stopped: {exc}") from exc
