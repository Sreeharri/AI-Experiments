# EUR-Lex provision relevance pilot

Apply this v6 update over the v5 pilot in the root of `AI-Experiments`,
alongside `enrich_eurlex.py`. Merge the directories and replace the files
provided in this ZIP. Then delete `eurlex_provision/scenario_checks.py`;
nothing uses it any more. The pilot uses the existing Qwen client,
configuration, pinned act manifest, enrichment code and the v4
`eurlex_provision/thresholds.py`.

## Inputs

| Path | Input |
|---|---|
| `output_db/*_enriched.json` | The three enriched acts from the completed PostgreSQL pilot |
| `runs/eurlex_full_v10/relevance_export/direct.json` | Document-level DIRECT results keyed by CELEX |
| `runs/eurlex_full_v10/relevance_export/indirect.json` | Document-level INDIRECT results keyed by CELEX |
| `config/business_anchors_en.yaml` | Complete English anchor catalog, including role descriptions and boundaries |
| `config/eurlex_provision_scenarios.json` | Three explicitly synthetic scenarios and the pilot assessment date |
| `config/eurlex_provision_expected.json` | Expected results for the three samples, used only to grade a run |
| `.env` | Existing Qwen settings, including `QWEN_BASE_URL` and the model ID when required |

The runner looks up each pilot CELEX in both exports and keeps these matches
in the audit files. A missing act is reported and recorded as JSON null.
Both input files must exist. Qwen receives the scenario, provision evidence,
and anchor catalog for each provision assessment.

Use a separate scenario JSON with the same shape and pass `--scenarios` to
supply real case facts. Set its `assessment_date` and mark those scenarios
`SUPPLIED_CASE`. Keep unknowns explicit. Each scenario holds exactly `id`,
`evidence_status` and `facts`, because the whole scenario is sent to Qwen.
The runner stops if a scenario contains anything else, such as expected
labels. You can also set the date with
`--assessment-date YYYY-MM-DD`. Source versions come from `pilot_acts.json`;
the assessment date alone gives no assurance that an original text reflects
all later changes.

## Run from the VS Code PowerShell terminal

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-provisions.txt
.\.venv\Scripts\python.exe .\screen_eurlex_provisions.py samples --prepare-only
.\.venv\Scripts\python.exe .\screen_eurlex_provisions.py samples
```

The prepare command writes the three sample inputs without calling Qwen. The
sample run assesses Regulation Article 2, Directive Article 6(2), and Decision
BAT 17 with the governing Article 1. The full run assesses Article 6 as a
complete article, including both dated sentences of paragraph 2.

After reviewing the sample results, run the complete three-act pilot.

```powershell
.\.venv\Scripts\python.exe .\screen_eurlex_provisions.py pilot
```

The full run plans 225 results with the bundled manifest. It produces one
overall result per article, annex, and data table. It uses BAT units and other
sections inside the decision's annex and assesses table rows in batches with
their headers, merged-cell context and footnotes. Annex and table audit records
retain their own source IDs and citations.

## Branch statuses and the relevance level

Qwen gives every branch one status, after writing its reason:

| Status | Meaning |
|---|---|
| `APPLIES_TO_COMPANY` | Regulates, or sets the scope for, the company's own stated activity, product or installation |
| `APPLIES_TO_OTHER_ACTOR` | Regulates another actor, such as a supplier, on whom the company concretely depends |
| `OUTSIDE_SCENARIO` | Excluded by a scenario fact: product, activity, location or threshold |
| `CONTEXT_ONLY` | Introductory wording or a table header with no effect of its own |
| `NEEDS_REVIEW` | A missing fact or passage could change the branch |

The runner, not Qwen, works out the level and review status from these
statuses. Any company branch gives DIRECT. Otherwise an open branch gives no
level, a branch on another actor gives INDIRECT, and anything else gives
IGNORE. `missing_evidence` lists the open branches and their reasons.

Qwen picks legal citations from the locators in the evidence and scenario
facts by ID (`Fact 1`, `Fact 2`, …). The response format only allows those
values. Each branch's own locator is added to its citations automatically.

## Context requests and evidence

The runner starts with target text, relevant definitions and scope, explicit
links, governing provisions for annex material, and selected recital candidates.
It separates precise table citations from annex-wide table candidates. The
model can request up to three exact locators per round, for up to two retrieval
rounds per batch. The runner uses the pinned act and any supplied supplementary
excerpts, then sends the original target back with the extra evidence. The
audit records retrieval attempts and passages cited by the model.

A separate Qwen scenario check runs with thinking on (use
`--review-thinking off` to compare). Each issue it raises names a branch, the
fact ID or legal locator it relies on, and the status it proposes. An issue
blocks the draft only if applying the proposed status would change the level
or review status. Other issues and all notes are kept in the audit. A blocked
draft gets one correction round with the issues and the previous draft. If the
second check still blocks, only the disputed branches become NEEDS_REVIEW; the
audit keeps Qwen's original status as `draft_status`.
If Qwen returns an invalid decision twice, the affected target is saved for
review and the run continues. A scoring reply cut off by the output limit
is retried with smaller branch groups. A single branch that still truncates
or a malformed review reply is held for review. The audit records the
model replies, finish reasons, token usage when supplied by Qwen, and explicit
thermal input threshold checks. Follow-up verification notes are audit-only
and do not by themselves prevent export.
API failures still stop the run so it can resume after the connection recovers.

If you have verified external legal text or a transcription of a formula,
you can provide it locally using `--supplements .\path\excerpts.json`.
The file is a JSON array. Each entry has `instrument`, `locator`,
`source_version_id`, `source_url`, and `text` strings. Use the exact instrument
and locator shown by the unresolved reference. For formula transcription, use
a source-verifiable locator, and identify its source version. For same-act
formula IDs, see the workflow below. External passages and formula images
remain visible as review candidates when their text is missing.

For a same-act formula, put the act's CELEX in `instrument` and the image
locator from `formula_images_requiring_pdf_check` in `locator`. The runner
retrieves it only if its version ID matches the enriched act. It uses the
transcription as evidence for the original target.

## Outputs and resume

| Run | Output directory |
|---|---|
| Sample inputs | `runs/eurlex_provisions_samples_v6_prepared` |
| Sample results | `runs/eurlex_provisions_samples_v6` |
| Complete pilot | `runs/eurlex_provisions_pilot_v6` |

For a live run, open `relevance_export/direct.json`, `indirect.json`, and
`ignore.json`. Each file groups the records by CELEX and provision locator.
Each provision record contains exactly `level`, `reason`, and `area`.
`area` is a list of anchor IDs and is empty for IGNORE. The exports only
include assessments with review status COMPLETE. Open `audit/run_summary.json`
for counts and paths to the full per-target audit files, including pending
assessments. Listed paths are relative to the run directory. Each held target
has a `held_reason`: `OPEN_BRANCH` (Qwen marked a branch NEEDS_REVIEW),
`SCENARIO_CHECK` (the check still disagreed after the correction round) or
`INVALID_REPLY`.

Run the same command to resume after interruption or a Qwen error. Each
completed target is saved atomically. If an interruption occurs within a
target, that target restarts. When inputs, the model, or the code changes, use
`--output-dir .\runs\new_provision_run` for a new run. If the process was
forcibly terminated and left a `.running` lock, confirm that it stopped before
removing the lock from that output directory.

Run the bundled tests from the repository root.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_eurlex_provisions.py
```

## Grading against expected results

`config/eurlex_provision_expected.json` holds the expected level, review
status and branch statuses for the three samples. These labels are never sent
to Qwen and never change a result. After each run, `run_summary.json` gets a
`grading` section: how many targets and branches match, and every mismatch
with the expected status, the saved status and the scorer's original status.
The labels and the grading code are not part of the run fingerprint, so you
can correct a label and rerun the same command to regrade without new Qwen
calls. The runner stops if a label names a branch that is not in the pinned
text. Labels for targets the run does not plan (for example Article 6(2) in
the pilot, which assesses all of Article 6) are listed as not run.
