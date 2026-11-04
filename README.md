# Legal Radar document relevance — v5 complete package

Complete standalone code, Qwen client, business profiles, export tools and tests.
No private endpoint, password or API key is included.

## Use in your existing AI-Experiments project

Back up your code. Copy the included lr_ai, tools and tests folders into
AI-Experiments, replacing matching files. Keep your existing .env and data.
Included config files match the earlier supplied profile; preserve any newer
company-approved configuration you have edited since then.

Run from AI-Experiments in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe .\tools\document_relevance.py --input .\data\inspection\eurlex_structured_10.json --as-of 2026-11-04 --input-budget 34000 --context-limit 40960 --workers 4 --out .\runs\sample10_v5
```

Use a NEW output directory for v5. Repeat the identical command to resume;
failed requests are retried, saved decisions/reviews are skipped. Send the
complete runs/sample10_v5 folder after the live run. Four workers are a starting
setting, not measured server capacity. For a timing comparison use --workers 1
with a different output directory.

## Fresh separate installation

Extract into a separate folder and open PowerShell there:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
New-Item -ItemType Directory -Force .\data\inspection
```

Enter your existing Qwen settings. Copy eurlex_structured_10.json into
 data/inspection, then run the tests and pilot above. Python 3.10+ required.

## Changes

- All 10 supplied acts reach the model request path. Duplicate boundaries and
  future versions are recorded input warnings, not pre-model stopping conditions.
- Missing-text and rejected-source records also reach AI, but cannot yield an
  accepted decision from missing/rejected evidence; they remain review cases.
- Exact repeats within the same intended article/annex may be deduplicated.
  Identical text in different article numbers is retained. Differing duplicate
  fragments remain available. The database parser is NOT silently repaired.
- Boundary warnings travel with evidence. Affected stored article references are
  unverified. IGNORE cannot rely solely on a malformed ~duplicate path. An intact
  scope can support exclusion; damaged scope requires more evidence or review.
- Future text is screened for potential FUTURE relevance, preserving version
  dates and forcing future_or_transposition. It is not proof of current wording.
  Obtain an earlier version separately for current-law analysis.
- First response: 4,096 tokens. Second look: 6,000 tokens. Truncated answers retry
  using the same evidence and thinking OFF. Semantic unresolved cases may use
  thinking ON. Second truncation stays NEEDS_REVIEW with diagnostics.
- API/network failures remain failed, with saved request history.
- Short output: strongest 3 anchors, 2 roles, 4 exact quotes up to 400 characters,
  3 conditions, 600-character reason. These are supporting anchors, not a complete
  mapping of every affected department/domain.
- Company roles apply within business-anchor scopes. Explicit unconfirmed
  assumptions override generic roles. Unknown product activities remain conditional.
  Prompts cannot guarantee semantic correctness; validate with human labels.
- Four parallel workers, one file per act, bounded retrieval and optional critique.
  Critique uses the same model; it is not independent validation.

## Context and selection

Default TOTAL input: 34,000 tokens including instructions, business profile,
metadata, outline, feedback and excerpts. With your confirmed 40,960 context:

34,000 input + 6,000 output/thinking + 512 margin = 40,512 tokens.

The server tokenizer counts each request. Lower-priority excerpts are trimmed
when necessary. Unused space is not padded. CLI rejects a budget that leaves
insufficient second-look output room.

Initial selection: up to nine article chunks from distinct paths, three recital
chunks and two annex chunks. Subsequent retrieval is bounded. Long articles can
span chunks; the initial pass is not guaranteed to contain each whole article.
Scope selection and keyword ranking remain heuristics. Missing recitals alone do
not stop the pipeline. Exact quote matching proves fidelity, not legal reasoning.

## Catalogue and evaluation

--input .\data\eurlex supports acts.jsonl plus text_en.jsonl and/or text_de.jsonl.
Records are read individually using offsets, with German fallback. Export truncation
cannot be repaired from absent bytes. Existing export tools are included.

Evaluate independently labelled relevant AND irrelevant examples before scaling:

```powershell
.\.venv\Scripts\python.exe .\tools\evaluate_document_relevance.py --run .\runs\sample10_v5 --labels .\labels\dev.csv --max-miss-rate 0.05 --max-review-rate 0.15 --min-ignore-removal 0.70
```

These targets are example settings to agree beforehand, not measured results.
CSV: celex,label. Labels DIRECT, INDIRECT, IGNORE (IRRELEVANT accepted).

## Verification

34 automated tests passed. Your complete supplied 10-act export also ran through
the real HTTP client/concurrent runner against a LOCAL SIMULATED Qwen endpoint:
10/10 reached it, three injected truncations retried, 20 calls, no runner failures.
Simulation deliberately returns review placeholders. Those are NOT real Qwen
classifications. Company endpoint and classification accuracy were not tested here.

Reports: verification/TEST_RESULTS.txt and verification/offline_verification.json.
To reproduce only the local transport test, without contacting your endpoint:

```powershell
.\.venv\Scripts\python.exe .\tools\verify_offline_sample.py --input .\data\inspection\eurlex_structured_10.json --out .\runs\OFFLINE_SIMULATION_v5
```
