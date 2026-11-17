# Legal Radar – Summaries step (guide)

Status 2026-11-17 (v4). Step after classification. Script: `summarize_eurlex_provisions.py`.

## What it does

Three summaries, all written by Qwen and checked by code:

| Summary | Length | Written from |
|---|---|---|
| Provision | concrete headline + small paragraph (2–4 sentences) + up to 4 key points of 1–2 sentences (who / duty / deadline / condition); further points are kept under "Weitere Punkte" | only the legal text of that provision |
| Act | small paragraph, 3–5 sentences with concrete facts | title, act subject, provision summaries |
| Department | 2–4 points of 1–2 sentences each (why relevant / why your department / read first / please confirm) | saved relevance and classification reasons + provision summaries |

Code checks (one correction request, otherwise the item is marked failed and retried on the next start):

- every key point names the passage it comes from;
- **numbers:** every number must occur in the text with the same value. Other notation is fine (1.000 / 1,000 / 1 000, 1.5 / 1,5), another value is not (1.5 MW is never 15 MW);
- **dates:** day, month and year must match a date in the text (31.12.2027 = 31 December 2027; 31.01.2027 is rejected). A date without day (Dezember 2027) must match month and year;
- **units:** a number with a unit (MW, kWh, TJ, t, kg, %, EUR …) must stand with the same unit in the text;
- **a number in a key point must stand in the passage that point names**, not just somewhere in the provision.

## Long provisions

A provision longer than one request is cut into parts.

- The parts that the relevance step kept are read, at most 3.
- A part that is still too long for the model is cut into smaller pieces, and **every piece is read**. Before v4 only the first piece was read.
- With more than one part, a last request writes one headline and paragraph for **all parts read**. If that request fails, the card says which part the paragraph covers.
- Whatever was not read is listed on the card and in `review.csv` (column `parts`): not relevant in the screening, limit reached, or too long.

## Files (copy into `C:\Users\sthirumalair\AI-Experiments`)

- `summarize_eurlex_provisions.py`
- `eurlex_summary_checks.py` (new in v4: the number, date and unit check)
- `prompts\eurlex_provision_summary.md`, `prompts\eurlex_merge_summary.md` (new in v4), `prompts\eurlex_act_summary.md`, `prompts\eurlex_department_summary.md`
- `tests\test_summarize_eurlex_provisions.py`

Needs the finished classification in `runs\eurlex_classification_v5` (`results.json`, `acts.json`). Nothing existing is changed.

If you already started a summaries folder with an older version, the script refuses to continue in it (the prompts changed). Use a new `--output-dir`.

## Commands (PowerShell, in the project folder)

```
# 1. code test, no Qwen needed – must end with OK (20 tests)
& .\.venv\Scripts\python.exe -m unittest tests.test_summarize_eurlex_provisions

# 2. count only, nothing is sent
& .\.venv\Scripts\python.exe summarize_eurlex_provisions.py --count-only

# 3. pilot: 50 DIRECT provisions plus one act with long annexes
& .\.venv\Scripts\python.exe summarize_eurlex_provisions.py --sample 50 --levels DIRECT --celex 32010L0075 --output-dir runs\eurlex_summaries_pilot

# 4. full run, DIRECT first, then everything (same folder, it continues)
& .\.venv\Scripts\python.exe summarize_eurlex_provisions.py --levels DIRECT
& .\.venv\Scripts\python.exe summarize_eurlex_provisions.py
```

`--celex 32010L0075` (Industrial Emissions Directive, long annexes and tables) adds all its DIRECT provisions to the pilot, so the long-provision handling is tested. Any other act with long annexes works too.

Options: `--language en` (default `de`), `--workers 4`. Ctrl+C stops, the same command continues.

## Output folder (`runs\eurlex_summaries_v1`)

- `preview\index.html` – open in a browser; one page per department, as the UI should look
- `inbox\<department>.json` – the data for the UI
- `review.csv` – one row per provision, for checking by hand
- `summary.json` – counts and failures

`review.csv` columns: celex, provision, level, headline, overview, bullets, more_bullets, parts, domain, departments, text_start, then to fill in: **summary correct (yes/no)**, **missing or wrong**, **department right (yes/no)**, comment.

## Pilot: when is it good enough for the full run

Open `review.csv` in Excel and compare each row with the legal text (the preview page shows the original passage under each point).

| Check | Go |
|---|---|
| `provisions_failed` in `summary.json` | at most 2 of 50 |
| Wrong or invented fact (duty, person, deadline, threshold not in the text) | 0 of 50 |
| A date, number or unit changed | 0 of 50 |
| An important duty, deadline, condition or exception missing | at most 5 of 50 |
| Long provisions (rows with `parts` filled): paragraph covers the parts read, nothing important missing from the parts read | all of them |
| Language reads correctly, legal terms not distorted | at least 45 of 50 |
| Department summary explains why this department, without new facts | 9 of 10 cards looked at |
| Department right (the provision reached a department that should read it) | note the wrong ones; this is a routing question, not a summary error |
| `cards_without_department_summary` | 0 after a second start |

If a check fails: note 3–5 example rows, change the matching prompt, run the pilot again into a new folder. Do not start the full run with a failed check.

## UI

Inbox per department, one card per act:

1. Card header: act title, CELEX, counts "2 direkt · 5 indirekt relevant", links EUR-Lex and PDF.
2. Act summary (small paragraph, 3–5 sentences).
3. Box "Warum Sie das bekommen" = department summary (up to 4 points, each with the articles it refers to).
4. Provision rows, DIRECT first. Closed: locator, headline, tag **Direkt relevant** (DIRECT) or **Indirekt relevant** (INDIRECT). Open: the paragraph (`overview`), then the key points with the original passage under each; "Weitere Punkte" (collapsed) when `more_bullets` exists; a grey line with the parts read and not read when `parts_total` exists; yellow line "Bitte bestätigen" when `to_confirm` is filled; domain name.
5. Footer: "KI-generierte Zusammenfassung. Maßgeblich ist der Originaltext."
6. Buttons per provision: *Relevant – bestätigt*, *Nicht für uns (andere Abteilung)*, *Nicht relevant*.

**Why not "Aufgabe":** DIRECT means the AI found the provision directly relevant. It is not yet a task. Show **Aufgabe** only after a person in the department has confirmed that it applies and taken it on (button "Relevant – bestätigt").

Fields in `inbox\<department>.json` → `cards[]`: `celex, title, eurlex_url, pdf_url, act_summary[], department_summary[{kind, text, provisions[]}], direct, indirect, note, provisions[{locator, provision_id, level, role, domain, domain_title, headline, overview, bullets[{kind, text, passages, quotes, part}], more_bullets?, parts_read?, parts_total?, not_read?[{part, reason}], overview_covers?, why_relevant, indirect_path, to_confirm, check}]`.

## Known limits

- Very long provisions: at most 3 kept parts and 8 requests per provision; everything not read is listed on the card.
- The checks find changed numbers, dates and units, not wrong meaning – the hand check in the pilot stays necessary.
- The department summary inherits the routing of the classification. A wrong department is fixed in the routing table, not in the summary.
- Provisions whose domain has no department yet land in `inbox\no_department_yet.json` without a department summary.
- Department summaries for more than 15 provisions use the first 15 (DIRECT first).
