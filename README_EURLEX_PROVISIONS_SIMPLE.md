# Simple EUR-Lex provision filter

Copy this patch into the project root of `AI-Experiments` with v6.1 installed.
Run from the project folder in PowerShell.

```powershell
.\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py samples
```

This assesses all of Article 2, all of Article 6, and BAT 17. Each target
gets a single model request with the complete provision, the parent act's
document-level result, direct references, business facts and business anchors.
The BAT 17 pack includes the technique table and its footnote. The annex
scope is included so the model can assess which activities are covered.
External references without supplied text are marked unavailable.
The document-level result is null if the act has no match in the exports.

To inspect the packs without contacting Qwen, add `--prepare-only`.
The prepared packs are under `runs/eurlex_provisions_samples_simple_prepared/audit`.
Live outputs are under `runs/eurlex_provisions_samples_simple`.
Start with `audit/run_summary.json`. Completed entries are exported into
`relevance_export/direct.json`, `indirect.json` and `ignore.json`. Each entry
contains level, reason and area. Unresolved evidence or a model context limit
leaves the result in the audit with NEEDS_REVIEW.

BAT 17 uses the supplied fact that Max Bögl manufactures cement and other
construction products. The scenario leaves company clinker-kiln operation
and capacity unspecified. Article 2 and Article 6 retain their synthetic
scenarios. Edit the scenario file or use `--scenarios` to supply different
case facts.

To assess the full set of articles, annexes and tables from the pilot
manifest, replace `samples` with `pilot`. Rerun the same command to resume
an interrupted run. Use a new `--output-dir` when inputs or the model change.
