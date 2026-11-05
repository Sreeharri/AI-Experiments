# Corrected filtering prompt + automatic rerun and evaluation

This package applies the proposed filtering correction. It includes
`prompts/document_relevance.txt` at the actual path loaded by the existing
document filter. Extract it into your existing AI-Experiments project.

## What changed

Only the decision prompt changes Qwen's classification instructions:

1. Before IGNORE, consider purchased equipment/materials, fuel/electricity costs
   and company rights within the supplied profile, without requiring a current
   transaction or proof that a specific provision applies.
2. Respect excluded roles and anchor exclusions, including their exceptions.
3. Retain indirect relevance for a meaningful business connection. A shared
   topic or generic hypothetical public-budget effect is not enough.
4. Do not invent manufacturing/importing activities. State a necessary unconfirmed
   assumption conditionally in the short reason.
5. Consider explicit expired periods using the supplied assessment date; do not
   infer expiry from an act's age. Future duties can still be relevant.

Document-level screening stays broad. No exact keyword matching, quotation
extraction, required article headings or Python relevance overrides are added.
The decision still has only `reason`, `anchor_ids` and `level`.

Evidence selection, full company profile, model, worker count and token settings
are not changed by the correction. The existing `lr_ai/document_filter.py`,
`tools/document_relevance.py`, Qwen client and business YAML files are not replaced.
This is an add-on for your existing working project, not a standalone model client.

## Run from your AI-Experiments project terminal (PowerShell)

Optional: preserve the old prompt before extracting:

```powershell
Copy-Item .\prompts\document_relevance.txt (".\prompts\document_relevance_before_correction_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".txt")
```

Then run these commands in order:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\relevance_filter_corrected.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements_evaluation.txt
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx
```

Keep your completed workbook in `data/simple_labels_with_astro_review.xlsx`.
Save edits first. Only Label is ground truth; LLM label and Astro label are
not overrides. The workbook is not edited. Ground truth is not sent to Qwen.

The saved input pointer `data/dev400_new_v2_insufficient.json` locates
`eligible_cache/eligible_acts.json`. If it has moved, supply `--input`:

```powershell
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx --input "C:\path\to\eligible_acts.json"
```

The wrapper selects exactly the labelled IDs (your 200), even if the evidence
cache contains 389 acts. Every launch creates a new output folder. It does not
load old predictions as the new run. It uses your existing model endpoint in
`.env`, four workers, 34000 input tokens, context 40960 and up to 24 chunks.
The default assessment date stays 2026-11-04 to match the labelled experiment.

## Automatic evaluation

After Qwen finishes, the code calculates and prints precision, recall, accuracy,
F1 and the binary confusion matrix. The saved report also contains specificity,
balanced accuracy, miss rate, retention rate and confidence intervals.

**DIRECT + INDIRECT = RELEVANT; IGNORE = discard.** DIRECT/INDIRECT differences
do not count as errors. Qwen's decisions are never relabelled by Python.
Failed calls are unresolved, not IGNORE. Missing/failed predictions are listed
separately; incomplete runs do not receive misleading headline metrics.

## Output folder

`runs/relevance_eval_<timestamp>_<unique_id>/`

- `predictions/`: fresh model results and runner manifest/summary.
- `evaluation/report.md`: readable scores and confusion matrix.
- `evaluation/metrics.json`: full metrics and coverage.
- `evaluation/per_act.csv`: truth, prediction, result and saved reason per act.
- `evaluation/false_negatives.csv`: relevant documents wrongly ignored.
- `evaluation/false_positives.csv`: ignored truth labels unnecessarily retained.
- `evaluation/unresolved.csv`: technical/missing predictions.
- `evaluation/ground_truth_snapshot.csv`: exact labels used.
- `ground_truth/labels.xlsx`: frozen original labels (CSV for CSV input).
- `prompts_used/document_relevance.txt`: installed prompt saved at launch.
- `pipeline.json`: IDs, prompt fingerprint, inputs and step exit codes.
- `inference.log`: progress and errors.

The terminal prints the output path. Exit code 0 means completed; 2 means
inference/evaluation problems; 1 means setup problems. To name an output folder,
use `--out` with a new folder. Existing folders are refused. `--sheet` selects
another sheet; the default is Labels.

## Verification and interpretation

Thirteen automated tests pass, including fresh inference followed by evaluation,
DIRECT/INDIRECT equivalence, no truth leakage, frozen labels and prompt snapshots,
and technical failure handling. A local integration check confirms that the
existing v7 engine loads this replacement prompt without changing its output
schema or evidence selection.

The live Qwen endpoint is not accessible here. This correction is a candidate;
improved accuracy is not claimed before the new run. Compare the new results
against the previous 5 misses and 31 false positives. Later validate the chosen
prompt on the separate 189 acts. This evidence-rich development sample does not
represent all 14,000 acts.

Optional tests after extraction:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_evaluate_document_screening.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_run_relevance_and_evaluate.py" -v
```

