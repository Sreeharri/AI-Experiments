# EUR-Lex provision relevance test

Extract this patch into the root of your existing AI-Experiments setup and
replace the matching provision-test files. It uses the same .env, Python
environment, anchor catalog and version-pinned enrichment files in output_db.

## Run the three original samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py samples
```

Open `runs/eurlex_provisions_samples_final/relevance_export/results.json`.
Article 2 includes Article 17. Article 6 includes Annex II and the directive's
scope and definitions. BAT 17 includes the technique table, annex scope and
the footnote's cited Section 1.5.1. The industrial-emissions anchor includes
the supplied company fact about cement manufacture, with kiln operation
left unspecified.

Each target receives one Qwen request and produces one label (DIRECT,
INDIRECT or IGNORE). Use the audit JSON to see the evidence pack and the
endpoint's token usage when returned.

## Run the next 50 after reviewing the samples

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py holdout50
```

Open `runs/eurlex_provisions_holdout50_final/relevance_export/results.json`.
The manifest selects 18 targets from the regulation, 16 from the directive
and 16 from the decision. It mixes articles, annexes, tables and BAT items.
Targets already supplied in full in the original three packs were excluded.
The selection also limits evidence size for this round.

Add `--prepare-only` to either command to write evidence packs without
calling Qwen. Rerun the same command to resume an interrupted run. If you
change inputs after a run, set `--output-dir` to a fresh folder.
