# v8: document screening + one LLM review of every IGNORE + evaluation

Install into your existing **AI-Experiments** project. This patch includes the
updated engine, runner, prompts, evaluation workflow and tests. Your existing
Qwen client, catalogue reader, .env, evidence exports and business YAML files
remain required. This is not a standalone replacement for the whole project.

## Commands in order (PowerShell, from AI-Experiments)

Keep the completed workbook at `data/simple_labels_with_astro_review.xlsx`.

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\document_screening_v8.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements_evaluation.txt
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx
```

Save your Excel edits before starting. Only the **Label** column is ground truth.
The script reads the saved workbook, selects its labelled IDs and freezes a copy
for evaluation. LLM/Astro columns are not overrides and are never sent to Qwen.
Evidence continuation rows do not count as additional acts.

The original cache is resolved from `data/dev400_new_v2_insufficient.json` and
its `eligible_cache` field. If the input has moved, supply its current path:

```powershell
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx --input "C:\path\to\eligible_acts.json"
```

## Filtering changes

1. New first-pass prompt distinguishes overall document relevance from proof of
   current applicability. Company rights, purchaser/customer connections and
   conditional corporate transactions are considered within the profile.
2. Every completed first-pass IGNORE is sent to Qwen exactly once more. DIRECT
   and INDIRECT receive no additional relevance call. The second call receives
   the full company profile, title, outline, metadata and the same selected
   evidence passages, without the first reason or hidden reasoning.
3. The second Qwen decision is final for reviewed acts. It may remain IGNORE.
   There is no third relevance pass, vote, keyword gate or Python relevance override.
4. The effective compact profile clarifies corporate taxes/capital transactions,
   relevant fleet purchases and owner rights, electricity customer data access,
   and energy/material supply restrictions. The source YAML files, existing IDs,
   scopes and exclusions are preserved. No new manufacturer/importer role is added.
5. Stored legal-status fields are forwarded from input where available. The model
   is told to distinguish repealing another act from being repealed. Unknown
   status stays unknown; the runner does not look up or invent legal status.

The final `decision` still contains **only reason, anchor_ids and level**.
The server enforces the same JSON schema and anchor vocabulary. Python handles
request/format failures, routes first-pass IGNORE to its review, and saves results;
it does not decide whether the act is relevant.

## Evidence and limits

The existing position-based evidence selection is retained, with no business-word
or article-heading matching added. Both prompts must fit the token budget before
the first request: passages are removed from the tail if needed, as previously.
This reserves room for the longer review prompt so it receives the same evidence.
Long acts remain sampled rather than guaranteed complete. Context availability
is not a guarantee that every passage has been supplied.

Defaults stay at 4 workers, up to 24 chunks, input budget 34000, context limit
40960 and 6000 output tokens with thinking enabled. The assessment date stays
**2026-11-04** to match your labelled experiment. Change it deliberately with
`--as-of YYYY-MM-DD` when evaluating another assessment date.

For N acts with K first-pass IGNOREs, there are N+K logical classification calls.
The existing client's network retries are separate from the one relevance review.
If a first call or review fails or gets cut off, the final result is unresolved;
the code never silently treats a failed review as a verified IGNORE.

## Outputs and evaluation

A fresh `runs/relevance_eval_<timestamp>_<unique>/` folder contains:

- `predictions/`: final per-act results, both model rounds and the actual evidence.
- `first_pass_predictions/`: saved first decisions for separate evaluation.
- `evaluation_first_pass/`: first-pass precision, recall, accuracy, F1 and error lists.
- `evaluation/`: final scores, false negatives, false positives and unresolved acts.
- `comparison.json`: both sets of metrics, relevant acts recovered by review and
  additional false positives introduced by review, with their IDs.
- `review_changes.csv`: every act's first/final level, result, reasons and review state.
- `ground_truth/`: frozen labels; `prompts_used/`: both installed prompts.
- `inference.log` and `pipeline.json`: progress, prompt fingerprints and step results.

**DIRECT + INDIRECT = RELEVANT.** DIRECT/INDIRECT differences are not errors.
Precision = TP/(TP+FP), recall = TP/(TP+FN), accuracy = (TP+TN)/N,
F1 = 2TP/(2TP+FP+FN). Extra metrics and 95% Wilson intervals are also saved.
Empty denominators are null/N/A. An incomplete run exposes its unresolved acts
and does not receive headline scores over a silently smaller sample.

The terminal first prints the initial evaluation and then the final evaluation.
It also prints the recovered-relevant and added-false-positive counts. Use
`comparison.json` to assess the cost/benefit of the review, not just final recall.
The process returns 0 for completion, 2 for inference/evaluation issues, and 1 for
setup issues. An explicit `--out` must name a new folder.

The runner manifest identifies `document_screening_v8` and fingerprints both
prompts, the source profile, the effective profile and code. Old run directories
cannot be resumed with changed settings. The labelled workbook is never edited.

## Optional first-pass-only experiment

```powershell
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx --no-ignore-review
```

This disables review. The normal command needs no flag: IGNORE review is on.
The standard model-only runner `tools/document_relevance.py` also enables review
by default for unlabelled production inputs. Evaluation requires labels.

## Verification

The 33 packaged tests pass locally. They cover three-field output, real model
decisions preserved, one call for positive levels, exactly two for IGNORE,
confirmed IGNORE without a third call, review failures/cut-offs, identical review
evidence, status forwarding without guessing, plain-text/missing headings,
not_roles as a string, profile exclusions preserved, ground-truth isolation,
binary metrics, unresolved results and first/final comparison.

Run the test files independently from your existing older tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_document_screening_v8.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_compare_screening_passes.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_run_relevance_and_evaluate.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_evaluate_document_screening.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_document_screening_v7.py" -v
```

Offline tests use explicit mock clients; none of their predictions are packaged
as real run results. Live Qwen inference was not run here and needs your company
endpoint. There are no hardcoded corrections for particular development-act IDs.
No label was changed. A recall increase is a hypothesis until you run v8.
These 200 remain the tuning set; validate the chosen configuration on the separate
labelled 189 before estimating performance on the full document population.
