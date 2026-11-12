"""Classify each provision of the selected acts for Max Bögl."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading

import yaml

from enrich_eurlex import write_json
from eurlex_enrichment.inputs import read_json
from eurlex_provision import evidence as evidence_module
from eurlex_provision.evidence import TARGET_KINDS
from eurlex_provision.profile import company_profile, system_prompt
from eurlex_provision.quality import WARNING_TEXT, assess
from eurlex_provision.screening import ModelError
from eurlex_provision.simple import (DECISION_FIELDS, KEPT_LEVELS, LEVELS, TargetScreeningError, build_pack,
                                     screen, target_text)
from lr_ai.qwen_client import QwenClient
from screen_eurlex_provisions import ROOT, load_supplements, sha

SAMPLES = {"32024R3110": "Article 2", "32015L2193": "Article 6", "32013D0163": "BAT 17"}
SUPPORTING_LOCATORS = {
    ("32015L2193", "Article 6"): ("Article 2", "Article 3"),
    ("32013D0163", "BAT 17"): ("1.5.1 Dust emissions",),
}
ANCHOR_FACTS = ROOT / "config/eurlex_provision_anchor_facts.json"
MAX_WORKERS = 8
CODE_FILES = ("eurlex_provision/simple.py", "eurlex_provision/quality.py", "eurlex_provision/profile.py")


class UniqueTargetEvidence(evidence_module.Evidence):
    """Keep existing labels unless two targets would be exported under the same name."""

    def __init__(self, data, supplements):
        self.unique_locators = {}
        super().__init__(data, supplements)
        targets_by_label = defaultdict(list)
        for key, unit in self.units.items():
            if unit["kind"] in TARGET_KINDS:
                targets_by_label[self.locator(key)].append(key)
        occupied = set(targets_by_label)
        for label, keys in targets_by_label.items():
            if len(keys) == 1:
                continue
            for key in sorted(keys):
                unique = f"{label} [source ID {key}]"
                while unique in occupied:
                    unique += " [duplicate]"
                occupied.add(unique)
                self.unique_locators[key] = unique
                self.aliases[evidence_module.norm(unique)].add(key)
                if key in self.tables:
                    for row in self.tables[key]["rows"]:
                        alias = self.row_locator(key, row["row"])
                        self.aliases[evidence_module.norm(alias)].add((key, row["row"]))
        if self.unique_locators:
            print(f"{self.document['celex']}: disambiguated {len(self.unique_locators)} target labels.",
                  flush=True)

    def locator(self, key):
        if key in self.unique_locators:
            return self.unique_locators[key]
        return super().locator(key)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("samples", "holdout50", "pilot"))
    parser.add_argument("--celex", nargs="+", help="Limit pilot mode to these manifest IDs")
    parser.add_argument("--target", nargs="+", help="Exact target locators for one selected act")
    parser.add_argument("--targets-file", type=Path,
                        help="JSON file mapping CELEX IDs to target locators. Limits pilot mode to these targets.")
    parser.add_argument("--prepare-only", action="store_true", help="Save evidence without calling the model")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db")
    parser.add_argument("--manifest", type=Path, default=ROOT / "pilot_acts.json")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml",
                        help="Business anchor catalog (roles, negative anchors and anchors)")
    parser.add_argument("--document-run", type=Path,
                        help="Document relevance run folder. Adds each act's document decision as context.")
    parser.add_argument("--thinking", choices=("on", "off"), default="off",
                        help="Let Qwen think before it answers. Slower; use it to compare on a small sample.")
    parser.add_argument("--workers", type=int, default=1,
                        help="Targets classified at the same time (1 to 8). 4 was fastest in the endpoint check.")
    parser.add_argument("--confirm-thinking", choices=("on", "off"), default="off",
                        help="Let Qwen think in the confirmation pass for IGNORE only.")
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def document_decisions(run):
    """Read why each act was kept at document level, if a document run is given."""
    if run is None:
        return {}
    decisions = {}
    for level in ("direct", "indirect"):
        records = read_json(run / "relevance_export" / f"{level}.json")
        if not isinstance(records, dict):
            raise ValueError(f"{level}.json in the document run must map CELEX IDs to decisions.")
        for celex, decision in records.items():
            decisions[celex] = {"level": decision["level"], "reason": decision["reason"],
                                "anchors": list(decision.get("area", []))}
    return decisions


def load_profile(path):
    catalog = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    facts = read_json(ANCHOR_FACTS) if ANCHOR_FACTS.is_file() else None
    return company_profile(catalog, facts)


def check_target(evidence, unit, pack):
    provision = pack["provision"]
    origin = evidence.tables[unit["id"]].get("origin") if unit["kind"] == "TABLE" else None
    return assess(unit["kind"], unit["label"], list(target_text(provision)),
                  provision.get("heading"), origin, single_text="text" in provision)


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
    chosen = None
    if getattr(args, "targets_file", None):
        chosen = read_json(args.targets_file)
        if args.mode != "pilot" or requested or targets:
            raise ValueError("--targets-file requires pilot mode without --celex or --target.")
        if (not isinstance(chosen, dict) or not chosen or not all(
                isinstance(values, list) and values and len(set(values)) == len(values)
                and all(isinstance(value, str) for value in values) for values in chosen.values())):
            raise ValueError("The targets file must map CELEX IDs to lists of unique target locators.")
        if unknown := sorted(set(chosen) - manifest.keys()):
            raise ValueError(f"The targets file names acts that are not in the manifest: {', '.join(unknown)}")
        selected = {celex: manifest[celex] for celex in chosen}
    holdout = None
    if args.mode == "holdout50":
        holdout = read_json(ROOT / "config/eurlex_provision_holdout50.json")
        if set(holdout) != set(selected) or sum(map(len, holdout.values())) != 50:
            raise ValueError("The holdout must specify 50 targets from the three pilot acts.")
    supplements = load_supplements(args.supplements)
    decisions = document_decisions(getattr(args, "document_run", None))
    packs, not_found = [], []
    for celex, version in selected.items():
        data = read_json(args.enriched_dir / f"{celex}_enriched.json")
        document = data["document"]
        if data["schema_version"] != 1 or (document["celex"], document["version_id"]) != (celex, version):
            raise ValueError(f"{celex}: enriched text does not match the pinned source version.")
        evidence = UniqueTargetEvidence(data, supplements)
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
                      "TABLE": summary["xhtml_data_tables"] + summary.get("pdf_data_tables", 0)}
            if Counter(unit["kind"] for unit in units) != Counter({k: v for k, v in counts.items() if v}):
                raise ValueError(f"{celex}: target inventory disagrees with enriched summary.")
        wanted = chosen[celex] if chosen else targets
        if wanted:
            by_locator = {evidence.locator(unit["id"]): unit for unit in units}
            missing = [locator for locator in wanted if locator not in by_locator]
            if missing and not chosen:
                raise ValueError(f"{celex}: target locator not found: {sorted(missing)}")
            # A targets file may hold names from an older run. Report those and go on with the rest.
            not_found.extend({"celex": celex, "target": locator} for locator in missing)
            units = [by_locator[locator] for locator in wanted if locator in by_locator]
        locators = set()
        for unit in units:
            locators_for_context = SUPPORTING_LOCATORS.get((celex, unit["label"]), ())
            supporting_ids = [evidence.find_unit(locator)["id"] for locator in locators_for_context]
            pack = build_pack(evidence, unit, supporting_ids)
            locator = pack["provision"]["locator"]
            if locator in locators:
                raise ValueError(f"{celex}: duplicate provision locator {locator}.")
            locators.add(locator)
            skip, warnings = check_target(evidence, unit, pack)
            packs.append({"celex": celex, "unit_id": unit["id"], "kind": unit["kind"], "pack": pack,
                          "not_assessable": skip, "input_warnings": warnings,
                          "document_screening": decisions.get(celex)})
    if not packs:
        raise ValueError("No target was selected. Check the target locators.")
    for item in not_found:
        print(f"Target not found and left out: {item['celex']} {item['target']}", flush=True)
    return packs, not_found


def run(args):
    packs, not_found = configure(args)
    profile = load_profile(args.anchors)
    system = system_prompt(
        (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8"),
        (ROOT / "prompts/eurlex_provision_ignore_review.md").read_text(encoding="utf-8"),
        profile,
    )
    if (getattr(args, "celex", None) or getattr(args, "targets_file", None)) and args.output_dir is None:
        raise ValueError("Set --output-dir for a subset run.")
    out = args.output_dir or ROOT / "runs" / (
f"eurlex_provisions_{args.mode}_v3_2" + ("_prepared" if args.prepare_only else "")
    )
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"Output directory is locked: {out}. Confirm no run is active before removing .running.") from exc
    try:
        return run_packs(args, out, packs, system, profile, not_found)
    finally:
        lock.unlink()


def request_totals(saved):
    attempts = [saved.get("model_attempt"), (saved.get("ignore_review") or {}).get("initial_attempt")]
    return [request for attempt in attempts if attempt for request in attempt.get("requests", [])]


def summarise(targets, checkpoints, client, error):
    completed, failures, skipped, warned = [], [], [], []
    reviews, warnings, ignore_reasons, negatives, paths = Counter(), Counter(), Counter(), Counter(), Counter()
    roles, bases, topics, formal, kinds = Counter(), Counter(), Counter(), Counter(), Counter()
    by_act = defaultdict(lambda: {"planned": 0, "done": 0, "levels": Counter(), "failed": 0})
    requests = []
    for item, path, entry in targets:
        act = by_act[item["celex"]]
        act["planned"] += 1
        if path not in checkpoints:
            continue
        saved = checkpoints[path]
        requests.extend(request_totals(saved))
        warnings.update(saved.get("input_warnings", []))
        if saved.get("input_warnings"):
            warned.append({**item, "warnings": saved["input_warnings"]})
        if review := saved.get("ignore_review"):
            reviews[review["status"]] += 1
        if "not_assessable" in saved:
            skipped.append({**item, "code": saved["not_assessable"]["code"],
                            "text": saved["not_assessable"].get("text", "")})
            act["done"] += 1
            if not client:
                completed.append(item)
        elif "failure" in saved:
            failures.append({**item, "error": saved["failure"]["error"], "stage": saved["failure"]["stage"]})
            act["failed"] += 1
        elif not client:
            completed.append(item)
        elif "level" in saved:
            completed.append({**item, **{field: saved[field] for field in DECISION_FIELDS}})
            act["done"] += 1
            act["levels"][saved["level"]] += 1
            details = saved.get("details", {})
            bases[details.get("basis", "UNKNOWN")] += 1
            topics[details.get("topic", "UNKNOWN")] += 1
            kinds[details.get("actor_kind", "UNKNOWN")] += 1
            if details.get("formal_function", "NONE") != "NONE":
                formal[details["formal_function"]] += 1
            if saved["level"] == "IGNORE":
                ignore_reasons[details.get("ignore_reason", "UNKNOWN")] += 1
                if details.get("negative_anchor", "NONE") != "NONE":
                    negatives[details["negative_anchor"]] += 1
            elif saved["level"] == "INDIRECT":
                paths[details.get("indirect_path", "UNKNOWN")] += 1
            elif details.get("company_role", "NONE") != "NONE":
                roles[details["company_role"]] += 1
    counts = Counter(row.get("level", "PREPARED") for row in completed)
    cached = [request["cached_tokens"] for request in requests if request.get("cached_tokens") is not None]
    summary = {
        "targets_planned": len(targets), "targets_saved": len(completed),
        "targets_exported": sum(counts[level] for level in LEVELS),
        "targets_not_assessable": len(skipped), "targets_failed": len(failures),
        "counts": dict(counts), "error": error,
        "not_assessable": dict(Counter(row["code"] for row in skipped)),
        "input_warnings": dict(warnings),
        "targets_kept": sum(counts[level] for level in KEPT_LEVELS),
        "decision_basis": dict(bases),
        "ignore_reasons": dict(ignore_reasons), "negative_anchors": dict(negatives),
        "indirect_paths": dict(paths), "company_roles": dict(roles),
        "formal_functions": dict(formal), "actor_kinds": dict(kinds),
        "topics": dict(topics.most_common()),
        "ignore_reviews": {status: reviews[status] for status in ("confirmed", "changed", "pending")},
        # Acts in which every classified provision was ignored: candidates for a document-level recheck.
        "acts_all_ignore": sorted(
            celex for celex, act in by_act.items()
            if act["levels"] and set(act["levels"]) == {"IGNORE"}
            and act["done"] == act["planned"] and not act["failed"]),
        "requests": {
            "count": len(requests), "seconds": round(sum(r.get("seconds") or 0 for r in requests), 1),
            "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in requests),
            "cached_tokens": sum(cached) if cached else None,
        },
        "results": completed, "not_assessable_targets": skipped,
        "targets_with_incomplete_text": warned, "failures": failures,
    }
    return summary, completed


def run_packs(args, out, packs, system, profile, not_found=()):
    client = None
    if not args.prepare_only:
        client = QwenClient.from_env(args.env_file, retries=0)
        discovered = client.discover()
        if not discovered["ok"]:
            raise ModelError(discovered["error"])

    audit = out / "audit"
    model = client.model if client else None
    thinking = getattr(args, "thinking", "off") == "on"
    confirm_thinking = thinking or getattr(args, "confirm_thinking", "off") == "on"
    fingerprint = sha({"packs": packs, "model": model, "prepare_only": args.prepare_only, "system": system,
                       "thinking": thinking, "confirm_thinking": confirm_thinking,
                       "code": [(ROOT / name).read_text(encoding="utf-8") for name in CODE_FILES],
                       "runner": Path(__file__).read_text(encoding="utf-8")})
    run_file = audit / "run_config.json"
    if run_file.exists() and read_json(run_file)["fingerprint"] != fingerprint:
        raise ValueError("Run inputs changed. Choose a new --output-dir.")
    write_json(run_file, {"fingerprint": fingerprint, "model": model, "thinking": thinking,
                          "confirm_thinking": confirm_thinking,
                          "document_hint": any(entry["document_screening"] for entry in packs),
                          "system_prompt": system})
    targets = []
    checkpoints = {}
    for entry in packs:
        target = entry["pack"]["provision"]["locator"]
        path = audit / entry["celex"] / f"{entry['kind'].lower()}_{sha((entry['unit_id'], target))[:18]}.json"
        item = {"celex": entry["celex"], "target": target, "kind": entry["kind"],
                "target_id": entry["unit_id"], "file": path.relative_to(out).as_posix()}
        targets.append((item, path, entry))
        if path.exists():
            saved = read_json(path)
            if saved.get("fingerprint") != fingerprint:
                raise ValueError(f"Checkpoint inputs changed for {target}. Choose a new --output-dir.")
            checkpoints[path] = saved

    stop = threading.Event()

    def process(index, item, path, entry, previous):
        """Classify one target and save its file. Runs in a worker thread."""
        if stop.is_set():
            return None
        label = f"{item['celex']} {item['target']}"
        pack = entry["pack"]
        saved = {**item, "fingerprint": fingerprint, "evidence_pack": pack}
        if entry["input_warnings"]:
            saved["input_warnings"] = entry["input_warnings"]
        if entry["not_assessable"]:
            text = " ".join(" ".join(target_text(pack["provision"])).split())
            saved["not_assessable"] = {**entry["not_assessable"], "text": text[:300]}
        elif client:
            print(f"[{index}/{len(targets)}] {label}", flush=True)
            context = {"act_context": entry["document_screening"],
                       "warnings": [WARNING_TEXT[code] for code in entry["input_warnings"]]}
            try:
                if "ignore_review" in previous:
                    saved["ignore_review"] = previous["ignore_review"]
                else:
                    decision, attempt = screen(client, system, profile, pack, thinking=thinking, **context)
                    if decision["level"] == "IGNORE":
                        saved["ignore_review"] = {
                            "status": "pending", "initial_decision": decision, "initial_attempt": attempt,
                        }
                        write_json(path, saved)
                if "ignore_review" in saved:
                    review = saved["ignore_review"]
                    decision, attempt = screen(client, system, profile, pack, thinking=confirm_thinking,
                                               first=review["initial_attempt"], **context)
                    saved["ignore_review"] = {
                        **review, "status": "confirmed" if decision["level"] == "IGNORE" else "changed",
                    }
            except TargetScreeningError as exc:
                saved["failure"] = {"error": str(exc), "attempts": exc.attempts,
                                    "stage": "ignore_review" if "ignore_review" in saved else "initial"}
                print(f"Could not classify {label}: {exc}", flush=True)
            except ModelError as exc:
                # The endpoint failed. Let the other workers finish their request and stop the run.
                stop.set()
                raise ModelError(f"{label}: {exc}") from exc
            else:
                saved.update(decision, details=attempt["evidence"], model_attempt=attempt)
        write_json(path, saved)
        return saved

    error = None
    try:
        todo = []
        for index, (item, path, entry) in enumerate(targets, 1):
            previous = checkpoints.get(path, {})
            if previous and "failure" not in previous and (
                    not client or "level" in previous or "not_assessable" in previous):
                continue
            todo.append((index, item, path, entry, previous))
        workers = max(1, min(int(getattr(args, "workers", 1) or 1), MAX_WORKERS)) if client else 1
        failure = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process, *job): job[2] for job in todo}
            try:
                for future in as_completed(futures):
                    try:
                        saved = future.result()
                    except ModelError as exc:
                        failure = failure or exc
                        continue
                    if saved is not None:
                        checkpoints[futures[future]] = saved
            except BaseException:
                # Ctrl+C: targets that have not started are skipped; running ones finish and are saved.
                stop.set()
                raise
        if failure:
            raise failure
    except ModelError as exc:
        error = str(exc)
        raise
    finally:
        # Count what the workers saved before a stop, including a first pass that awaits its confirmation.
        for _, path, _ in targets:
            if path not in checkpoints and path.exists():
                checkpoints[path] = read_json(path)
        summary, completed = summarise(targets, checkpoints, client, error)
        summary["targets_not_found"] = list(not_found)
        if client:
            results = {}
            for item in completed:
                results.setdefault(item["celex"], {})[item["target"]] = {
                    field: item[field] for field in DECISION_FIELDS
                }
            write_json(out / "relevance_export/results.json", results)
        write_json(audit / "run_summary.json", summary)
    print(f"Saved {len(completed)} targets to {out}", flush=True)
    if summary["targets_not_assessable"]:
        print(f"{summary['targets_not_assessable']} targets are not assessable. "
              "See not_assessable_targets in audit/run_summary.json.", flush=True)
    if summary["failures"]:
        print(f"{len(summary['failures'])} targets need a retry. Rerun the same command to retry them.", flush=True)
    return bool(summary["failures"])


if __name__ == "__main__":
    try:
        raise SystemExit(1 if run(arguments()) else 0)
    except (OSError, ValueError, KeyError, TypeError, ModelError, yaml.YAMLError) as exc:
        raise SystemExit(f"Provision screening stopped: {exc}") from exc
