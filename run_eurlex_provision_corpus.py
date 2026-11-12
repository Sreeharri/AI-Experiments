"""Run provision screening over the verified EUR-Lex corpus in resumable batches."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from enrich_eurlex import write_json
from enrich_eurlex_corpus import PROVISION_METRICS, read_manifest, verify_enriched
from eurlex_enrichment.inputs import read_json


ROOT = Path(__file__).resolve().parent
SMOKE_ACTS = ("31998D0600", "31964L0475")
LEVELS = {"DIRECT", "INDIRECT", "UNSURE", "IGNORE"}
FIELDS = {"level", "reason", "area"}
MAX_ACTS = 20
MAX_TARGETS = 250


def prepare_plan(args):
    manifest = read_manifest(args.manifest)
    enrichment = read_json(args.enriched_dir / "enrichment_run_summary.json")
    if (enrichment["acts_planned"] != len(manifest)
            or enrichment["acts_valid"] != len(manifest)
            or enrichment["acts_failed"] or enrichment["acts_pending"] or enrichment["error"]):
        raise ValueError("The enrichment report must cover the full manifest with no failures or pending acts.")

    inputs = sha256()
    settings = [
        Path(__file__), ROOT / "screen_eurlex_provisions_simple.py",
        ROOT / "eurlex_provision/simple.py", ROOT / "eurlex_provision/evidence.py",
        ROOT / "eurlex_provision/screening.py", ROOT / "eurlex_provision/quality.py",
        ROOT / "eurlex_provision/profile.py",
        ROOT / "prompts/eurlex_provision_simple.md", ROOT / "prompts/eurlex_provision_ignore_review.md",
        args.anchors,
    ]
    if args.document_run:
        settings.extend(args.document_run / "relevance_export" / f"{level}.json"
                        for level in ("direct", "indirect"))
    for optional in ("lr_ai/qwen_client.py", "config/eurlex_provision_anchor_facts.json"):
        if (ROOT / optional).is_file():
            settings.append(ROOT / optional)
    for path in settings:
        inputs.update(sha256(path.read_bytes()).digest())
    for option in ("thinking", "confirm_thinking"):
        if getattr(args, option, "off") == "on":
            inputs.update(option.encode())

    provision_counts = Counter()
    provision_inventory = {}
    document_text_only, without_targets = [], []
    for celex, version in manifest.items():
        payload = (args.enriched_dir / f"{celex}_enriched.json").read_bytes()
        record = json.loads(payload.decode("utf-8-sig"))
        metrics = verify_enriched(record, celex, version)
        count = sum(metrics[field] for field in PROVISION_METRICS)
        for field in PROVISION_METRICS:
            provision_counts[field] += metrics[field]
        if count:
            provision_inventory[celex] = count
            inputs.update(sha256(payload).digest())
        elif metrics["document_text_targets"]:
            document_text_only.append(celex)
        else:
            without_targets.append(celex)

    if (dict(provision_counts) != enrichment["provisions"]
            or set(document_text_only) != set(enrichment["acts_with_document_text_only"])
            or set(document_text_only + without_targets) != set(enrichment["acts_without_provision_targets"])):
        raise ValueError("Enriched files and the final enrichment report disagree. Verify enrichment before screening.")
    if not all(celex in provision_inventory for celex in SMOKE_ACTS):
        raise ValueError("The PDF and legacy HTML smoke-test acts must have provision targets.")

    batches = []
    acts, target_count = {}, 0
    for celex, count in provision_inventory.items():
        if acts and (len(acts) >= MAX_ACTS or target_count + count > MAX_TARGETS):
            batches.append({"manifest": acts, "targets": target_count})
            acts, target_count = {}, 0
        acts[celex] = manifest[celex]
        target_count += count
    if acts:
        batches.append({"manifest": acts, "targets": target_count})
    return {
        "inputs_sha256": inputs.hexdigest(),
        "acts_with_provisions": len(provision_inventory), "targets": sum(provision_counts.values()),
        "document_text_only": document_text_only, "without_targets": without_targets,
        "batches": batches, "smoke_manifest": {celex: manifest[celex] for celex in SMOKE_ACTS},
        "smoke_targets": sum(provision_inventory[celex] for celex in SMOKE_ACTS),
    }


def run_batch(args, directory, manifest, prepare_only=False):
    manifest_path = directory / "manifest.json"
    write_json(manifest_path, manifest)
    command = [
        sys.executable, str(ROOT / "screen_eurlex_provisions_simple.py"), "pilot",
        "--manifest", str(manifest_path), "--enriched-dir", str(args.enriched_dir),
        "--anchors", str(args.anchors), "--env-file", str(args.env_file),
        "--output-dir", str(directory),
    ]
    if args.document_run:
        command.extend(["--document-run", str(args.document_run)])
    command.extend(["--thinking", args.thinking, "--confirm-thinking", args.confirm_thinking])
    if prepare_only:
        command.append("--prepare-only")
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def batch_summary(directory, expected):
    path = directory / "audit" / "run_summary.json"
    if not path.exists():
        return {}, None
    summary = read_json(path)
    if summary["targets_planned"] != expected:
        raise ValueError(f"Unexpected target count in {path}.")
    return summary, path.stat().st_mtime_ns


def handled(summary):
    """Targets with a final outcome: classified, or reported as not assessable."""
    return summary.get("targets_exported", 0) + summary.get("targets_not_assessable", 0)


def complete(summary, expected):
    return (bool(summary) and handled(summary) == expected
            and not summary.get("targets_failed") and not summary.get("error"))


def batch_directory(args, number):
    return args.output_dir / "batches" / f"{number:04d}"


def export_results(args, plan, stopped):
    results = {}
    failed, not_assessable, all_ignore = [], [], []
    counts = Counter()
    tallies = {name: Counter() for name in ("not_assessable", "input_warnings", "decision_basis",
                                            "ignore_reasons", "negative_anchors", "indirect_paths",
                                            "company_roles", "formal_functions", "actor_kinds", "topics",
                                            "ignore_reviews")}
    requests = Counter()
    models = set()
    for number, batch in enumerate(plan["batches"], 1):
        directory = batch_directory(args, number)
        summary, _ = batch_summary(directory, batch["targets"])
        if not summary:
            continue
        models.add(read_json(directory / "audit" / "run_config.json")["model"])
        path = directory / "relevance_export" / "results.json"
        export = read_json(path)
        if not export.keys() <= batch["manifest"].keys():
            raise ValueError(f"Unexpected act IDs in {path}.")
        batch_counts = Counter()
        for celex, provisions in export.items():
            for decision in provisions.values():
                if decision.keys() != FIELDS or decision["level"] not in LEVELS:
                    raise ValueError(f"Unexpected result fields or relevance level in {path}.")
                batch_counts[decision["level"]] += 1
            if celex in results:
                raise ValueError(f"Duplicate act {celex} in batch exports.")
            results[celex] = provisions
        if sum(batch_counts.values()) != summary["targets_exported"]:
            raise ValueError(f"Export and batch summary disagree in {directory}.")
        counts.update(batch_counts)
        failed.extend(summary["failures"])
        not_assessable.extend(summary["not_assessable_targets"])
        all_ignore.extend(summary["acts_all_ignore"])
        for name, tally in tallies.items():
            tally.update(summary[name])
        requests.update({key: value for key, value in summary["requests"].items() if value})

    if len(models) > 1:
        raise ValueError("Batch checkpoints use different Qwen models. Keep the original model for this output directory.")
    exported = sum(counts.values())
    write_json(args.output_dir / "relevance_export" / "results.json", results)
    done = exported + len(not_assessable)
    summary = {
        "acts_with_provisions": plan["acts_with_provisions"],
        "targets_planned": plan["targets"], "targets_exported": exported,
        "targets_not_assessable": len(not_assessable),
        "targets_failed": len(failed), "targets_pending": plan["targets"] - done - len(failed),
        "counts": {level: counts[level] for level in sorted(LEVELS)}, "model": next(iter(models), None),
        **{name: dict(tally) for name, tally in tallies.items()},
        "requests": {**dict(requests), "seconds": round(requests["seconds"], 1)},
        "acts_all_ignore": sorted(all_ignore),
        "document_text_only": plan["document_text_only"], "without_targets": plan["without_targets"],
        "stop_reason": stopped, "failures": failed,
    }
    write_json(args.output_dir / "run_summary.json", summary)
    write_json(args.output_dir / "not_assessable.json", not_assessable)
    print(f"{exported:,} results exported and {len(not_assessable):,} targets reported as not assessable, "
          f"of {plan['targets']:,} planned. Folder: {args.output_dir}", flush=True)
    return done == plan["targets"]


def run(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock = args.output_dir / ".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"{lock} exists. Check whether a corpus screening process is active.") from exc
    try:
        return run_locked(args)
    finally:
        lock.unlink()


def run_locked(args):
    plan = prepare_plan(args)
    path = args.output_dir / "batch_plan.json"
    if path.exists() and read_json(path) != plan:
        raise ValueError("Enriched inputs or screening settings changed. Use a new output directory.")
    if not path.exists():
        write_json(path, plan)
    print(f"{plan['targets']:,} targets from {plan['acts_with_provisions']:,} acts "
          f"in {len(plan['batches']):,} batches", flush=True)

    smoke_dir = args.output_dir / "prepare_check"
    prepared, _ = batch_summary(smoke_dir, plan["smoke_targets"])
    if prepared.get("targets_saved") != plan["smoke_targets"]:
        print("Preparing evidence for the PDF and legacy HTML smoke-test acts.", flush=True)
        exit_code = run_batch(args, smoke_dir, plan["smoke_manifest"], prepare_only=True)
        prepared, _ = batch_summary(smoke_dir, plan["smoke_targets"])
        if exit_code or prepared.get("targets_saved") != plan["smoke_targets"]:
            raise ValueError("The prepare-only smoke test did not complete. Check its terminal output.")

    stopped = None
    try:
        for number, batch in enumerate(plan["batches"], 1):
            directory = batch_directory(args, number)
            summary, previous_stamp = batch_summary(directory, batch["targets"])
            if complete(summary, batch["targets"]):
                continue
            print(f"Batch {number}/{len(plan['batches'])} · {batch['targets']:,} targets", flush=True)
            exit_code = run_batch(args, directory, batch["manifest"])
            summary, stamp = batch_summary(directory, batch["targets"])
            if (stamp == previous_stamp or summary.get("error")
                    or handled(summary) + summary.get("targets_failed", 0) != batch["targets"]
                    or (exit_code and not summary.get("targets_failed"))):
                stopped = f"Batch {number} stopped. See the terminal output and batch audit."
                break
    except KeyboardInterrupt:
        stopped = "Interrupted. Run the same command to resume."
    finally:
        finished = export_results(args, plan, stopped)

    if stopped:
        print(stopped, flush=True)
    return 0 if finished and not stopped else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "runs/eurlex_full_v10/provision_manifest.json")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db_corpus")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--document-run", type=Path,
                        help="Document relevance run folder. Default: the manifest's folder, "
                             "when it holds relevance_export/direct.json and indirect.json.")
    parser.add_argument("--no-document-hint", action="store_true",
                        help="Do not tell Qwen why the act was kept at document level.")
    parser.add_argument("--thinking", choices=("on", "off"), default="off",
                        help="Let Qwen think before it answers. Slower.")
    parser.add_argument("--confirm-thinking", choices=("on", "off"), default="off",
                        help="Let Qwen think in the confirmation pass for IGNORE only.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/eurlex_provisions_full_v3_1")
    args = parser.parse_args()
    if args.document_run is None and not args.no_document_hint:
        export = args.manifest.parent / "relevance_export"
        if (export / "direct.json").is_file() and (export / "indirect.json").is_file():
            args.document_run = args.manifest.parent
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    try:
        return run(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Provision screening stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
