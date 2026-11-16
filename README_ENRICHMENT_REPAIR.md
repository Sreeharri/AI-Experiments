# Repairing the enriched files (provision boundaries)

The first full provision run showed three problems in the enriched files.
This repair fixes them without a model and without touching the finished run.

| Problem | What you saw | Repair |
|---|---|---|
| A sentence such as "Annex II to Directive ... is amended" was stored as a new annex or article | "Article 1" with no text, the same label several times, fragments like "Article 17 of Directive 89/391/EEC." | The text is glued back onto the article or annex it belongs to |
| Text stored as letter codes (`/C67/C79/C85` = "COU") | Acts with no articles (Landfill Directive 1999/31) | The codes are turned back into text |
| The whole act stored as one block of text | Acts with no articles (Regulation 883/2004) | The text is cut into articles and annexes at their headings |

Not repaired: acts where only one page is stored (they are listed to fetch
again from EUR-Lex), scanned images, and old tables stored as `>TABLE>`.

## The four commands

Run them in `AI-Experiments`, one after the other.

**1. Find the acts that need the repair** (a few minutes, no database, no Qwen)

```powershell
& .\.venv\Scripts\python.exe .\tools\repair_enrichment_gaps.py find
```

It writes `runs\eurlex_repair_v1\repair_manifest.json` and prints how many
acts are affected.

**2. Enrich these acts again into a new folder** (uses the database)

```powershell
& .\.venv\Scripts\python.exe .\enrich_eurlex_corpus.py `
    --manifest .\runs\eurlex_repair_v1\repair_manifest.json --output-dir .\output_db_corpus_repair
```

**3. Check the result**

```powershell
& .\.venv\Scripts\python.exe .\tools\repair_enrichment_gaps.py check
```

It prints how many acts were repaired and writes
`runs\eurlex_repair_v1\repair_report.json`. Look at two lists in it:

- `fetch_again_from_eurlex`: only a page of these acts is stored.
- `false_headings_left`: should be empty.

**4. Screen the repaired acts into a new run folder**

```powershell
& .\.venv\Scripts\python.exe .\run_provisions_overnight.py `
    --manifest .\runs\eurlex_repair_v1\screen_manifest.json --enriched-dir .\output_db_corpus_repair `
    --no-document-hint --workers 4 --output-dir .\runs\eurlex_provisions_repair_v1
```

The old folders `output_db_corpus` and `runs\eurlex_provisions_baseline_v3_2`
are not changed. For the repaired acts, the results in
`runs\eurlex_provisions_repair_v1` replace the old ones.

## Good to know

- The two smoke-test acts of the screening runner are always part of the
  repair manifest. They are screened again; that is a handful of targets.
- Recovered articles carry the note "Check provision boundaries and source
  layout", because the cut was made at headings found in running text.
- In some old PDF texts words are stored without spaces
  ("ForthepurposesofthisDirective"). The repair does not change that.
- Every repaired act records what was merged under
  `document.provision_repair` in its enriched file.
