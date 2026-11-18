# Legal Radar EUR-Lex changes v1.5

Extract this patch into the existing AI-Experiments folder. It uses the classification results and the backend wording export already produced by v1.4. Existing run folders can serve as evidence for comparison.

## Test and replay the 40 pilot items

Run from AI-Experiments in PowerShell with the project's Python environment. If you only have the pilot ZIP, extract its `eurlex_changes_export_v4` directory under `runs`. The explainer reads `records.jsonl`, `roles.json` when available, and `runs\eurlex_classification_v5\results.json`.

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*eurlex_changes.py'
& .\.venv\Scripts\python.exe .\explain_eurlex_changes.py --export-dir .\runs\eurlex_changes_export_v4 --item-ids .\config\eurlex_changes_pilot_v4_ids.txt --output-dir .\runs\eurlex_changes_pilot_v5
```

The two database integration tests are skipped without `LR_TEST_DATABASE_URL`. Use a disposable test database for those tests. The same pilot command resumes a partially completed run and retries validation failures. An exit code of 2 means an explanation or act summary still failed validation. Changed inputs, settings or prompts require a new output folder.

Check `summary.json` for 40 planned items, zero open items, zero failures and an empty `act_summary_failures` list. Inspect `review.csv` and `to_screen.csv`. For entries in the inbox preview, check the legal effect, dates, affected provision and receiving department. Spot-check stored old and new wording against the running UI using the `change_id` from `records.jsonl`. The review queue is expected to contain items where the available evidence cannot justify publishing.

## Run the full batch after the pilot passes

```powershell
& .\.venv\Scripts\python.exe .\explain_eurlex_changes.py --export-dir .\runs\eurlex_changes_export_v4 --output-dir .\runs\eurlex_changes_v5
```

The command writes reports, act pages and department inbox previews. Legal Radar can use `to_screen.csv` to identify records that need the affected provisions screened, missing sources retrieved or dates and scope confirmed.

## Refresh missing sources when the office machine can reach EUR-Lex

The original v4 export recorded 1,715 corrigenda as `NOT_FETCHED`. Test connectivity on a known missing example first. To include retrieval in the full batch, use a fresh export folder, then use that folder as `--export-dir` for both the pilot and full explanations. The `--item-ids` replay checks that all 40 expected IDs still exist in the refreshed data.

```powershell
& .\.venv\Scripts\python.exe .\tools\eurlex_text_fetch.py '32016R0185R(01)' DE
# Set LR_BACKEND_DIR to the folder containing app\services\eurlex_wording.py, if it is not already set.
& .\.venv\Scripts\python.exe .\tools\export_eurlex_changes.py --since 2015 --fetch-missing --out .\runs\eurlex_changes_export_v5
```

Run the second command if the connectivity test succeeds. When the refreshed export changes the evidence, use a new pilot output folder.

## How to read the output

- `summary.json` counts generated explanations, missing evidence, implicit repeals needing scope review, failed explanations and failed act summaries.
- `review.csv` records explanations, previous relevance classifications and dates with their sources. An article with mixed or missing dates has a blank group date. The individual change rows carry their own dates.
- `to_screen.csv` gives the reasons for review with item and change IDs. Scope and date remain open for implicit repeals until verified. Missing source evidence, ambiguous provision matches and act-level routing go to review.
- `inbox\` and `preview\` contain the changes that passed the evidence and routing checks. The business context quotes the prior classification for the exact affected locator. Read it with the legal change explanation.
- `act_pages\` shows changes made and received with role badges and a review status where applicable.
