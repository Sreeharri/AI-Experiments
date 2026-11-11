# EUR-Lex provision relevance test

Extract this patch into the root of your existing AI-Experiments setup and
replace the matching provision-test files. It uses the same .env, Python
environment, anchor catalog and version-pinned enrichment files in output_db.

## Run the three original samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py samples
```

Open `runs/eurlex_provisions_samples_context_safe/relevance_export/results.json`.
Article 2 includes Article 17. Article 6 includes Annex II and the directive's
scope and definitions. BAT 17 includes the technique table, annex scope and
the footnote's cited Section 1.5.1. The industrial-emissions anchor includes
the supplied company fact about cement manufacture, with kiln operation
left unspecified.

The export saves level (DIRECT, INDIRECT or IGNORE), reason and area.
IGNORE uses an empty area array. The prompt asks Qwen to name the target's
effect and actor, and identify the company evidence or missing link.
The runner supplies numbered passages from the target body or table cells.
Qwen selects a passage ID and business anchor source IDs. The runner derives
area from those source IDs and saves the corresponding source text in the
audit. Review the reason to verify the interpretation.

Each target receives an initial assessment. Every initial IGNORE receives
one confirmation pass. The confirmation can keep IGNORE or change the
decision to DIRECT or INDIRECT. The audit records the initial decision, and
the export uses the confirmed decision. Check ignore_reviews in
audit/run_summary.json for confirmed, changed and pending counts.

If a reply fails validation,
the runner sends the validation error with one correction request. If the
target still fails, its audit records both replies and the runner continues
to the next target. Check targets_failed and failures in audit/run_summary.json.
Rerunning the same command in the same output folder retries failed targets and
reuses completed results. The context-safe folders start a fresh set of results.

## Run the next 50 after reviewing the samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py holdout50
```

Open `runs/eurlex_provisions_holdout50_context_safe/relevance_export/results.json`.
The manifest selects 18 targets from the regulation, 16 from the directive
and 16 from the decision. It mixes articles, annexes, tables and BAT items.
Targets already supplied in full in the original three packs were excluded.
The selection also limits evidence size for this round.

Add `--prepare-only` to either command to write evidence packs without
calling Qwen. Rerun the same command to resume an interrupted run. If you
change inputs after a run, set `--output-dir` to a fresh folder.


## Prepare the selected-act manifest

```powershell
& .\.venv\Scripts\python.exe .\prepare_eurlex_provision_manifest.py --run .\runs\eurlex_full_v10 --out .\runs\eurlex_full_v10\provision_manifest.json
```

This reads relevance_export/direct.json and indirect.json and obtains each
exact version ID from predictions/<CELEX>.json. Compare the reported counts
with the document run. Manifest preparation uses saved files.

## Check the two BAT Decision targets

Use the enriched BAT Decision in output_db. Run just the two targets
that previously exceeded the context window.

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py pilot --celex 32013D0163 --target "Article 1" "Annex 1" --output-dir .\runs\eurlex_bat_context_fix
```

Check audit/run_summary.json for two exports and zero failures. The two
decisions are in relevance_export/results.json under that run folder.
Article 1 receives the annex scope and section outline if the complete
reference is oversized. Annex 1 is screened in groups of whole sections
with shared scope and definitions. The annex decision takes the highest
level across all groups. The audit records each group and its decision.
An initial IGNORE still receives a confirmation pass.

## Enrich and screen a corpus batch

Run these from AI-Experiments after creating the manifest. This example
selects its first three acts. The enrichment command reads PostgreSQL
through .env. The provision command reads the enriched JSON. Use a distinct
screening output folder for each different batch.

```powershell
$manifest = ".\runs\eurlex_full_v10\provision_manifest.json"
$batch = @((Get-Content -Raw $manifest | ConvertFrom-Json).PSObject.Properties.Name | Select-Object -First 3)
& .\.venv\Scripts\python.exe .\enrich_eurlex.py run --source postgres --env-file .\.env --manifest $manifest --celex $batch --output-dir .\output_db_corpus
if ($LASTEXITCODE -ne 0) { throw "Resolve the enrichment error before screening this batch." }
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py pilot --manifest $manifest --celex $batch --enriched-dir .\output_db_corpus --output-dir .\runs\eurlex_provisions_corpus_batch01
```

The existing enricher requires English source versions and verified XHTML.
It stops at the first enrichment error in a batch. Check the selected
version language and source coverage before scheduling the whole corpus.
