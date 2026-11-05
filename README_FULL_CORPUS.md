# Full stored EUR-Lex: PostgreSQL inventory -> evidence -> resumable Qwen screening

This package extends the v9 evidence pipeline to your existing PostgreSQL schema.
It reads legal_instruments, source_documents, instrument_versions, legal_elements
and recital_sets, using the schema from your earlier working database exporter.
It must be extracted into your existing AI-Experiments project, with the existing
lr_ai/qwen_client.py, lr_ai/catalogue_input.py, .env and config/ files.

## Run from the AI-Experiments terminal, in order

1. Install the patch and dependencies:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\eurlex_full_corpus.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-corpus.txt
```

2. Read ALL stored linked EUR-Lex acts directly from PostgreSQL, with no AI calls:

```powershell
.\.venv\Scripts\python.exe .\tools\export_eurlex_corpus.py --env .\.env --as-of 2026-11-05 --out .\data\eurlex_corpus_20261105
```

DATABASE_URL in .env supplies the PostgreSQL connection. SQLAlchemy-style
postgresql+psycopg URLs are normalized for psycopg. The connection uses a
REPEATABLE READ, READ ONLY transaction. No INSERT, UPDATE, DELETE, schema changes
or HTTP refetches are performed.

3. Inspect these files in the export folder:

- inventory_summary.json: exact live totals, content gaps and type/language counts.
- inventory.csv: one selected record per canonical CELEX ID.
- stored_versions.csv: all available version metadata.
- prefilter_candidates.csv: narrow internal-appointment title matches for review.
- unresolved_text.csv: linked acts with no usable text.
- unlinked_source_documents.json: stored source documents without an instrument link.
- unusable_identifiers.json: rows with missing/unsupported CELEX IDs.
- database_schema.json: table/column metadata for diagnosis.

There are NO automatic title/country relevance exclusions. Preview candidates
continue to Qwen unless you explicitly provide an approved exclusion CSV.

4. Optional: run preparation only to exercise the runner without contacting Qwen:

```powershell
.\.venv\Scripts\python.exe .\tools\screen_eurlex_corpus.py --input .\data\eurlex_corpus_20261105 --out .\runs\eurlex_preparation_check --prepare-only
```

5. Start the complete AI screening:

```powershell
.\.venv\Scripts\python.exe .\tools\screen_eurlex_corpus.py --input .\data\eurlex_corpus_20261105 --out .\runs\eurlex_full_v10 --workers 4
```

Use a new export folder for a new database snapshot. Keep the snapshot fixed while
screening it. To continue the same inference run after stopping or network errors:

```powershell
.\.venv\Scripts\python.exe .\tools\screen_eurlex_corpus.py --input .\data\eurlex_corpus_20261105 --out .\runs\eurlex_full_v10 --workers 4 --resume
```

Successful saved acts are reused by the runner; failed acts are retried. Do not
use the preparation-only output folder as the actual inference folder.
Settings, code/profile/prompt fingerprints and selected snapshot must match for
resume. A running process holds the runner's .writer.lock; do not start two
writers for the same run folder.

## Scope: what "all stored acts" means

The export reads every non-deleted EUR_LEX source linked through legal_instruments.
It inventories all stored versions, then selects ONE version per canonical CELEX.
Duplicate instrument rows for the same ID are not classified multiple times.
Ranking prefers usable stored text, non-future versions, non-rejected quality,
English then German then another stored language, preferred/current status and
recent version/fetch dates. The selected version is recorded per ID.

There is no requirement for recitals, chapters, minimum articles, annexes, an
English version, an in-force flag, a certain year or a regulation/directive type.
A version whose effective date is future is not silently discarded. If only
future/rejected/foreign-language data exists, it remains visible and can be
screened with its warnings rather than labelled IGNORE by code.

Unlinked documents and invalid IDs are explicitly reported; a canonical act
cannot be manufactured from a missing link/identifier. Soft-deleted source
documents are outside this active snapshot. These limits appear in the inventory;
the package does not claim that every stored source-document row is a distinct act.

## Evidence: use whatever is actually available

Every selected version exports ALL its legal_elements rows, all element types,
and available recital sets. Multiple recital sets attached to the SAME version
are merged without mixing versions. Titles, dates, source identity and in-force
metadata are retained. Recognized stored full_text/body_text/text_content/
content_text/plain_text string fields are forwarded when present on the source
document or version. Missing columns in the optional document-text list resolve
to null via JSON access rather than causing a SQL column error.

The v9 preparation combines substantive articles, annexes, recitals and orphan/
unstructured paragraphs. It recovers child text hidden by malformed aggregate
parents, removes only reference-only fragments and exact duplicate passages,
and preserves raw records plus an omission audit.

Small acts: ALL prepared passages are included if the complete request fits.
No recitals? Use the body and annexes. No chapters/articles? Use available text.
Only recitals? They can still supply screening evidence.
Long acts: position-based coverage and the real tokenizer select what fits the
34000 input budget, with 6000 output tokens and context 40960. Default
--max-chunks 0 means no fixed passage-count cap.

If no legal body/recital/fallback text exists, the act remains unresolved and is
not silently labelled IGNORE. Titles/metadata alone are not proof of irrelevance.
The exporter does not fetch fresh EUR-Lex HTML/PDF or fix missing source text;
the gap list identifies where a separate refetch/parser repair is necessary.

The full profile and v8 prompts remain in use. An initial IGNORE receives one
additional Qwen review, with opening context and priority for unseen passages.
Every decision has exactly reason, anchor_ids and level. Python does not assign
relevance based on country names, keywords or structural counts.

## Export and inference files

Export:
- corpus_manifest.json: complete snapshot, canonical IDs and source file hashes.
- acts/<CELEX>.json: raw selected act/version, all available element/recital data.
- prepared_evidence/<CELEX>.json: all prepared chunks and preparation audit.
- export_progress.json: progress; complete manifest is written only on success.
- inventory files listed above.

Inference:
- predictions/<CELEX>.json: first/final Qwen decisions, actual per-round evidence,
  token counts, newly supplied review sources and status/coverage.
- predictions/manifest.json and summary.json: settings and run totals.
- corpus_summary.json: label counts, reviews, missing and unresolved IDs.
- selected_ids.txt: avoids Windows command-length limits for thousands of acts.
- prefilter_excluded.json: any explicitly approved exclusions.
- corpus_run.json: snapshot and settings for resume.

The adapter reads one act file per job instead of loading the whole corpus into
one huge JSON array. Source hashes detect edits to an exported record. Do not
edit the act files/manifest during inference. Store the snapshot on a disk with
space for raw source text, prepared evidence and predictions.

## Optional reviewed prefilter

Only use explicit reviewed exclusions. Create a CSV:

```csv
celex,reason
32026D9999,Example only - replace with a real reviewed corpus ID and reason
```

Pass its real path with --exclude-approved. Unknown IDs, duplicates and missing
reasons fail. The CSV above is an example, NOT a supplied exclusion list.
Country mentions, another Member State, foreign trade, sanctions, funding or
non-binding form never cause automatic exclusions. Initial default is zero.

## Optional evaluation of your labelled subset

The full corpus has no ground-truth labels, so precision/recall cannot be computed
for all of it. To evaluate the existing labelled subset after screening, add:

```powershell
.\.venv\Scripts\python.exe .\tools\screen_eurlex_corpus.py --input .\data\eurlex_corpus_20261105 --out .\runs\eurlex_full_v10 --workers 4 --resume --labels .\data\simple_labels_with_astro_review.xlsx
```

Scores are explicitly for that labelled subset, not the entire corpus.
DIRECT + INDIRECT = RELEVANT. The Label column stays truth.
If labelled IDs were excluded by the primary filter, this command refuses to
report misleading reduced-denominator scores; audit those exclusions separately.
Labelled evaluations go into a new labelled_evaluation_* folder so a later
evaluation does not overwrite or collide with an earlier one.

## Verification and practical limits

44 offline tests pass, including read-only query contracts against fixtures,
all-shape/all-type export, language fallback, missing text, CELEX identifier
safety, snapshot tampering, preview behaviour, approved exclusions and resume
workflow, plus the existing engine/evidence/evaluation tests. The SQL is grounded
in your earlier database exporter, but a LIVE connection to your office database
was not available here. No live database export or Qwen inference was run.

For the new tests only:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_eurlex_corpus.py" -v
```

If a database/schema error occurs, inspect database_schema.json and the partial
export. No AI run is automatically started by a failed export. The export itself
uses a fresh output folder and is not resumable across database snapshots;
inference on a completed snapshot is resumable.

The working user's existing Qwen client dependencies and company network access
are still required. Higher corpus volume and longer texts may take substantial
time; preserve the snapshot and resume inference as needed. No production recall
guarantee is made from the repeatedly tuned 200-act sample.

