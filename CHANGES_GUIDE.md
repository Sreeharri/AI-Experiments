# Legal Radar – Changes step (guide)

Status 18 Nov 2026 (v2.3, simple explanations with the whole article as background). Explains amendments, repeals, corrigenda and other changes between EUR-Lex acts and routes them to the departments. Plan and background: `claude/legal-radar-changes-plan.md`.

## v2.3: one point of a mixed instruction (after run v9)

Run v9 on 2019/1020 (v2.2): 13 requests, 10 right; 32 deletions shown as "Gestrichen." without Qwen; missing text down from 27 to 13 (12 corrigenda without text, 1 implicit repeal). The whole article helped: Art. 56(1) of 305/2011 and Annex I point 21 were explained correctly from it. Still wrong: 765/2008 Art. 32(1) points (c), (f), (g). They got the whole instruction ("(a) point (c) is replaced …; (b) points (d) and (e) are deleted; (c) points (f) and (g) are replaced …") and mixed the points up.

v2.3 (explain only, the export stays): for a point in such a mixed instruction, Qwen gets only the clause about that point and the point's own quoted wording as new text ("(g) activities carried out under programmes of technical assistance …"). A simple instruction stays as it is. Tests: 30.

## v2.2: long articles

An article or annex longer than 5,000 characters is no longer cut at the end. It keeps the changed paragraph or point and as much as fits of the text just before and after it, cut at sentence or point ends, with "…" where text is left out. Short articles still go whole. Where the place is not in a version (for example a new point in the version before the change), that version keeps its beginning. Tests: 29.

## v2.1: whole article as background, deletions from the amending act

Run v8 on 2019/1020: Qwen answered all 28 requests; 22 were right, 6 wrong. The 6 wrong ones had no old text (Qwen made up an "instead of", 765/2008 Art. 32(1)) or a wrong amending sentence (765/2008 Chapter III and Title). v2.1 stays simple: still no checks, nothing rejected.

| Change | What it does |
|---|---|
| Whole article from the database | The export adds `article_before` (the changed article or annex in the newest version dated before the change, else the original) and `article_now` (newest version), each with its version date, at most 5,000 characters. Qwen gets them with the heading as background to understand the place; without old text it can find the old wording there. Same version or same text: only `article_now`. |
| Deleted, even when the database says OTHER | When the amending act's own words say the place is deleted ("points 1, 2 and 19 are deleted", "Chapter III, containing Articles 15 to 29, is deleted", "(b) points (d) and (e) are deleted"), the change is a deletion: not sent to Qwen, shown as "Gestrichen." (or "Aufgehoben."). Additions stay additions. |
| Article ranges | "Articles 15 to 29" now also finds Articles 16 to 28 (13 changes of 765/2008 had no text before; now they are deletions). |
| Chapters and titles | For places like "Chapter III" or "Title", the export takes the amending point about that place ("(1) the title is replaced by …"), no longer the whole amending part of the act. |
| Broken closing quotes | "(2) in Annex I, point 21 … is replaced" is split off even when the quote of point (1) was never closed (footnote marks). 2023/1542 Annex I gets its own sentence; Art. 4(5) no longer carries it. |
| Prompt | Without old text: no "instead of … now …", only what the place says now (or compare with the old wording from `article_before`). Only the change at `place`; other points of the instruction or the article are left out. German only. |

A new export is needed (new fields). Tests: 28 (2 need the test database).

## v2.0: simple explanations

The checks of v1.6/v1.7 rejected too much (v7: 5 of 40 explained, v5 had 21). v2.0 keeps it simple, like the other AI steps:

| Case | Sent to Qwen | Shown |
|---|---|---|
| Replaced / amended (REPLACE, MODIFY, OTHER, AMENDS) | old and new text; without old text the new text; without new text the amending act's sentence | Qwen's explanation of the difference; remark "Alter/Neuer Wortlaut nicht verfügbar" |
| Added (ADD, INSERT) | new text (or the amending act's sentence) | Qwen's explanation of what is new |
| Deleted / repealed (DELETE, REPEAL, REPEALS) | nothing | "Gestrichen." / "Aufgehoben." |
| Corrigendum | wrong and correct text | Qwen's explanation |
| Whole-act change (exception, postponement …) | the amending act's sentence and final articles | Qwen's explanation |
| Implicit repeal | nothing | review queue |
| No text at all | nothing | "Kein Wortlaut verfügbar." |

- Qwen returns only `{"explanation": "..."}`. No headline, significance, importance or to_confirm.
- Nothing is checked or rejected; what Qwen writes is kept. Missing things are written as remarks.
- The date of each change comes from the database (`effective_from`, with its source). The exact old and new words stay as the backend shows them.
- One request per single change. A change without an answer is asked again at the next start; the others are kept (`parts\<item>\<n>.json` holds what was sent and the answer).
- Every change routed to a department (DIRECT or INDIRECT) goes to its inbox.

The v1.x sections below are history.

## What changed in v1.7 (after pilots v5 and v6)

Built on the v1.6 code (per-change requests, review queue, `--item-ids`). Pilot results on the same 40 items: v5 explained 21, v6 explained 2 (24 failed, mostly because Qwen had to copy English words exactly into German fields), and v6 accepted one wrong subject (2014/24 Art. 13(b): EUR 221 000 called a threshold for "Werkeverträge"; point (b) is about service contracts).

| Brief point | What v1.7 does |
|---|---|
| 1. Python copies the evidence | Every passage has an ID per change and type (OLD O1…, NEW N1…, INSTRUCTION I1…, FINAL_ARTICLES F1…, WRONG/CORRECT W/R for corrigenda, PLACE_CONTEXT C1). The stable reference is `<change id>:<id>`. Qwen writes German values and only **selects IDs** (`before_evidence`, `after_evidence`); Python fills the quotations word for word. Numbers and dates of each value must stand in the selected passages. With old and new wording, the sentences that differ are marked; only changed passages can be selected (the guided-JSON enum leaves the unchanged ones out), so an unchanged neighbouring sentence can never support a difference. |
| 2. Subject stays with the value | The export adds the article heading and the current wording of the exact place (`point_context`). For a point, Python reads what the point is about ("(b) service contracts …") and sends it as `subject_of_point`. Headline and facts may only name the contract category of the point's own subject; the sentences may also name categories the point's wording mentions. Without the point's wording, no category may be named: "Punkt (b)". Tested for Art. 13 (a) and (b) separately. |
| 3. Each evidence situation | `BOTH`, `NEW_ONLY`, `INSTRUCTION`, `REMOVAL_OLD`, `REMOVAL_INSTRUCTION`, `ACT`, `CORRECTION`, each with its own rule in the request and its own schema (no "before" without old wording, no "after" for a deletion). A documented deletion or repeal may be supported by the instruction alone. Implicit repeals stay in scope review. Dates: Python adds the change's date (with its source), the amending act's entry into force and its date, never mixed; Qwen may only add transposition, transition, reporting or other deadlines from the instruction or final articles, with a kind that the cited passage supports. |
| 4. Partial successes, diagnosable failures | Each change is its own part. Good parts are kept; the item is `PARTIAL` and lists the missing parts; the next start asks only the failed parts again. Every part file (`parts\<item>\<n>.json`) holds the evidence sent, the first and the corrected answer, and the exact error. `summary.json` → `outcomes` (EXPLAINED, PARTIAL, FAILED, MISSING_EVIDENCE, SCOPE_REVIEW, OPEN), `parts`, `failed_part_kinds` (COMPARISON_WITHOUT_OLD, UNSUPPORTED_NUMBER, SUBJECT, DATE, EVIDENCE …) and `failed_part_files`. |
| 5. Replay and comparison | Same 40 IDs (`config\eurlex_changes_pilot_v4_ids.txt`) into a new folder; `tools\compare_change_pilots.py` shows v5 / v6 / v7 side by side (`compare.csv`). Regression tests: Art. 13(a)/(b), 2004/37 Art. 14 paragraphs 1 (old and new) and 8 (new only), an explicit repeal, a new-only change, a partially failed annex. |

Cost: one request per single change. On the v2 export that is about 18,400 requests instead of 6,600 (largest request about 18,000 characters, within the model limit).

## What changed in v1.4 (review)

| Review point | What v1.4 does |
|---|---|
| Old and new wording must be the same as in the UI | The export now uses the **backend's own matcher** (`app\services\eurlex_wording.py`, the same code behind `GET /api/v1/acts/{id}/changes?include_wording=true`): first the reviewed comparison (`act_wording_comparisons`), else `match_stored()`. The file is loaded read-only from the backend folder (`--backend-dir`); nothing in the backend changes. My own version matching is removed. Every change carries the backend's `comparison_status` (`REVIEWED_MAPPING`, `MATCHED_STORED_EVIDENCE`, `PREVIOUS_WORDING_UNRESOLVED`, `NEEDS_MATCH_REVIEW`, `TARGET_UNRESOLVED`, `NOT_MAPPED`) and its `change_id`, so it can be checked against the UI one by one. The matcher also gives the exact paragraph or point, not the whole article. |
| Only the instruction available | Qwen explains what the instruction says without comparing; each change line shows "Alter Wortlaut nicht verfügbar" / "Neuer Wortlaut nicht verfügbar". |
| Roles independent of document type, from all relationships | `roles.json` is built from **all** operational relationships in the database (not the filtered export): independent badges *Ändert andere Rechtsakte*, *Wurde geändert*, *Wurde berichtigt*, plus *Berichtigung* for corrigenda. An act can carry several. |
| One date per change, with its source | Each change keeps its exact place (for example "Article 7 paragraph 2"), its own date and the date's source (`CHANGE`, `ANNOTATION`, `RELATIONSHIP`, `ENTRY_INTO_FORCE`, `MISSING`). The explanation of one article lists every change with its own date; `dates_differ` flags articles whose changes apply from different dates. |
| "Why it matters" only on a reliable match | Written only when the change is matched to a classified DIRECT/INDIRECT provision (changed provision, changing provision, or for corrigenda the corrected provision), or to the whole act for a whole-act change or corrigendum. Routing only via the changing act is too broad: no importance, and the change is listed in `to_screen.csv` for screening the new text. The business areas from `business_anchors_de.yaml` for the provision's legal area are given as context. New field `to_confirm`, for example "Ob ein Max-Bögl-Werk unter die genannte Kategorie fällt, ist noch zu bestätigen." |
| Corrigenda | Routed to the corrected article when the correction names it (`CORRECTED_PROVISION`). |
| Very large annexes (hundreds of changes) | At most 25 changes and 12,000 characters of wording per request; Qwen says the provision is changed in many places. |

What I did not do: a read-only comparison against the running UI. With the same matcher on the same database rows, the wording is the same by construction; to check, open 3–4 acts in the UI and compare old/new text with `review.csv` by `change_id`.

## What changed in v1.3 (second pilot)

**New, as asked:**
- **Role of every act** (from all relationships, no AI): *Basisrechtsakt* (only changed), *Ändernder Rechtsakt* (only changes others), *Basisrechtsakt und ändernder Rechtsakt* (both), *Berichtigung* (corrigendum document), *Berichtigungsrechtsakt* (an act that only corrects others). On the act page and on every card, for the changed and the changing act. Export v2 counts: 1,587 base, 1,484 amending, 618 both, 1,722 corrigenda, 2 correcting acts.
- **"Warum wichtig für Max Bögl"** (`importance`): one or two sentences per change, written from the classification of the affected article (legal area, subject, reason, departments). Only for changes routed to a DIRECT or INDIRECT article; otherwise empty. No advice, no tasks (tasks are the next phase). Corrigenda get it too.

**Fixed after the second pilot:**

| Problem | Fix |
|---|---|
| Older acts are cited "Regulation (EU) No 321/2013" (number before year): the changing act's passage was not found (6,334 instructions missing) | Both forms are recognised, also two-digit years ("89/391/EEC", "No 1408/71") |
| An article number inside the quoted new text was taken for the changed place (321/2013 Art. 4 got the text of point 1.3) | Quoted new wording is ignored when finding the place; the point must itself change it ("in Article 4", "Article 4 is replaced", "the following Article 4a is inserted") |
| Several quoted articles in one instruction: Article 11b got the text of Article 1 | Only the quote that starts with the changed article is taken |
| "Replaced by the text in the Annex to this Regulation": no new wording | The changing act's annex is taken as new wording (or as instruction when it holds the amendments) |
| Version text did not match the instruction | When the quoted new wording is not in the stored version, the quote is used |
| Removed provision explained as a "new duty" (2018/1999 Art. 51) | A removed provision with only the old text may not say anything is new |
| Additions and deletions together with only the old text (2023/1586 Annex II) | No Qwen: "Der neue Wortlaut liegt nicht vor." |
| Long annex marked EDITORIAL from a few acronyms, before = after | Shortened long texts can never be EDITORIAL; facts with the same before and after are rejected |
| "verlängert" rejected for a validity extension | Allowed when the changing act's title speaks of validity |
| EUR-Lex not reachable from your PC (every fetch failed; reason unknown) | Second official source added (Publications Office, publications.europa.eu). After 25 failures in a row fetching stops for that run, and the reasons are in `export_summary.json` → `fetch_errors`. Hand test: `tools\eurlex_text_fetch.py` (see Commands) |
| "Rücktritt der Richtlinie" | Fixed words in the prompt: aufgehoben, gestrichen, ersetzt, eingefügt |

## What changed in v1.2 (pilot findings)

| Pilot problem | Fix in v1.2 |
|---|---|
| Corrigenda: the database held the corrected act's own text, not the corrigendum (1,719 of 1,723 had no usable text). Qwen then wrote general sentences ("ein Datum war falsch") | Only real corrigendum text is used ("Corrigendum/Berichtigung", "for/read", "Anstatt/muss es heißen", "… erhält folgende Fassung"). Otherwise it is read from EUR-Lex (`--fetch-missing`), **German first, then English**. A corrigendum that exists in neither language only concerns other language versions: shown as a line, never in the inbox. Without text: a line, no Qwen. Qwen must name the concrete wrong → correct values |
| Only the old wording known, operation "replace": Qwen described the old text as a "new duty" | No new wording and no instruction → no Qwen; a line "… ersetzt durch …, ab …. Der neue Wortlaut liegt nicht vor." (`NEW_WORDING_MISSING`) |
| Only the new wording known: Qwen invented the old one ("früher mussten nur …") | Without old wording "before" is blocked by the schema, and words like früher, bisher, nicht mehr, erweitert, gesenkt are rejected (except for extensions/postponements named in the changing act's title) |
| Dates of entry into force were rejected as "not in the text" (3 failures) | The changing act's date, entry into force, application and the change date are sent as `dates_known` [M1]; "after" values may also come from the final articles |
| "the words ‘X’ are replaced by ‘Y’": the old value was rejected | The instruction may give "before" when it names the replaced words |
| Huge annex replaced: the change lay after the first 12,000 characters, so it was marked EDITORIAL | Long texts are compared in full first; only the changed sentences and their neighbours are kept |
| Old wording almost never found (194 of 25,501): the original version has no date | The original version counts from the act's date; a version is only used when no other change of the act falls between it and this change |
| Art. 51 of 2018/1999: the changing act's own Article 51 (hydrogen plans) was taken for the change | Without the act number only amending annexes are searched, and only points that change something; an amending act that changes several acts is split per act |
| "Annex", "Annex TXT", "Annex P A TXT", "Title", "" not readable | "Annex" without number = the act's only annex; other unreadable places of one changing act form one item with the act's whole amending part |
| Changing act without text in the database (1,323 cases) | Read from EUR-Lex with `--fetch-missing` (English, cached in `runs\eurlex_fetch_cache`) |
| Qwen commented on relevance ("für Arbeitnehmer der Baubranche") | Forbidden in the prompt |

Correct failures that stay failures: "1,5" (32019R1009 Annex II) and "48" (32009R0987 Art. 67) were invented; the checks stopped them as intended.

## What it does

| Step | Script | Qwen? | Result |
|---|---|---|---|
| 1 Export | `tools\export_eurlex_changes.py` (+ `tools\eurlex_text_fetch.py`) | no | `runs\eurlex_changes_export_v5\records.jsonl`: one record per change, read from the backend database (read-only); missing corrigenda and changing acts read from EUR-Lex |
| 2 Explain | `explain_eurlex_changes.py` | yes | headline, what changed (2–3 sentences), facts before → after, dates, significance |
| 3 Changing acts | same script | yes | a short paragraph per changing act |
| 4 Export for the UI | same script | no | inbox cards per department, preview pages, review list |

**Which changes:** every relationship "changing act → changed act" of a type that changes text or application (AMENDS, REPLACES, REPEALS, CORRECTS, DEROGATES, SUSPENDS, EXTENDS…, DEFERS_APPLICATION, …) where the changing act **or** the changed act is in the classification results, and the changing act is from 2015 or later (`--since`). Relationships that only add rules next to an act (COMPLETES, IMPLEMENTS, …) are listed in `links.json`, not explained.

**Three kinds of records:**
- **PROVISION**: one change to one article or annex (add, insert, replace, delete …). All changes of one changing act to the same article are explained together.
- **ACT**: a change of the whole act without provision detail (whole act repealed, application deferred …).
- **CORRIGENDUM**: a correction of the published text. Wrong and correct text are taken by code from "for: '…', read: '…'" (English) or "Anstatt: „…“ muss es heißen: „…“" (German); whole replaced parts too. It is labelled "Berichtigung" and has no new date.

**Old and new wording** (column `wording`):
- `FULL`: the wording before and after the change, exactly as the UI shows it (reviewed comparison or the backend's matcher);
- `NEW_ONLY`: only the new wording (for example a new article);
- `OLD_ONLY`, `INSTRUCTION_ONLY`: only that is available;
- `NONE`: no text at all. Not sent to Qwen; a line built by code, listed in `missing_text.csv` with the reason.

Qwen also gets the changing act's final articles (entry into force, application, transposition, transition), so the dates of the change are complete.

**Checks** (one correction request, otherwise the item is marked failed and retried at the next start):
- a "before" value must stand in the old text, an "after" value in the new text or the instruction; numbers and dates follow the same rules as the summaries (`eurlex_summary_checks.py`);
- without old text, "before" must stay empty (schema) and no comparison words are allowed;
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
tools\eurlex_text_fetch.py
tools\backend_wording.py
tools\compare_change_pilots.py    (new in v1.7)
config\eurlex_changes_pilot_v4_ids.txt   (the 40 pilot items)
explain_eurlex_changes.py
prompts\eurlex_change_explain.md
prompts\eurlex_corrigendum_explain.md
prompts\eurlex_change_act_summary.md
tests\test_export_eurlex_changes.py
tests\test_explain_eurlex_changes.py
CHANGES_GUIDE.md
```

Needs: summaries v4.4 in place (`summarize_eurlex_provisions.py`, `eurlex_summary_checks.py`), the classification `runs\eurlex_classification_v5\results.json`, `DATABASE_URL` in `.env` (as for the corpus export), and the backend folder (the one with `app\services\eurlex_wording.py`). No backend file is changed.

## Commands

Run after the summaries retry has finished; both use the same Qwen endpoint.

```
cd C:\Users\sthirumalair\AI-Experiments

# 0. find the backend folder (the one that holds app\services\eurlex_wording.py); prints it
$f = Get-ChildItem C:\Users\sthirumalair\project -Recurse -Filter eurlex_wording.py -ErrorAction SilentlyContinue | Select-Object -First 1
$env:LR_BACKEND_DIR = $f.Directory.Parent.Parent.FullName; $env:LR_BACKEND_DIR

# 1. code tests, no Qwen and no database needed - must end with "Ran 30 tests" and "OK (skipped=2)"
& .\.venv\Scripts\python.exe -m unittest tests.test_export_eurlex_changes tests.test_explain_eurlex_changes

# 2a. can this PC reach EUR-Lex? Prints what each of the two sources answered
& .\.venv\Scripts\python.exe tools\eurlex_text_fetch.py "32016R0185R(01)" DE

# 2b. new export into runs\eurlex_changes_export_v5 (database read-only; wording by the backend's matcher,
#     plus article heading and the current wording of each exact place)
#     with --fetch-missing only if 2a ended with "Result: OK"; then the first run takes 1-2 hours
& .\.venv\Scripts\python.exe tools\export_eurlex_changes.py --since 2015 --fetch-missing

# 3. what would be explained and who would get it
& .\.venv\Scripts\python.exe explain_eurlex_changes.py --count-only

# 4. replay of the same 40 items into a new folder, then the comparison with v5 and v6
& .\.venv\Scripts\python.exe explain_eurlex_changes.py --item-ids config\eurlex_changes_pilot_v4_ids.txt --output-dir runs\eurlex_changes_pilot_v8
& .\.venv\Scripts\python.exe tools\compare_change_pilots.py v5=runs\eurlex_changes_pilot_v5 v7=runs\eurlex_changes_pilot_v7 v8=runs\eurlex_changes_pilot_v8 --ids config\eurlex_changes_pilot_v4_ids.txt

# 5. full run (only after the replay was reviewed) into runs\eurlex_changes_v8
& .\.venv\Scripts\python.exe explain_eurlex_changes.py
```

The two skipped tests need a test database (`LR_TEST_DATABASE_URL`); they ran on a local PostgreSQL before delivery. Do not point them at the production database.

Options: `--celex 32010L0075` (only changes of these acts, export and explain), `--since 0` (all years), `--language en`, `--workers 4`. Ctrl+C stops, the same command continues. A new export needs a new `--out` folder; changed prompts need a new `--output-dir`. Older folders stay as they are; v1.7 does not use them. `--backend-dir` can be given instead of `LR_BACKEND_DIR`.

If 2a fails, send me its output (it names the reason: proxy, certificate, bot check). Run 2b without `--fetch-missing` meanwhile: corrigenda without text then become lines without Qwen.

## What to send after step 2

`runs\eurlex_changes_export_v5\export_summary.json` (and `roles.json`). It shows records per kind, relationship and operation, the gaps (`wording_*`, `target_not_resolved`, `instruction_not_found`, `same_text_in_versions`, `corrigenda_text_*`, `changing_text_*`) and `fetched_from_eurlex`. New: `comparison_*` counts (how many changes the backend matcher could match, as in the UI) and `roles`. Compare with v2 (`export_v2`): `instruction_not_found` (6,334) should drop clearly; with EUR-Lex reachable, `corrigenda_without_parsed_corrections` (1,721) too. `wording_FULL` may be lower than in v2 (3,697): the backend matcher is stricter, and that is what the UI shows.

## Pilot: when is it good enough for the full run

Open `runs\eurlex_changes_pilot_v7\review.csv` and `compare.csv` next to EUR-Lex (`preview\index.html` shows old and new text behind "Wortlaut vorher / nachher").

| Check | Go |
|---|---|
| Every item has an accounted outcome (`summary.json` → `outcomes`, no `OPEN`) | all 40 |
| Known subject swap (2014/24 Art. 13 (a)/(b)) | fixed: no category named that the point does not state |
| An accepted explanation invents a change, date, threshold or company obligation | **0** |
| `FAILED` + `PARTIAL` | note the `failed_part_kinds`; open 3–4 part files for the most frequent kind |
| Missing evidence (10) and scope review (4) | may stay queued while the text is unavailable |
| An invented difference (something the texts do not show) | 0 of 40 |
| A real change of a duty, threshold, deadline or addressee marked EDITORIAL | **0** (this hides a change) |
| Significance right | at least 36 of 40 |
| Dates right and complete (applies from, transposition, transition) | at least 36 of 40 |
| Corrigenda: labelled Berichtigung, wrong → correct right | all |
| Routing: the department should see this change | note the wrong ones (routing question) |
| "Warum wichtig für Max Bögl" (column `importance for Max Bögl`): fits the change and the department, nothing invented, no site or product claimed | at least 36 of the rows that have one |
| Dates per change (column `changes with date (source)`) | every change has the right date; `MISSING` noted |
| Old/new wording equals the UI for 3–4 acts (by `change_id`) | all |

If a check fails: note 3–5 rows, change the prompt, run the pilot again into a new folder.

## Output folder (`runs\eurlex_changes_v8`)

- `inbox\<department>.json`: cards for the UI, one per changed act, with its changes (newest first)
- `preview\index.html`: the same as pages
- `review.csv`: one row per item, with columns to fill in: explanation correct, missing or wrong, significance right, comment
- `summary.json`: outcomes per item and per part, failure kinds, counts per kind, significance, wording and route
- `parts\<item>\<n>.json`: one file per single change: status, evidence sent, every answer, the error
- `compare.csv` (from `tools\compare_change_pilots.py`): the runs side by side
- `missing_text.csv`: items shown as a code line, with the reason (`NO_TEXT`, `NEW_WORDING_MISSING`, `CORRIGENDUM_TEXT_MISSING`, `OTHER_LANGUAGES_ONLY`)
- `to_screen.csv`: changes without a department (`NOT_SCREENED`) and changes routed only via the changing act (`CHANGING_ACT`): their new text should be screened by the provision-relevance step
- `acts\<changing act>.json`: the paragraph about each changing act
- `act_pages\<celex>.json`: what the act page shows (see below)

Facts in v1.7: `facts[{what, before, after, place, change_id, before_evidence[{id, ref, type, place, text}], after_evidence[…]}]` — the evidence texts are copied by Python. Dates: `dates[{what, date, source}]` with source `CHANGE`/`ANNOTATION`/`ENTRY_INTO_FORCE`/`ADOPTION` (added by Python) or `EVIDENCE` (added by Qwen from a cited passage). Items also carry `outcome`, `parts` and `missing_parts`.

Card fields: `celex, title, role{changes_others, was_changed, was_corrected, corrigendum, labels[]}, eurlex_url, pdf_url, changes[{kind, relation, label, operations, provision, changing{celex, title, date, role, eurlex_url, pdf_url}, effective_from, dates_differ, wording, headline, text, importance, to_confirm, facts[{what, before, after, passages, quotes}], dates[{what, date}], changes[{change_id, provision, operation, operation_label, effective_from, effective_source, comparison_status, old_text, new_text, wording_notes[]}], significance, level, route, old_text, new_text}], changing_acts{celex: [sentences]}, note`. The inner `changes` are the single changes with the UI's wording, matched by `change_id`.

## Act page

For every act with changes, `act_pages\<celex>.json` holds what the act page shows below the summaries:

- `role`: independent flags and their badges, e.g. `{"changes_others": true, "was_changed": true, "was_corrected": false, "corrigendum": false, "labels": ["Ändert andere Rechtsakte", "Wurde geändert"]}`; show every label as a tag next to the title.

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
3. below it **"Warum wichtig für Max Bögl"**: the `importance` sentences, then `to_confirm` in italics (both hidden when empty);
4. one line per single change: place, operation, "gilt ab …", and "Alter/Neuer Wortlaut nicht verfügbar" where it applies;
5. behind a toggle: old and new wording per change (the same as the backend's comparison view, so the existing view can be linked by `change_id`);
6. the article's normal summary (current law) stays below.

## Known limits (v1.7)

- English versions of the texts (the backend stores EN); corrigenda German or English.
- For very long wording only the changed passages and their neighbours are sent.
- The subject check knows the procurement categories (works, services, supplies). Other wrong subjects (for example a wrong product group) are not caught by code; the review of the pilot is there for them.
- `point_context` is the current wording of the place (newest stored version). It is only used to know what a point is about, never as old or new wording.
- `NOT_SCREENED` changes are listed, not screened yet: screening their new text with the provision-relevance step is the next step if the list is long.
- Old and new wording come only from the backend's matcher (as in the UI). Where it finds none, the change is explained from the instruction without comparison. Improving the matcher is a backend topic; the AI step follows it automatically.
- The backend file `eurlex_wording.py` is read from the backend folder at every export; its SHA-256 is in `export_summary.json`, so a later backend update is visible.
- The EUR-Lex reader recognises articles and annexes by their heading lines ("Article 5", "ANNEX II"). Check a few `EURLEX_EN` records in the pilot.
