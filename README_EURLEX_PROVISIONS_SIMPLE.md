# EUR-Lex provision relevance test

Extract this patch into the root of your existing AI-Experiments setup and
replace the matching provision-test files. It uses the same .env, Python
environment, anchor catalog and version-pinned enrichment files in output_db.

## Run the three original samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py samples
```

Open `runs/eurlex_provisions_samples_validated/relevance_export/results.json`.
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

Each target normally receives one request. If the reply fails validation,
the runner sends the validation error with one correction request. If the
target still fails, its audit records both replies and the runner continues
to the next target. Check targets_failed and failures in audit/run_summary.json.
Rerunning the same command in the same output folder retries failed targets and
reuses completed results. The validated folders start a fresh set of results.

## Run the next 50 after reviewing the samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py holdout50
```

Open `runs/eurlex_provisions_holdout50_validated/relevance_export/results.json`.
The manifest selects 18 targets from the regulation, 16 from the directive
and 16 from the decision. It mixes articles, annexes, tables and BAT items.
Targets already supplied in full in the original three packs were excluded.
The selection also limits evidence size for this round.

Add `--prepare-only` to either command to write evidence packs without
calling Qwen. Rerun the same command to resume an interrupted run. If you
change inputs after a run, set `--output-dir` to a fresh folder.
