# EUR-Lex applicability screening

Extract this update into the AI-Experiments project root. It uses the existing
v6.1 dependencies, enriched JSON files and English business anchors.

Run from the project root in PowerShell.

```powershell
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py samples
```

The three samples are Article 2, Article 6 and BAT 17. Qwen receives one pack
per provision with the full provision, available legal context and the
business anchors. The only company evidence is the anchor catalog.

Open `runs/eurlex_provisions_samples_three_way/relevance_export/results.json`.
Each CELEX ID maps its provision locators to `DIRECT`, `INDIRECT` or `IGNORE`.
The labels report matches against the supplied anchors.

Add `--prepare-only` to generate packs offline. Replace `samples` with `pilot`
to run the full article, annex and table inventory in the manifest.
If a request fails, the run stops with the target named in the error.
Rerun the same command to resume. Use a fresh `--output-dir` if inputs change.
