# EUR-Lex provision relevance, version 3.1

Copy the files of this patch into the root of `AI-Experiments` and replace the
matching files. If `resume_eurlex_provision_corpus.py` is still there, delete
it. The patch uses the same `.env`, Python environment, anchor catalog and
enriched files as before. Older run folders stay valid and untouched.

## What changed since version 3

Version 3 (run A) removed far more noise than version 2, but it also removed
provisions that matter. The saved answers showed why:

| Finding in run A | Change in 3.1 |
|---|---|
| "FORMAL" was used as a general "not relevant" answer: 31 of 53 removals, among them the VAT rule and the machinery article | Qwen must name one formal function from a fixed list (addressees, entry into force, …). A rule, value or definition is never formal |
| 15 provisions, nearly all unrelated to noise, got the topic `noise_vibration`. Most likely cause: the reply format only lets Qwen finish a listed value, so a reply starting with "no…" could only end there | "none" and similar spellings are now listed values and mean "no topic" |
| Qwen copied an example from the instructions into an answer (a chimney table became "importers of toys") | The instructions no longer contain example answers |
| A rule that names no actor was assigned to "Member States" and dropped | Qwen names whose conduct the rule governs, and says what kind of actor that is |
| A company role was chosen although the actor was a state or a standards body | A role only gives DIRECT when the actor is a business |
| Half of the removals for "no path" were labelled relevant (6 of 12) | A business actor in a business area with no named link is UNSURE, not removed |
| The confirmation pass confirmed all 53 removals | It no longer sees the first answers. `--confirm-thinking on` lets Qwen think in that pass only |

## How the level follows

Qwen returns `actor`, `actor_kind`, `subject`, `formal_function`, `topic`,
`company_role`, `indirect_path`, `open_question` and `reason`. It never
returns a level.

| Qwen's answers | Level | Basis in the audit |
|---|---|---|
| a formal function, and the actor is not a business | IGNORE | `FORMAL` |
| a formal function, but a business is named as actor | UNSURE | `FORMAL_CONFLICT` |
| topic is a negative anchor | IGNORE | `OUTSIDE_BUSINESS` |
| no catalog entry fits, no role, no path | IGNORE | `NO_MATCH` |
| business anchor, a company role, actor is a business | DIRECT | `ROLE` |
| the same with an open question | UNSURE | `OPEN_QUESTION` |
| business anchor, an indirect path, no direct role | INDIRECT | `PATH` |
| business anchor, no role, no path, actor is a business or not stated | UNSURE | `TOPIC_ONLY` |
| business anchor, no role, no path, actor is a state, EU body or private person | IGNORE | `NO_PATH` |

UNSURE means the provision is kept for the department because something is
open: a company fact, or an unclear link. DIRECT, INDIRECT and UNSURE are
kept. IGNORE is removed. Every IGNORE gets a confirmation pass.

Applied to the answers Qwen gave in run A, with the actor kind filled in by
hand, the new table alone brings back 4 of the 17 removed provisions that
were labelled relevant and keeps 1 more that was labelled IGNORE. The other
changes need a new run to show.

## 1. Rerun the 100 reviewed provisions

```powershell
$py = ".\.venv\Scripts\python.exe"
$common = @("pilot", "--manifest", ".\runs\eurlex_full_v10\provision_manifest.json",
            "--enriched-dir", ".\output_db_corpus",
            "--targets-file", ".\config\eurlex_provision_review100.json")

# D: version 3.1, no document hint, no thinking
& $py .\screen_eurlex_provisions_simple.py @common --output-dir .\runs\review100_v3_1_D
# E: the same, with thinking in the confirmation pass only
& $py .\screen_eurlex_provisions_simple.py @common --confirm-thinking on --output-dir .\runs\review100_v3_1_E
```

Use your own manifest and enriched folder if they differ. Rerun a command to
resume it. A setting cannot be changed inside an existing output folder.
`--thinking on` (both passes) and `--document-run` (document hint) still
exist for later comparisons.

## 2. Compare with the labels and the checked cases

```powershell
foreach ($v in "D", "E") {
    & $py .\tools\compare_provision_labels.py `
        --labels '.\EUR-Lex_100_Provision_Review_AI_labels (1).xlsx' `
        --run ".\runs\review100_v3_1_$v" --baseline .\runs\review100_v3_A
}
```

The output has three parts:

- **Scores against the workbook labels.** Read "Removed by Qwen but labelled
  relevant" first (run A: 17 of 52) and then "Kept by Qwen but labelled
  IGNORE" (run A: 14 of 28).
- **Hand-checked cases.** Six provisions whose expected result was checked
  against the legal text, from `config/eurlex_provision_checked_cases.json`.
  Each line says `ok` or `WRONG`. Run A had 3 of 6 right. Add a case to the
  file whenever you verify one. These cases are never shown to Qwen.
- **`label_comparison.csv`** in the run folder, one row per provision with
  Qwen's answers.

The tunnel item of 32004L0054 is left out of the scores, because its stored
text is the end of Article 11 and not Annex II.

Use hand-checked labels where you can. Labels from another AI only show
where two models disagree.

## 3. Full corpus, once a variant is good enough

```powershell
& .\.venv\Scripts\python.exe .\run_eurlex_provision_corpus.py
```

Results go to `runs/eurlex_provisions_full_v3_1`. The document hint is used
when the manifest's folder holds `relevance_export/direct.json` and
`indirect.json`; add `--no-document-hint` to leave it out. `--thinking on` and
`--confirm-thinking on` work here too. Run the same command again to resume.

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
comes from the database parser and was the same in earlier runs. Such rows
can be listed under `excluded` in `config/eurlex_provision_checked_cases.json`
so that they do not distort the scores.

## Results

- `relevance_export/results.json`: level, reason and area per classified
  target. Levels are DIRECT, INDIRECT, UNSURE and IGNORE.
- `audit/run_summary.json`: counts, plus
  - `targets_kept`, `decision_basis`, `ignore_reasons`, `negative_anchors`,
    `indirect_paths`, `company_roles`, `formal_functions`, `actor_kinds`,
    `topics`
  - `not_assessable` and `not_assessable_targets`
  - `ignore_reviews`: confirmed and changed IGNORE decisions
  - `acts_all_ignore`: acts in which every provision was ignored
  - `requests`: number of calls, seconds and, when reported, cached tokens
- `audit/run_config.json`: the system prompt, and whether thinking, thinking
  in the confirmation pass and the document hint were on.
- `audit/<CELEX>/…json`: the full evidence, every request part and Qwen's
  answers (`details`).

## Tests

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_eurlex_provisions_simple.py
```
