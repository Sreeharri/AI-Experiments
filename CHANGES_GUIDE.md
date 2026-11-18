# Legal Radar – Changes step (guide)

Status 18 Nov 2026 (v1.1, with act pages). Explains amendments, repeals, corrigenda and other changes between EUR-Lex acts and routes them to the departments. Plan and background: `claude/legal-radar-changes-plan.md`.

## What it does

| Step | Script | Qwen? | Result |
|---|---|---|---|
| 1 Export | `tools\export_eurlex_changes.py` | no | `runs\eurlex_changes_export\records.jsonl`: one record per change, read from the backend database (read-only) |
| 2 Explain | `explain_eurlex_changes.py` | yes | headline, what changed (2–3 sentences), facts before → after, dates, significance |
| 3 Changing acts | same script | yes | a short paragraph per changing act |
| 4 Export for the UI | same script | no | inbox cards per department, preview pages, review list |

**Which changes:** every relationship "changing act → changed act" of a type that changes text or application (AMENDS, REPLACES, REPEALS, CORRECTS, DEROGATES, SUSPENDS, EXTENDS…, DEFERS_APPLICATION, …) where the changing act **or** the changed act is in the classification results, and the changing act is from 2015 or later (`--since`). Relationships that only add rules next to an act (COMPLETES, IMPLEMENTS, …) are listed in `links.json`, not explained.

**Three kinds of records:**
- **PROVISION**: one change to one article or annex (add, insert, replace, delete …). All changes of one changing act to the same article are explained together.
- **ACT**: a change of the whole act without provision detail (whole act repealed, application deferred …).
- **CORRIGENDUM**: a correction of the published text. Wrong and correct text are taken from "for: '…', read: '…'" by code. It is labelled "Berichtigung" and has no new date.

**Old and new wording** (column `wording`):
- `FULL`: the article before the change and after it, from the stored versions (or a reviewed comparison);
- `NEW_ONLY`: only the new text (new article, or the quoted text of the instruction);
- `OLD_ONLY`, `INSTRUCTION_ONLY`: only that is available;
- `NONE`: no text at all. Not sent to Qwen; listed in `missing_text.csv` for enrichment.

Qwen also gets the changing act's final articles (entry into force, application, transposition, transition), so the dates of the change are complete.

**Checks** (one correction request, otherwise the item is marked failed and retried at the next start):
- a "before" value must stand in the old text, an "after" value in the new text or the instruction; numbers and dates follow the same rules as the summaries (`eurlex_summary_checks.py`);
- without old text, "before" must stay empty;
- a change of a number or date can never be EDITORIAL; a corrigendum that changes a number or date can never be WORDING_ONLY;
- the answer must be in German (or English with `--language en`).

**Who gets a change:**
1. the departments of the changed article, if it is DIRECT or INDIRECT in the classification (`CHANGED_PROVISION`);
2. for whole-act changes and corrigenda: the departments of the changed act (`CHANGED_ACT`);
3. otherwise the departments of the changing act's article that makes the change (`CHANGING_PROVISION`), or of the changing act (`CHANGING_ACT`);
4. otherwise nobody yet (`NOT_SCREENED`): listed in `to_screen.csv`.

EDITORIAL changes and corrigenda that only fix wording stay out of the inbox (they are still in `review.csv`). A repeal of a relevant act always goes to the inbox.

## Files (copy into `C:\Users\sthirumalair\AI-Experiments`)

```
tools\export_eurlex_changes.py
explain_eurlex_changes.py
prompts\eurlex_change_explain.md
prompts\eurlex_corrigendum_explain.md
prompts\eurlex_change_act_summary.md
tests\test_export_eurlex_changes.py
tests\test_explain_eurlex_changes.py
CHANGES_GUIDE.md
```

Needs: summaries v4.4 in place (`summarize_eurlex_provisions.py`, `eurlex_summary_checks.py`), the classification `runs\eurlex_classification_v5\results.json`, and `DATABASE_URL` in `.env` (as for the corpus export). No backend file is changed.

## Commands

Run after the summaries retry has finished; both use the same Qwen endpoint.

```
cd C:\Users\sthirumalair\AI-Experiments

# 1. code tests, no Qwen and no database needed - must end with "Ran 16 tests" and "OK (skipped=2)"
& .\.venv\Scripts\python.exe -m unittest tests.test_export_eurlex_changes tests.test_explain_eurlex_changes

# 2. export: first count, then write (reads the database, changes nothing)
& .\.venv\Scripts\python.exe tools\export_eurlex_changes.py --count-only
& .\.venv\Scripts\python.exe tools\export_eurlex_changes.py --since 2015

# 3. what would be explained and who would get it
& .\.venv\Scripts\python.exe explain_eurlex_changes.py --count-only

# 4. pilot: 40 items, a fixed mix of corrigenda, whole-act changes and each kind of wording
& .\.venv\Scripts\python.exe explain_eurlex_changes.py --sample 40 --output-dir runs\eurlex_changes_pilot

# 5. full run (after the pilot passed)
& .\.venv\Scripts\python.exe explain_eurlex_changes.py
```

The two skipped tests need a test database (`LR_TEST_DATABASE_URL`); they ran on a local PostgreSQL before delivery. Do not point them at the production database.

Options: `--celex 32010L0075` (only changes of these acts, export and explain), `--since 0` (all years), `--language en`, `--workers 4`. Ctrl+C stops, the same command continues. A new export needs a new `--out` folder; changed prompts need a new `--output-dir`.

## What to send after step 2

`runs\eurlex_changes_export\export_summary.json`. It shows how many records per kind, per relationship and operation, and the gaps (`wording_*`, `target_not_resolved`, `instruction_not_found`, `same_text_in_versions`, `corrigenda_without_parsed_corrections`). It decides where enrichment is needed.

## Pilot: when is it good enough for the full run

Open `runs\eurlex_changes_pilot\review.csv` next to EUR-Lex (`preview\index.html` shows old and new text behind "Wortlaut vorher / nachher").

| Check | Go |
|---|---|
| `failed` in `summary.json` | at most 2 of 40 |
| An invented difference (something the texts do not show) | 0 of 40 |
| A real change of a duty, threshold, deadline or addressee marked EDITORIAL | **0** (this hides a change) |
| Significance right | at least 36 of 40 |
| Dates right and complete (applies from, transposition, transition) | at least 36 of 40 |
| Corrigenda: labelled Berichtigung, wrong → correct right | all |
| Routing: the department should see this change | note the wrong ones (routing question) |

If a check fails: note 3–5 rows, change the prompt, run the pilot again into a new folder.

## Output folder (`runs\eurlex_changes_v1`)

- `inbox\<department>.json`: cards for the UI, one per changed act, with its changes (newest first)
- `preview\index.html`: the same as pages
- `review.csv`: one row per item, with columns to fill in: explanation correct, missing or wrong, significance right, comment
- `summary.json`: counts per kind, significance, wording and route; failures
- `missing_text.csv`: changes without any text (for enrichment)
- `to_screen.csv`: changes that could not be routed (neither act classified for that provision)
- `acts\<changing act>.json`: the paragraph about each changing act
- `act_pages\<celex>.json`: what the act page shows (see below)

Card fields: `celex, title, eurlex_url, pdf_url, changes[{kind, relation, label, operations, provision, changing{celex, title, date, eurlex_url, pdf_url}, effective_from, wording, headline, text, facts[{what, before, after, passages, quotes}], dates[{what, date}], significance, level, route, old_text, new_text}], changing_acts{celex: [sentences]}, note`.

## Act page

For every act with changes, `act_pages\<celex>.json` holds what the act page shows below the summaries:

- `changes_made` (the act changes other acts, for example an amending act):
  - `summary`: a short explanation of what the act changes (2–4 sentences, written by Qwen from its explained changes);
  - `changed_acts`: which acts it changes and how many provisions of each;
  - `items`: every change it makes, newest first: label (Änderung, Aufhebung, Berichtigung …), changed act, provision,
    headline, `short` (first sentence of the explanation), effective date, significance, and the link fields.
- `changed_by` (the act was changed by other acts):
  - `summary_line`: one line built by code ("Dieser Rechtsakt wurde 4-mal geändert oder berichtigt, durch 2 Rechtsakte. Zuletzt: …");
  - `changing_acts`: which acts changed it, each with its short explanation;
  - `items`: every change made to it, newest first.

**Link to the changes page:** every item carries `change_ids` (the backend's `relationship_provision_changes.id`),
`relationship_id`, `changed_document_id` and `changing_document_id`, so the frontend can build its own link. Or give a
template and the script fills `link`:

```
& .\.venv\Scripts\python.exe explain_eurlex_changes.py --link-template "/acts/{changed_document_id}/changes?change={change_id}"
```

Placeholders: `item_id`, `change_id`, `relationship_id`, `changed_celex`, `changing_celex`, `changed_document_id`,
`changing_document_id`. The template only affects the files written at the end; it can be changed at any later start.

Preview: `preview\acts\index.html`, one page per act.

On the act page, below the summaries:

1. **"Änderungen durch diesen Rechtsakt"** (only for acts that change others): the short explanation, then the list;
   each row: tag, changed act and provision, headline, one sentence, "gilt ab …", link "Zur Änderung".
2. **"Geändert durch"** (only for acts that were changed): the summary line, then the list; each row links to the
   change.

## UI

On the department card of an act:
1. tag **Geändert** / **Berichtigung** / **Aufgehoben** with the changing act and "gilt ab …";
2. box **"Was sich geändert hat"**: per change the headline, the 2–3 sentences, facts as ~~vorher~~ → **nachher**, the dates;
3. behind a toggle: old and new wording;
4. the article's normal summary (current law) stays below.

## Known limits (v1)

- EUR-Lex only. English versions of the texts (the backend stores EN).
- Old/new wording is the whole article or annex; for very long ones only the changed passages and their neighbours are sent.
- `NOT_SCREENED` changes are listed, not screened yet: screening their new text with the provision-relevance step is the next step if the list is long.
- Enrichment of missing texts (`missing_text.csv`) is a separate step after we see the gaps.
- A corrigendum of only the German version may have no English text in the database; it then shows up as `corrigenda_without_parsed_corrections` or `changing_act_without_text` in the export gaps.
