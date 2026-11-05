# v9 evidence extraction -> Qwen screening -> evaluation

Install into your existing AI-Experiments project. Your existing Qwen client,
catalogue reader, .env and config YAML files remain required.

## Run everything in order (PowerShell, from AI-Experiments)

Keep the saved labelled workbook in data/simple_labels_with_astro_review.xlsx.

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\document_screening_v9_evidence.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements_evaluation.txt
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx
```

No separate manual extraction or calculation is required.

The command finds your existing complete-act export using
data/dev400_new_v2_insufficient.json -> eligible_cache/eligible_acts.json.
If the input has moved, supply --input:

```powershell
.\.venv\Scripts\python.exe .\tools\run_relevance_and_evaluate.py --labels .\data\simple_labels_with_astro_review.xlsx --input "C:\path\to\eligible_acts.json"
```

## What is fetched

The extraction step reads complete act records by the workbook IDs from your
existing local structured JSON/ZIP export or existing catalogue directory.
It does NOT connect to PostgreSQL or refresh EUR-Lex. A fresh DB export would
require your actual DB exporter/schema; this package does not guess that schema.

Your existing 389-act export can supply the same 200 labelled IDs. Missing IDs
stop extraction before inference. Missing usable text is a technical failure,
never an IGNORE label. No acts are removed based on business relevance.

## Evidence improvements

- Reference-only fragments such as "Article 16 Article 18" are omitted from the
  model candidates and recorded in the preparation audit. They are not laws
  reconstructed or rewritten by Python.
- Exact repeated passages are deduplicated, regardless of source headings.
- Substantive child paragraphs are recovered if a malformed aggregate article
  previously hid them.
- Supplied full_text, body_text or text_content string fields on the act,
  document or version can supply fallback passages. Missing text is not invented.
- Selection covers opening, middle and end. It prioritizes body text (three
  body passages per recital/annex passage) without business-keyword matching.
- The old 24-piece cap and per-group 32-piece ceiling are removed by default.
  --max-chunks 0 means use the actual tokenizer budget. A positive cap is optional.
- Candidate passages grow until the full request fits the configured input
  budget, including prompt, full profile, title, outline and metadata.
- The IGNORE review keeps four opening/context passages and prioritizes previously
  unseen passages before repeating the remainder of the first pack. If all
  prepared text already fitted, it repeats the complete pack.
- Both actual model packs, token counts, source IDs and coverage are saved.

This is improved preparation of available text, not a replacement for a reliable
source parser. It cannot recover Article 13 or any other provision if absent from
the original export and its fallback text. Legitimate forms/annexes are not
blanket-deleted. Apart from narrow reference fragments, no short passage is
removed solely because it is short.

## Classification and evaluation

The v8 first-pass and IGNORE-review prompts are unchanged. Company-profile
clarifications, roles, exclusions and source YAML files are unchanged from v8.
Qwen alone decides reason, anchor_ids and level. All DIRECT/INDIRECT decisions
finish after one call; every initial IGNORE receives one additional decision.
No third relevance pass or semantic Python override is added. Request/JSON
failures remain unresolved.

The Label column is ground truth. DIRECT and INDIRECT both count as RELEVANT.
Review/LLM columns are ignored. Only IDs leave the labelling workbook for
inference. Labels are frozen for evaluation and never passed to Qwen.

## New output folder

runs/relevance_eval_<timestamp>_<unique>/

- extracted_input/selected_acts.json: complete selected source records, retaining
  their original elements for traceability.
- extracted_input/prepared_evidence/<ID>.json: all prepared chunks, outline,
  quality issues and omitted-fragment audit BEFORE model token selection.
- extracted_input/extraction.json: selected IDs and extraction counts/issues.
- predictions/<ID>.json: final result; rounds[0].evidence is the first pack;
  rounds[1].evidence is the review pack where a review completed;
  rounds[1].new_source_ids identifies the newly supplied passages.
- evaluation_first_pass/: initial precision, recall, accuracy, F1 and error lists.
- evaluation/: final metrics, false negatives, false positives and unresolved acts.
- comparison.json and review_changes.csv: recovered relevant acts and added
  false positives, including their IDs and first/final reasons.
- ground_truth/: saved labels; prompts_used/: actual prompts at launch.
- pipeline.json and inference.log: workflow status and progress.

Prepared evidence is not necessarily the exact model pack: the tokenizer selects
what fits. Read the per-round evidence to see exactly what Qwen received.
Coverage describes prepared stored chunks, not proof of completeness against
the official publication. The model response's decision still has only three fields.

Defaults: assessment date 2026-11-04 (same development experiment), input budget
34000, context limit 40960, 6000 output tokens with thinking enabled, four workers.
Input budget includes all input content, not only legal passages.

## Separate extraction, if wanted

Create a text file with one CELEX ID per line, then run:

```powershell
.\.venv\Scripts\python.exe .\tools\extract_evidence_inputs.py --input "C:\path\to\eligible_acts.json" --ids-file .\data\selected_ids.txt --out .\data\evidence_v9 --as-of 2026-11-04
```

That command does not contact Qwen. Output folders must be new.

## Verification and limits

38 offline tests pass: evidence-fragment audit, child-paragraph recovery,
full-text fallback, >32-chunk coverage, real-budget selection, additional review
evidence, ID extraction/missing-ID failure, no truth leakage, review routing,
failure handling and both evaluations. Source extraction preserves original
element content. No hardcoded development-act relevance corrections exist.

Tests with old "same evidence in both passes" expectations were updated because
v9 deliberately changes review context. To run just the evidence tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_evidence_v9.py" -v
```

Live database, full office export and Qwen inference were not available here.
Run on the same 200 labelled IDs first and compare against v8. Increasing evidence
may increase runtime and can change precision as well as recall. No 95% recall
claim is made before measurement. Validate the chosen setup on unseen labelled
acts afterward.

