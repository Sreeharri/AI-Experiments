# EUR-Lex provision relevance, version 3

Copy the files of this patch into the root of `AI-Experiments` and replace the
matching files. If `resume_eurlex_provision_corpus.py` is still there, delete
it. The patch uses the same `.env`, Python environment, anchor catalog and
enriched files as before. Older run folders stay valid and untouched.

## What changed since version 2

Version 2 kept almost everything and called it DIRECT. Qwen named the right
actor, then justified DIRECT with a broad company role. Version 3 keeps the
evidence pack of version 2 and changes how the decision is made.

| Area | Version 2 | Version 3 |
|---|---|---|
| Decision | Qwen chose the level and wrote a reason afterwards | Qwen answers separate questions; the level follows from the answers |
| Actor | Mentioned in one sentence | Its own answer: who has to act |
| Negative anchors | A reason Qwen could pick, never used | Offered in the same list as the business anchors; Qwen picks the closest entry for the subject |
| Company role | Any matching role name was enough | The role counts only if the actor is that role for this subject |
| Missing company fact | `open_points`, never used | An `open_question` turns DIRECT into UNSURE |
| Document hint | Always sent with `--document-run` | Still optional; test with and without |
| Thinking | Off | Off by default; `--thinking on` to compare |

## How the level follows

Qwen returns `actor`, `subject`, `content`, `topic`, `company_role`,
`indirect_path`, `open_question` and `reason`. It never returns a level.

| Qwen's answers | Level | Basis in the audit |
|---|---|---|
| content is FORMAL | IGNORE | `FORMAL` |
| topic is a negative anchor | IGNORE | `OUTSIDE_BUSINESS` |
| no catalog entry fits, no role, no path | IGNORE | `NO_MATCH` |
| business anchor and a company role | DIRECT | `ROLE` |
| business anchor, a company role and an open question | UNSURE | `OPEN_QUESTION` |
| business anchor, no role, an indirect path | INDIRECT | `PATH` |
| business anchor, no role, no path | IGNORE | `NO_PATH` |

UNSURE means: the provision would apply to a Max Bögl role, but only if a
company fact that the catalog does not hold is true. It is kept for the
department, and its reason ends with "To check: …". DIRECT, INDIRECT and
UNSURE are kept. IGNORE is removed. Every IGNORE gets a confirmation pass in
which Qwen answers all questions again.

## 1. Run the 100 reviewed provisions in three variants

One run cannot tell which change helps, so run the same 100 three times:

```powershell
$py = ".\.venv\Scripts\python.exe"
$common = @("pilot", "--manifest", ".\runs\eurlex_full_v10\provision_manifest.json",
            "--enriched-dir", ".\output_db_corpus",
            "--targets-file", ".\config\eurlex_provision_review100.json")

# A: no document hint, no thinking
& $py .\screen_eurlex_provisions_simple.py @common --output-dir .\runs\review100_v3_A
# B: with the document hint
& $py .\screen_eurlex_provisions_simple.py @common --document-run .\runs\eurlex_full_v10 --output-dir .\runs\review100_v3_B
# C: no document hint, with thinking (slower)
& $py .\screen_eurlex_provisions_simple.py @common --thinking on --output-dir .\runs\review100_v3_C
```

Use your own manifest and enriched folder if they differ. Rerun a command to
resume it. A setting cannot be changed inside an existing output folder.

## 2. Compare each variant with the labels

```powershell
foreach ($v in "A", "B", "C") {
    & $py .\tools\compare_provision_labels.py `
        --labels '.\EUR-Lex_100_Provision_Review_AI_labels (1).xlsx' `
        --run ".\runs\review100_v3_$v" --baseline .\runs\eurlex_provisions_review100_v2
}
```

Read two lines first: "Removed by Qwen but labelled relevant" (should be low)
and "Kept by Qwen but labelled IGNORE" (version 2: 37 of 66). Each run folder
gets a `label_comparison.csv` with one row per provision and Qwen's answers:
basis, actor, subject, topic, company role, indirect path and open question.
A wrong row shows which answer was wrong.

Use hand-checked labels where you can. Labels from another AI only show
where two models disagree.

## 3. Full corpus, once a variant is good enough

```powershell
& .\.venv\Scripts\python.exe .\run_eurlex_provision_corpus.py
```

Results go to `runs/eurlex_provisions_full_v3`. The document hint is used when
the manifest's folder holds `relevance_export/direct.json` and
`indirect.json`; add `--no-document-hint` to leave it out, and `--thinking on`
for thinking. Run the same command again to resume.

## What Qwen receives

```text
System message   instructions + company profile          the same in every request
User message     act, target, position, references       changes per target
                 TASK line                               last: "classify" or "confirmation pass"
```

The system message is identical in every request, so vLLM prefix caching can
reuse it. `tools/check_prefix_cache.py` tests whether that works on your
endpoint.

- **act**: CELEX, title and, with `--document-run`, why the act was kept.
- **target**: the provision, once. Markers `[P1]`, `[P2]`, … number its text.
- **position**: the text before a table, the annex scope, and the provision
  that refers to an annex or table.
- **references**: cited provisions, at most about 5,000 characters.
- **missing_references**, **context_not_included**: names of texts that could
  not be supplied or did not fit.
- **source_notes**: known problems, such as text that is cut off.

A target above 12,000 characters is split into parts. The target takes the
highest level of its parts, and the audit keeps every part's result.

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

Known gap: text stored under the wrong label is not detected. Example: in
32004L0054 the element stored as Annex II holds the end of Article 11. This
comes from the database parser and was the same in earlier runs.

## Results

- `relevance_export/results.json`: level, reason and area per classified
  target. Levels are DIRECT, INDIRECT, UNSURE and IGNORE.
- `audit/run_summary.json`: counts, plus
  - `targets_kept`, `decision_basis`, `ignore_reasons`, `negative_anchors`,
    `indirect_paths`, `company_roles`, `topics`
  - `not_assessable` and `not_assessable_targets`
  - `ignore_reviews`: confirmed and changed IGNORE decisions
  - `acts_all_ignore`: acts in which every provision was ignored
  - `requests`: number of calls, seconds and, when reported, cached tokens
- `audit/run_config.json`: the system prompt, and whether thinking and the
  document hint were on.
- `audit/<CELEX>/…json`: the full evidence, every request part and Qwen's
  answers (`details`).

## Tests

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_eurlex_provisions_simple.py
```
