"""Rerun the installed Qwen classifier, then evaluate that new run automatically.

Install alongside the existing tools/document_relevance.py. No manual calculation
or second evaluation command is needed. Ground truth never enters the model call.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from evaluate_document_screening import read_labels
from compare_screening_passes import evaluate_both_passes
from extract_evidence_inputs import extract_inputs

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")


def locate_input(explicit, diagnostic, root):
    if explicit is not None:
        path = Path(explicit).resolve()
    else:
        if not diagnostic.is_file():
            raise ValueError("Input cache pointer not found. Supply --input PATH_TO_eligible_acts.json")
        info = json.loads(diagnostic.read_text(encoding="utf-8-sig"))
        cache = info.get("eligible_cache") if isinstance(info, dict) else None
        if not isinstance(cache, str) or not cache.strip():
            raise ValueError("Diagnostic has no eligible_cache. Supply --input explicitly.")
        path = Path(cache)
        if not path.is_absolute():
            path = root / path
        path = path / "eligible_acts.json"
    if not path.exists():
        raise ValueError(f"Evidence input not found: {path}. Supply --input with its current path.")
    return path.resolve()


def classifier_command(args, identifiers, input_path, run_dir, root):
    # Only IDs are read from the labelling table for inference. The workbook,
    # ground-truth labels and previous model decisions are never model inputs.
    command = [sys.executable, "-u", str(root / "tools/document_relevance.py"),
            "--input", str(input_path), "--celex", ",".join(identifiers),
            "--as-of", args.as_of.isoformat(), "--anchors", str(args.anchors.resolve()),
            "--domains", str(args.domains.resolve()), "--env", str(args.env.resolve()),
            "--input-budget", str(args.input_budget), "--context-limit", str(args.context_limit),
            "--max-chunks", str(args.max_chunks), "--workers", str(args.workers),
            "--out", str(run_dir)]
    if args.no_ignore_review:
        command.append('--no-ignore-review')
    return command


def run_classifier(command, root, log_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with log_path.open("w", encoding="utf-8") as log:
        with subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            return process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True, help="Completed Excel or CSV; Label is ground truth")
    parser.add_argument("--sheet", default="Labels")
    parser.add_argument("--input", type=Path, help="Evidence export; otherwise use the saved dev cache pointer")
    parser.add_argument("--diagnostic", type=Path, default=ROOT / "data/dev400_new_v2_insufficient.json")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 4),
                        help="Assessment date (default 2026-11-04, matching the labelled development set)")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--domains", type=Path, default=ROOT / "config/domains.yaml")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--input-budget", type=int, default=34000)
    parser.add_argument("--context-limit", type=int, default=40960)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--max-chunks", type=int, default=0, help="0: token-budget selection")
    parser.add_argument("--no-ignore-review", action="store_true", help="Ablation: first pass only")
    parser.add_argument("--out", type=Path, help="New output folder; defaults to a unique runs/relevance_eval_* folder")
    args = parser.parse_args(argv)
    state_path = None
    state = {}
    try:
        if not (ROOT / "tools/document_relevance.py").is_file():
            raise ValueError("Extract this add-on into your existing AI-Experiments project: tools/document_relevance.py is missing.")
        if args.input_budget < 2048 or args.input_budget + 6000 + 512 > args.context_limit:
            raise ValueError("Input budget must be >=2048 and leave 6000 output tokens plus 512 tokens inside context-limit.")
        for path in (args.anchors, args.domains, args.env):
            if not path.is_file():
                raise ValueError(f"Required project file not found: {path}")
        prompt_path = ROOT / "prompts/document_relevance.txt"
        review_prompt_path = ROOT / "prompts/review_ignore.txt"
        for path in (prompt_path, review_prompt_path):
            if not path.is_file():
                raise ValueError(f"Classifier prompt not found: {path}")
        labels_path = args.labels.resolve()
        # Validate everything before contacting Qwen or creating run output.
        labels = read_labels(labels_path, sheet=args.sheet)
        input_path = locate_input(args.input, args.diagnostic.resolve(), ROOT)
        if args.out is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = ROOT / "runs" / f"relevance_eval_{stamp}_{uuid4().hex[:8]}"
        else:
            output = args.out.resolve()
        output.mkdir(parents=True, exist_ok=False)
        truth_dir = output / "ground_truth"
        truth_dir.mkdir()
        frozen_labels = truth_dir / ("labels" + labels_path.suffix.lower())
        initial_hash = file_hash(labels_path)
        shutil.copy2(labels_path, frozen_labels)
        if file_hash(frozen_labels) != initial_hash:
            raise ValueError("Labels changed while being copied. Close Excel and rerun.")
        run_dir = output / "predictions"
        run_dir.mkdir()
        prompt_dir = output / "prompts_used"
        prompt_dir.mkdir()
        shutil.copy2(prompt_path, prompt_dir / prompt_path.name)
        shutil.copy2(review_prompt_path, prompt_dir / review_prompt_path.name)
        prompt_hash = file_hash(prompt_dir / prompt_path.name)
        if prompt_hash != file_hash(prompt_path):
            raise ValueError("Prompt changed while being copied. Rerun after finishing the edit.")
        print(f'Extracting {len(labels)} labelled IDs and preparing evidence...',flush=True)
        selected_input = extract_inputs(input_path, list(labels), output / 'extracted_input', args.as_of)
        command = classifier_command(args, list(labels), selected_input, run_dir, ROOT)
        state = {
            "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
            "labelled_acts": len(labels), "selected_ids": list(labels),
            "truth_column": "Label", "mapping": {"DIRECT": "RELEVANT", "INDIRECT": "RELEVANT", "IGNORE": "IGNORE"},
            "original_labels_file": str(labels_path), "frozen_labels_file": str(frozen_labels),
            "labels_sha256": initial_hash, "input": str(input_path),
            "extracted_input": str(selected_input),
            "assessment_date": args.as_of.isoformat(), "classification_command": command,
            "decision_prompt_sha256": prompt_hash,
            "review_prompt_sha256": file_hash(prompt_dir / review_prompt_path.name),
            "ignore_review": not args.no_ignore_review,
            "decision_prompt_snapshot": str(prompt_dir / prompt_path.name),
            "note": "No labels or review columns are sent to Qwen. Uses installed classifier and prompt.",
        }
        state_path = output / "pipeline.json"
        save_json(state_path, state)
        print(f"Fresh inference for {len(labels)} acts. Output: {output}", flush=True)
        print(f"Decision prompt SHA256: {prompt_hash}", flush=True)
        inference_code = run_classifier(command, ROOT, output / "inference.log")
        state["inference_exit_code"] = inference_code
        save_json(state_path, state)
        # Always evaluate saved outputs after a normal runner exit, including a
        # failed/partial run. Evaluator exposes failures instead of making IGNOREs.
        print("\nQwen finished. Evaluating the new predictions against Label...", flush=True)
        eval_dir = output / "evaluation"
        evaluation_code = evaluate_both_passes(frozen_labels, args.sheet, run_dir, output)
        state["evaluation_exit_code"] = evaluation_code
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["original_labels_unchanged"] = file_hash(labels_path) == initial_hash
        state["status"] = "completed" if inference_code == 0 and evaluation_code == 0 else "incomplete_or_failed"
        save_json(state_path, state)
        print(f"\nReport: {eval_dir / 'report.md'}", flush=True)
        print(f"Metrics: {eval_dir / 'metrics.json'}", flush=True)
        print(f"Missed acts: {eval_dir / 'false_negatives.csv'}", flush=True)
        print(f"False positives: {eval_dir / 'false_positives.csv'}", flush=True)
        if inference_code or evaluation_code:
            print("Some steps failed or predictions are missing. Inspect inference.log and evaluation/unresolved.csv.")
            return 2
        return 0
    except KeyboardInterrupt:
        if state_path:
            state.update(status="interrupted", finished_at=datetime.now(timezone.utc).isoformat())
            save_json(state_path, state)
        print("\nInterrupted. Partial files remain in the new output folder.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError) as exc:
        if state_path:
            state.update(status="failed", error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
            save_json(state_path, state)
        print(f"Run-and-evaluate stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
