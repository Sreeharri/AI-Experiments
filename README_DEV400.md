# Shortage fix — read first

The uploaded report records 334 eligible acts and 139 rejected versions.
100 versions failed the two-annex-chunk requirement; 42 failed the selected
article distinctness check; three failed both. These are versions, not counts
of distinct acts. The old report does not distinguish zero annex chunks from
one, so it cannot prove how many are recoverable.

The default requirement remains TWO annex chunks. To explicitly allow one
usable annex chunk (for example a complete short annex), run:

```powershell
.\.venv\Scripts\python.exe .\tools\prepare_eurlex_dev400.py --env .\.env --as-of 2026-11-04 --min-annex-chunks 1 --out .\data\dev400_new_v2
```

Zero usable annex chunks are still rejected. The 9 distinct selected article
paths, 3 usable recitals, chapter, title, same-version and date requirements
remain. No missing text is invented, and the Qwen code/prompt is unchanged.

If fewer than 400 qualify, the new version preserves all eligible full-text acts
and blank labels in a timestamped sibling folder, with an `insufficient` manifest.
It still exits unsuccessfully and does not claim to have made 200 + 200.
The updated diagnostic records actual article/recital/annex counts per rejected
version. The previous run did not preserve the 334 acts, so this rerun must read
them from your database again.

Extract this patch in the existing AI-Experiments root. It contains no AI/prompt
replacements. Test with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_prepare_eurlex_dev400.py" -v
```

20 offline tests pass, including 400-act fixture export, explicit one-annex mode,
zero-annex rejection and preservation of eligible sources on a shortage. No live
database export has been performed here. Getting 400 remains unconfirmed until
the rerun completes.

---

# Prepare a new 200 + 200 development input

This is an additive patch for your existing **v6.3 AI-Experiments project**.
It prepares inputs only. The ZIP does not contain or overwrite the AI runner,
model client, prompts, business anchors or evaluator. No Qwen requests are made.

"20 + 200" in the request is interpreted as **200 + 200 = 400 acts**.

## Install and export

Run PowerShell from the root of your existing AI-Experiments project. Extract
the ZIP **there**, keeping `tools`, `tests` and `config` as subdirectories:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\eurlex_dev400_preparation.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dev400.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_prepare_eurlex_dev400.py"
.\.venv\Scripts\python.exe .\tools\prepare_eurlex_dev400.py --env .\.env --as-of 2026-11-04 --out .\data\dev400_new
```

The selected `.env` needs your existing backend `DATABASE_URL`. If it lives in
another folder, pass its path to `--env`. The Qwen connection settings are not
used for preparation. Keep both new SQL files next to the new Python file.
Existing `lr_ai/document_filter.py`, `tools/document_relevance.py`,
`tools/export_eurlex.py`, prompt files and profile YAML files must remain in the
project. No database data is changed: the existing connection helper uses one
read-only, repeatable-read transaction with a 20-minute per-statement timeout.

The actual PostgreSQL export must run on your machine. This package contains
tested selection/export code, **not 400 already exported real acts**. The
database was unavailable in the preparation environment.

## What enters the sets

The exporter searches your stored EUR-Lex regulations, directives and decisions,
rather than using an online list of titles without checking the stored text.
It excludes the **406 unique CELEX IDs** in the earlier two 200-act lists plus
the 10 inspection acts. The earlier lists have four IDs in common with those
10 acts. Their old proposed labels are discarded.

Every new act must provide all of the following:

| Required input | Rule |
| --- | --- |
| Title | Nonblank English/German document title or instrument title |
| Chapter structure | At least one chapter with a heading or body text |
| Articles | At least 9 distinct canonical article paths with usable text |
| Recitals | At least 3 distinct usable recitals, from elements or the same version's recital set |
| Annex evidence | At least 2 usable annex chunks, which may come from one long annex |
| Stored source | All legal elements and the recital set belong to the selected version |
| Date | Act and version are not dated after `--as-of`; no future consolidated text |
| Existing evidence selector | Produces the current 9 article + 3 recital + 2 annex starting pack |

English is preferred, with German as fallback. If a preferred version is
incomplete, the exporter tries another eligible stored version of that CELEX
before moving to another act. Version IDs, dates, kind and language are saved.
An original act with a future commencement date can still be included when
already published. A structurally complete original can be selected instead of
an incomplete consolidation; do not assume it represents the latest amended law.

These rules check **input completeness**. They neither assign nor change
DIRECT, INDIRECT, IGNORE or NEEDS_REVIEW. Stored parser warnings are retained;
structural presence alone does not prove that every passage was parsed correctly.

Each input contains **all stored legal-element and recital text for its selected
version**, without article/annex truncation. Unused harvesting metadata and
relationship lists are omitted. This does not mean the model sees all text at
once: the existing AI evidence selection and context budgeting remain unchanged.

If fewer than 400 eligible acts exist after exclusions, preparation stops. It
does not relax the requirements or write a partially filled pair. Eligible full-text acts are now preserved in a separate timestamped `dev400_new_eligible_*` folder. Where full
text was examined, an adjacent `dev400_new_insufficient.json` explains rejected
versions. Additional source ingestion or parser repair may then be necessary.

## Output

```text
data/dev400_new/
  dev_A_200.json
  dev_B_200.json
  dev_A_200_labels.csv
  dev_B_200_labels.csv
  dev_A_200_celex.txt
  dev_B_200_celex.txt
  selected_acts.json
  rejected_versions.csv
  selection_manifest.json
```

The inputs contain **200 unique acts each**, with no overlap between sets.
`selected_acts.json` records each act's version, available structure, warnings,
initial evidence source IDs and content hash. The manifest records seed,
exclusions, counts and hashes of inputs, profile, prompt and relevant code.

The seed is fixed. With the same database snapshot and code/profile, selection
and split reproduce. CELEX IDs are sampled by seeded hash after structural
eligibility; splitting roughly balances act type and decade across A and B.
The output directory must be new to avoid replacing a frozen set accidentally.
Use `--exclude-file .\other_ids.csv` to exclude another list with a `celex`
column, or a text file with one CELEX per line.

Both sets are development sets. Structurally complete acts are a restricted
subset of the database, so these sets do not measure performance on short acts
or records with missing evidence. Related amending/base acts are not grouped;
set B is not a leakage-proof independent holdout. Labels are not pre-balanced:
inspect the human label counts before interpreting per-class metrics.

## Inspect and run the frozen AI pipeline

Optional inspection of the exact initial evidence, with no model request:

```powershell
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\dev400_new\dev_A_200.json --as-of 2026-11-04 --prepare-only --out .\runs\dev_A_200_preview
```

Only when you want to start the AI development run:

```powershell
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\dev400_new\dev_A_200.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\dev_A_200_v6_3
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\dev400_new\dev_B_200.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\dev_B_200_v6_3
```

Use the JSON files directly. The unchanged runner reads each 200-act input into
memory; available RAM must accommodate that input. Compressing an input into a
ZIP does not avoid that, and its ZIP path also has a 500 MB uncompressed limit.

Fill `label` independently with `DIRECT`, `INDIRECT` or `IGNORE`. The optional
`anchor_ids` and `notes` columns are for human annotation; keep `celex` intact.
The existing evaluator accepts the `celex,label` columns in these files, but
requires every row to be labelled. Agree evaluation thresholds before looking
at model results; those thresholds are intentionally not changed by this patch.

## Files in this patch

- `tools/prepare_eurlex_dev400.py`: selection, complete-text export and split.
- `tools/eurlex_dev_candidates.sql`: read-only structural candidate query.
- `tools/eurlex_dev_act.sql`: read-only single-version complete-text export.
- `config/excluded_previous_celex.txt`: exclusions from earlier samples.
- `tests/test_prepare_eurlex_dev400.py`: offline workflow and evidence tests.
- `requirements-dev400.txt`: dependencies already used by the project.
- `verification/dev400_checks.txt`: recorded checks and their limits.

The offline 400-act test uses **synthetic fixtures** to verify exact set sizes,
disjointness, blank labels, reproducibility and runner input compatibility. It
is not a live database export or a model-accuracy test.
