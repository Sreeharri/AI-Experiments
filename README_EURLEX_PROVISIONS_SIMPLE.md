# EUR-Lex provision relevance, version 2

Copy the files of this patch into the root of `AI-Experiments` and replace the
matching files. Then delete `resume_eurlex_provision_corpus.py`: its fixes
(unique target names, splitting of long targets) are now part of the normal
runner. The patch uses the same `.env`, Python environment, anchor catalog and
enriched files as before. Old run folders stay valid and untouched; version 2
writes to new folders.

## What changed

| Area | Before | Now |
|---|---|---|
| Broken input | Every stored article, annex and table went to Qwen | Targets that are not provisions are reported as "not assessable" and are not sent |
| Company profile | 58 business anchors, placed after the target | Roles, "not roles", indirect paths, 17 negative anchors and 58 business anchors, placed first and identical in every request |
| Question to Qwen | Prove the company link, otherwise IGNORE | Keep the provision unless one named ignore reason applies |
| Target text | Sent twice | Sent once, with passage markers such as `[P3]` |
| Table context | The whole parent section, sometimes a whole annex | Only the text just before the table |
| Annex and table | Judged without the provision that refers to them | That provision is added |
| References | Whole cited articles, no limit | Limited in size; the cited paragraph is kept first |
| Long targets | Split only after the server refused the request | Split before the call, along paragraphs, sections and table rows |

## 1. Check prefix caching (once)

```powershell
& .\.venv\Scripts\python.exe .\tools\check_prefix_cache.py
```

The company profile is about 48,000 characters. vLLM cannot store it on the
server for later calls: every request still carries the full text. What the
server can do is reuse its earlier work when a request starts with exactly the
same text. The script sends four tiny requests and tells you whether the
repeated ones are faster. Nothing in the screening depends on the result. With
caching the run is only faster.

## 2. Rerun the 100 reviewed provisions

```powershell
$manifest = ".\runs\eurlex_full_v10\provision_manifest.json"
& .\.venv\Scripts\python.exe .\screen_eurlex_provisions_simple.py pilot `
    --manifest $manifest --enriched-dir .\output_db_corpus `
    --targets-file .\config\eurlex_provision_review100.json `
    --document-run .\runs\eurlex_full_v10 `
    --output-dir .\runs\eurlex_provisions_review100_v2
```

`config/eurlex_provision_review100.json` lists the 100 provisions of the review
workbook. A target name that no longer exists is printed, listed under
`targets_not_found` in the summary and left out. Add `--prepare-only` to save
the evidence without calling Qwen. Rerun the same command to resume.

## 3. Compare with the labels

```powershell
& .\.venv\Scripts\python.exe .\tools\compare_provision_labels.py `
    --labels .\EUR-Lex_100_Provision_Review.xlsx `
    --run .\runs\eurlex_provisions_review100_v2 --baseline .\runs\eurlex_provisions_full
```

The first line to read is "Removed by Qwen but labelled relevant". That share
should stay at or below 5%. The script also writes `label_comparison.csv` with
one row per provision. Use hand-checked labels: labels from another AI only
show where two models disagree.

## 4. Full corpus

```powershell
& .\.venv\Scripts\python.exe .\run_eurlex_provision_corpus.py
```

Results go to `runs/eurlex_provisions_full_v2`. The document decisions are read
from the manifest's folder (`relevance_export/direct.json` and `indirect.json`)
when they are there; set `--document-run` to use another folder. Run the same
command again to resume. Run steps 2 and 3 first.

## What Qwen receives

```text
System message   instructions + company profile          the same in every request
User message     act, target, position, references       changes per target
                 TASK line                               last: "classify" or "confirmation pass"
```

- **act**: CELEX, title and, with `--document-run`, why the act was kept.
- **target**: the provision, once. Markers `[P1]`, `[P2]`, … number its text.
- **position**: the text before a table, the annex scope, and the provision
  that refers to an annex or table.
- **references**: cited provisions, at most about 5,000 characters. A long one
  is shortened to the cited paragraph and marked `excerpt`.
- **missing_references**, **context_not_included**: names of texts that could
  not be supplied or did not fit.
- **source_notes**: known problems, such as text that is cut off.

A target above 12,000 characters is split into parts. Each part is classified
with the opening words of the whole target. The target takes the highest level
of its parts, and the audit keeps every part's result.

## How Qwen decides

1. It says what the provision does and whom it addresses.
2. It returns IGNORE only for one of three reasons:
   `FORMAL` (no rule of its own), `OUTSIDE_BUSINESS` (falls under a negative
   anchor or "not roles", and no business anchor covers it) or `NO_PATH` (binds
   only other actors, with no indirect path to the company).
3. Otherwise the provision is DIRECT or INDIRECT. INDIRECT names one of the
   four indirect paths.
4. A company detail the catalog does not hold (equipment, capacity, site) never
   lowers the level. It is written to `open_points` for the department.
5. In doubt between IGNORE and relevant, the answer is INDIRECT.

Every IGNORE gets a confirmation pass that can keep it or change it. The code
never changes a level. It rejects a reply only when its fields contradict each
other, and asks once for a correction.

## Not assessable targets

These are saved and listed, and are not sent to Qwen:

| Code | Meaning |
|---|---|
| `REFERENCE_ONLY` | Only names other provisions, such as a cell of a correlation table |
| `TABLE_OF_CONTENTS` | A table-of-contents line with a page number |
| `IMAGE_ONLY` | Picture placeholders without usable text |
| `NO_TEXT` | Nothing besides its own label |
| `PDF_PAGE_TABLE` | Unverified page table from a PDF; its text is classified with the annex |

Text that starts or ends in the middle of a sentence is still sent, with a
warning to Qwen, and is listed under `targets_with_incomplete_text`.

## Results

- `relevance_export/results.json`: level, reason and area per classified target.
- `audit/run_summary.json`: counts, plus
  - `not_assessable` and `not_assessable_targets`
  - `ignore_reasons`, `negative_anchors`, `indirect_paths`: use these to sample
    and check each reason
  - `ignore_reviews`: confirmed and changed IGNORE decisions
  - `acts_all_ignore`: acts in which every provision was ignored, as
    candidates for a second look at document level
  - `requests`: number of calls, seconds and, when the server reports them,
    cached tokens
- `audit/<CELEX>/…json`: the full evidence, every request part and Qwen's
  detailed answer (`details`: effect, ignore reason, anchors, open points).
- Corpus runs also write `run_summary.json` and `not_assessable.json` in the
  run folder.

## Earlier commands

`samples` and `holdout50` still work and write to
`runs/eurlex_provisions_samples_v2` and `runs/eurlex_provisions_holdout50_v2`.
`--celex` with `--target` still selects single targets of one act.

## Tests

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_eurlex_provisions_simple.py
```
