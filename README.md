# Legal Radar v6 — Qwen makes the final decision

## Exact flow

1. Python prepares the document evidence and full business profile, including not_roles.
2. Qwen returns ONE field: {"level":"DIRECT"}, INDIRECT, IGNORE or NEEDS_REVIEW.
3. DIRECT, INDIRECT or IGNORE is saved immediately, exactly as Qwen returned it.
4. ONLY Qwen's NEEDS_REVIEW triggers one second call, with additional available text
   and thinking enabled. The second response is saved, including NEEDS_REVIEW.

There are no Python quote checks, anchor/role checks, relevance overrides, separate
critiques or forced labels. Source/version warnings are context for Qwen and metadata
for the saved file; they do not overwrite its decision. No quotes/reasons are requested.

Python still handles reading files, preparing text, token budgets, HTTP requests,
decoding the answer and saving it. A network error, context overflow or malformed/
truncated response is recorded as failed, NOT invented as NEEDS_REVIEW. It does not
trigger a second relevance call. Temporary HTTP errors can use the client's normal
network retry handling. No automatic logic certifies Qwen's accuracy.

## Prompts

- prompts/document_relevance.txt: the short main relevance prompt.
- prompts/second_pass.txt: added only after NEEDS_REVIEW.

Prompt edits change the checkpoint fingerprint, so use a new --out directory.

## Update your current AI-Experiments folder

Back up the old code. Copy lr_ai, tools, prompts and tests from this ZIP into your
existing folder, replacing matching files. Keep your existing .env, data and config.
The included config files are copies of the earlier supplied business profile; use
those only if needed, keeping any newer approved company profile you have.

Old v5 tests check the removed validation behaviour. In an updated existing folder,
run ONLY the new workflow test filename rather than discovering obsolete test files:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_simple_flow.py" -v
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\inspection\eurlex_structured_10.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\sample10_v6
```

Use a NEW output directory. The CLI no longer accepts --critique or --max-rounds;
the rule is fixed: one initial call and one second call only after NEEDS_REVIEW.
Repeat the identical command to resume. Saved reviews/decisions are skipped; failed
requests are retried. Four workers are an initial setting, not measured server capacity.

The model decision is under decision.level. The rounds array preserves each response.
Evidence passages, source warnings, token counts and request metadata are retained for
inspection, but do not affect the saved relevance label after Qwen has answered.

## Fresh installation

Extract into a clean folder, then:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
New-Item -ItemType Directory -Force .\data\inspection
```

Enter your Qwen settings or copy your existing .env. Copy your existing
 eurlex_structured_10.json into data/inspection. Run the pilot above.
Python 3.10+ required. Never copy the placeholder .env.example over an existing .env.

## Input selection and request limits

Initial evidence: title, outline, up to nine article chunks from distinct article
paths, three recital chunks and two annex chunks. Additional chunks are supplied
on the second pass where available. Long articles can span multiple chunks.
The same full company profile is included in both calls; explicit assumptions
remain supplied as uncertain facts. This is document screening, not provision analysis.

The default total input cap is 34,000 tokens. First output cap: 1,024 tokens with
thinking OFF; second: 6,000 tokens with thinking ON. Input + output + 512 margin
stays within the confirmed server context. The tokenizer counts every request and
lower-priority excerpts are trimmed as necessary. Python does not alter model labels.

Duplicate boundaries are not silently repaired and future text is not a verified
current-law version; both are passed to Qwen as context. JSONL catalogue input from
acts.jsonl plus text_en.jsonl/text_de.jsonl remains supported. The evaluator is an
optional separate experiment and NEVER runs inside the classification workflow.

## Verification

15 automated tests passed, including immediate saving of all three resolved labels,
review then resolution, review twice, no data-warning overrides, no quote/anchor
requirements, not_roles preservation, protocol errors and second-pass extra evidence.

The actual supplied 10-act export passed through the real HTTP client and runner
against a LOCAL SIMULATED endpoint. All 10 reached it; exactly four scripted
NEEDS_REVIEW cases received a second call (14 calls total), with one still NEEDS_REVIEW.
The test responses are scripted; they measure request flow, NOT relevance accuracy.
The company Qwen endpoint was not contacted. See verification reports.

For a clean-folder installation, run all included tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

The optional local transport check contacts only a localhost test server:

```powershell
.\.venv\Scripts\python.exe .\tools\verify_offline_sample.py --input .\data\inspection\eurlex_structured_10.json --out .\runs\OFFLINE_SIMULATION_v6
```
