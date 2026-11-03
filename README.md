# Legal Radar AI Experiments

Round 1 tests document relevance for the retained EUR-Lex collection. It uses
the existing read-only PostgreSQL exporter, Qwen client and bilingual business
anchors. The 25-domain catalogue is included for later classification work.
Each run has separate source data, summaries, predictions, labels and scores.

## 1. Set up on your office laptop

Extract this ZIP into a separate project folder. In PowerShell, open the
extracted folder that contains this README.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe tools\check_business_anchors.py
```

Set `DATABASE_URL` to the Legal Radar PostgreSQL connection string. You can
also pass `--db-env "..\legal_radar_backend\.env"` to the two export commands
below if that relative path matches your folders. The Qwen endpoint in
`.env.example` is the one from your earlier experiment. Set the API key if
required and stay on the company network. Add your company CA via
`SSL_CERT_FILE` if required. Keep the filled .env local.

## 2. Export the titles and send that file for shortlisting

```powershell
.\.venv\Scripts\python.exe tools\relevance.py titles
```

The file to share for the shortlist is **`data\catalogue\titles.json`**.
Each row has the PostgreSQL document ID, CELEX ID and English/German titles.
The exporter selects non-deleted original EUR-Lex regulations, directives
and decisions with latest recorded decision `KEEP`. The count is whatever
matches in your database. The broader original exporter includes blocked
records, so its total differs. This first benchmark measures the retained
catalogue. It does not measure acts excluded by the upstream filter.

The shortlist should be a CSV with header `celex,stratum,group` and 400 distinct
CELEX IDs from the title export. Use `DIRECT`, `INDIRECT`, `IRRELEVANT`,
`AMBIGUOUS` or `RANDOM` for tentative title-based sampling categories.
`group` is optional. Give related records a common group ID where known. These
categories are for sampling, not ground truth. Include some random and
ambiguous records to reduce title-selection bias. Save as `shortlist.csv`.

## 3. Prepare the split and export evidence for the 400

```powershell
.\.venv\Scripts\python.exe tools\relevance.py prepare shortlist.csv
```

This produces two exact sets of 200 with an approximately balanced mix of
the sampling categories. Known amendment links among selected records and
explicit groups stay together. Review the printed counts. The exporter
reads text and metadata for the selected IDs from the database and checks
the IDs against the title catalogue. It selects one stored text/recital
version per language according to the original exporter's version policy.
If a linked group prevents a 200/200 split, adjust the shortlist.

## 4. Summarize 400 and classify only development

```powershell
.\.venv\Scripts\python.exe tools\relevance.py summarize
.\.venv\Scripts\python.exe tools\relevance.py classify dev
.\.venv\Scripts\python.exe tools\relevance.py workbook dev
```

Qwen receives all exported recitals for one chosen language per act. The
runner prefers more available recitals, then a matching title, then German.
Overlong inputs are split using the endpoint tokenizer and combined into one
brief. Every summary point has recital reference IDs. No clustering is used.
Relevance uses title, brief and matching-language anchors with strict JSON,
thinking off and sequential requests. An act with no usable recitals gets
`NEEDS_REVIEW`. An API, truncation or validation error stops the command with
the current CELEX on screen. After resolving the error, rerun the same command.
Completed per-act files are retained and skipped. Use one process per run folder.

Open **`runs\round1\blind\dev\labels.xlsx`**. Fill the **Label** column with
`DIRECT`, `INDIRECT`, `IRRELEVANT` or `NEEDS_REVIEW` and use Notes for your
reason. The workbook excludes predictions and shortlist categories.
Check official text or the adjacent `evidence` JSON when the AI summary
leaves anything unclear. The JSON includes numbered recitals and available
articles. A summary error should not become the human reference label.

## 5. Score development and freeze

Close the workbook after saving, then run:

```powershell
.\.venv\Scripts\python.exe tools\relevance.py score dev
```

Open `runs\round1\score_dev.json` for the confusion matrix, direct recall,
direct misses, indirect cases called direct, review rate and mismatched
CELEX IDs. The accuracy denominator excludes human NEEDS_REVIEW rows,
but model abstentions count as misses on human-decided rows.

If you revise the prompts or anchors, preserve the same split and exported
texts by copying only `sample.json` and `data` into a new run directory.
Run all subsequent commands with `--run runs\round2` **before** the subcommand.
Regenerate summaries and predictions, then score with the previous
human labels via `score dev --labels runs\round1\blind\dev\labels.xlsx`.
Use the original blind labels as long as the source snapshot is the same.

Once satisfied, record your actual acceptance criteria before inspecting
holdout predictions:

```powershell
.\.venv\Scripts\python.exe tools\relevance.py freeze --criteria "Replace with agreed recall and review-rate limits"
```

## 6. Run the final 200

```powershell
.\.venv\Scripts\python.exe tools\relevance.py classify holdout
.\.venv\Scripts\python.exe tools\relevance.py workbook holdout
```

Label and save `runs\round1\blind\holdout\labels.xlsx` before opening the
predictions. Then run:

```powershell
.\.venv\Scripts\python.exe tools\relevance.py score holdout
```

Review the holdout report against the frozen criteria. This ZIP covers the
400-act experiment. After acceptance, the same evidence, summary and decision
modules can be used in a separate batched catalogue backfill. Other sources,
domain classification, department routing and tasks are later stages.
